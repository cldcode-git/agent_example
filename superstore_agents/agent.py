"""Bir "agent"ın en sade hali: model + sistem talimatı + araçlar + döngü.

Agent döngüsü (agentic loop) şöyle çalışır:

    1. Modele görevi ve kullanabileceği araçların listesini gönder.
    2. Model ya cevap verir (bitti) ya da "şu aracı şu girdiyle çağır" der (tool_use).
    3. Aracı BİZ çalıştırırız (SQL sorgusu, başka bir agent'a soru...) ve sonucu modele geri yollarız.
    4. Model sonuca bakıp yeni bir araç çağırır ya da cevabı yazar. 2. adıma dön.

Modelin kendisi hiçbir şey çalıştırmaz; sadece ne yapılacağına karar verir.
Kararları uygulayan ve sınırları koyan (hangi araçlar var, kaç tur dönülebilir) bizim kodumuzdur.

Bu dosyada döngüyü bilerek elle yazdık (SDK'nın hazır tool_runner yardımcısı yerine)
ki her adım açıkça görülsün.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import anthropic

from .config import MODEL
from .tracing import Tracer

_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # ANTHROPIC_API_KEY ortam değişkenini okur
    return _client


@dataclass
class Tool:
    """Agent'ın çağırabileceği bir araç: adı, açıklaması, girdi şeması ve çalışan Python fonksiyonu."""
    name: str
    description: str
    input_schema: dict
    func: Callable[..., str]

    def to_api(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "strict": True,  # model girdileri şemaya birebir uyar
        }


@dataclass
class AgentResult:
    text: str
    turns: int
    usage: dict
    stop_reason: str


@dataclass
class Agent:
    name: str
    system: str
    tools: list[Tool]
    tracer: Tracer
    effort: str = "medium"   # low | medium | high | xhigh | max - ne kadar derin düşüneceği
    max_turns: int = 15      # sonsuz döngüye karşı emniyet kemeri
    depth: int = 0           # ekrana basarken girinti (alt agent'lar içeride görünür)
    _tool_map: dict = field(init=False, repr=False)

    def __post_init__(self):
        self._tool_map = {t.name: t for t in self.tools}

    def run(self, task: str) -> AgentResult:
        self.tracer.log(self.name, "start", self.depth, task=task)
        messages: list[dict] = [{"role": "user", "content": task}]
        usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

        for turn in range(1, self.max_turns + 1):
            response = client().beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=self.system,
                tools=[t.to_api() for t in self.tools],
                messages=messages,
                thinking={"type": "adaptive", "display": "summarized"},  # düşünce özetlerini görmek için
                output_config={"effort": self.effort},
                cache_control={"type": "ephemeral"},  # tekrar gönderilen geçmişi önbellekle (ucuz + hızlı)
                # Model güvenlik nedeniyle reddederse sunucu tarafında önerilen modele düşer
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            self._add_usage(usage, response.usage)

            if response.stop_reason == "refusal":
                category = getattr(response.stop_details, "category", None)
                self.tracer.log(self.name, "refusal", self.depth, category=category)
                return self._finish("Model bu isteği güvenlik nedeniyle yanıtlamadı.", turn, usage, "refusal")

            for block in response.content:
                if block.type == "thinking" and block.thinking:
                    self.tracer.log(self.name, "thinking", self.depth, text=block.thinking)

            # Modelin cevabını (düşünce blokları dahil) HİÇ DEĞİŞTİRMEDEN geçmişe ekle
            messages.append({"role": "assistant", "content": response.content})

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                text = "\n".join(b.text for b in response.content if b.type == "text").strip()
                if response.stop_reason == "max_tokens":
                    text += "\n\n[Uyarı: cevap uzunluk sınırında kesildi]"
                return self._finish(text, turn, usage, response.stop_reason)

            # Model aynı anda birden fazla araç isteyebilir -> paralel çalıştır.
            # (Örn. orkestratör iki uzman agent'a aynı anda soru sorabilir.)
            with ThreadPoolExecutor(max_workers=len(tool_uses)) as pool:
                results = list(pool.map(self._execute, tool_uses))
            # Tüm sonuçlar TEK bir user mesajında geri gider
            messages.append({"role": "user", "content": results})

        return self._finish(
            f"[{self.max_turns} tur sınırına ulaşıldı, görev tamamlanamadı]", self.max_turns, usage, "max_turns"
        )

    def _execute(self, block) -> dict:
        self.tracer.log(self.name, "tool_call", self.depth, tool=block.name, input=block.input)
        tool = self._tool_map.get(block.name)
        try:
            if tool is None:
                raise ValueError(f"Bilinmeyen araç: {block.name}")
            output, is_error = tool.func(**block.input), False
        except Exception as exc:  # hatayı modele geri bildir; model düzeltip tekrar deneyebilir
            output, is_error = f"HATA: {exc}", True
        if not isinstance(output, str):
            output = json.dumps(output, ensure_ascii=False, default=str)
        self.tracer.log(self.name, "tool_result", self.depth, tool=block.name, output=output, is_error=is_error)
        return {"type": "tool_result", "tool_use_id": block.id, "content": output, "is_error": is_error}

    @staticmethod
    def _add_usage(total: dict, u) -> None:
        total["input"] += u.input_tokens or 0
        total["output"] += u.output_tokens or 0
        total["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        total["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0

    def _finish(self, text: str, turns: int, usage: dict, stop_reason: str) -> AgentResult:
        self.tracer.log(self.name, "end", self.depth, turns=turns, usage=usage, text=text, stop_reason=stop_reason)
        return AgentResult(text=text, turns=turns, usage=usage, stop_reason=stop_reason)
