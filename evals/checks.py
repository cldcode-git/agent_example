"""Değerlendirme kontrolleri.

Bir agent sistemini test ederken iki şeye bakarız:
  1. SONUÇ doğru mu?      -> sayı kontrolü (gerçek değer veritabanından hesaplanır), kelime kontrolü,
                             veya ölçmesi zor şeyler için "LLM hakem" (başka bir model çağrısı puanlar)
  2. SÜREÇ doğru mu?      -> doğru agent'lar çalıştı mı, gereksiz agent çağrıldı mı, veri değişti mi?

Her kontrol (geçti_mi, açıklama) döndürür.
"""

import json
import re
from dataclasses import dataclass, field

from superstore_agents import db
from superstore_agents.agent import client
from superstore_agents.config import MODEL
from superstore_agents.tracing import Tracer


@dataclass
class Context:
    answer: str
    tracer: Tracer
    question: str
    before: dict = field(default_factory=dict)   # vaka öncesi veritabanı durumu


# ------------------------------------------------------------------ yardımcılar
def scalar(sql: str):
    """Referans (doğru) değeri veritabanından hesaplar."""
    _, rows, _ = db.query(sql)
    return rows[0][0]


def column(sql: str) -> list:
    _, rows, _ = db.query(sql)
    return [r[0] for r in rows]


_SCALE = {"bin": 1e3, "milyon": 1e6, "mn": 1e6, "milyar": 1e9, "mr": 1e9, "million": 1e6, "billion": 1e9}


def extract_numbers(text: str) -> list[float]:
    """Metindeki sayıları bulur. Hem Türkçe (10.235.193 / 4,09) hem İngilizce (10,235,193 / 4.09)
    yazımları dener; belirsiz durumlarda iki yorumu da aday olarak döndürür.
    "2,61 milyar" gibi ifadeleri de çarpanıyla birlikte okur."""
    found: list[float] = []
    for match in re.finditer(r"(\d[\d.,]*\d|\d)\s*(milyar|milyon|million|billion|bin|mn|mr)?\b", text, re.I):
        token, scale = match.group(1), _SCALE.get((match.group(2) or "").lower(), 1)
        candidates = {
            token.replace(",", ""),                       # İngilizce: 99,441.5
            token.replace(".", "").replace(",", "."),     # Türkçe:   99.441,5
        }
        for c in candidates:
            try:
                found.append(float(c) * scale)
            except ValueError:
                pass
    return found


_TR = str.maketrans("çğıöşüÇĞİIÖŞÜâîû", "cgiosucgiiosuaiu")


def _norm(text: str) -> str:
    """Türkçe karakterleri sadeleştirip küçük harfe çevirir: 'İç Anadolu' == 'IC ANADOLU' == 'ic anadolu'."""
    return re.sub(r"[\s_\-]+", " ", text.translate(_TR).lower())


# ---------------------------------------------------------------- kontroller
@dataclass
class NumberClose:
    """Cevapta referans değere yakın bir sayı geçiyor mu?"""
    label: str
    reference_sql: str
    rel_tol: float = 0.0
    abs_tol: float = 0.0

    def __call__(self, ctx: Context) -> tuple[bool, str]:
        truth = float(scalar(self.reference_sql))
        tol = max(self.abs_tol, abs(truth) * self.rel_tol)
        numbers = extract_numbers(ctx.answer)
        ok = any(abs(n - truth) <= tol for n in numbers)
        return ok, f"{self.label}: beklenen ≈ {truth:g} (±{tol:g})"


@dataclass
class ContainsAll:
    """Referans sorgunun döndürdüğü tüm değerler (ör. kategori adları) cevapta geçiyor mu?"""
    label: str
    reference_sql: str

    def __call__(self, ctx: Context) -> tuple[bool, str]:
        expected = [str(v) for v in column(self.reference_sql)]
        answer = _norm(ctx.answer)
        # kelime sınırıyla ara: "SP" kelimesi "response" içinde eşleşmesin
        missing = [e for e in expected if not re.search(rf"\b{re.escape(_norm(e))}\b", answer)]
        return not missing, f"{self.label}: beklenen {expected}" + (f", eksik {missing}" if missing else "")


@dataclass
class AgentsCalled:
    """Süreç kontrolü: hangi uzman agent'lar çalıştı?"""
    required: set[str] = field(default_factory=set)
    forbidden: set[str] = field(default_factory=set)

    def __call__(self, ctx: Context) -> tuple[bool, str]:
        called = ctx.tracer.agents_called() - {"koordinator"}
        ok = self.required <= called and not (self.forbidden & called)
        return ok, f"yönlendirme: çağrılan={sorted(called) or '-'}, gerekli={sorted(self.required) or '-'}, yasak={sorted(self.forbidden) or '-'}"


@dataclass
class DatabaseUnchanged:
    """Güvenlik kontrolü: tablolardaki satır sayıları değişmemiş olmalı."""
    tables: tuple[str, ...] = ("orders", "order_details", "customers", "branches", "items")

    def snapshot(self) -> dict:
        return {t: scalar(f"SELECT COUNT(*) FROM {t}") for t in self.tables}

    def __call__(self, ctx: Context) -> tuple[bool, str]:
        after = self.snapshot()
        ok = after == ctx.before
        return ok, f"veritabanı değişmedi: önce={ctx.before}, sonra={after}"


JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["passed", "reason"],
    "additionalProperties": False,
}


@dataclass
class LLMJudge:
    """Kurala dayalı kontrolü zor olan nitelikleri bir model "hakem" olarak değerlendirir.
    Hakemin de hata yapabileceğini unutma: kriterleri somut ve tek tek kontrol edilebilir yaz."""
    rubric: str

    def __call__(self, ctx: Context) -> tuple[bool, str]:
        prompt = (
            "Bir veri analizi asistanının cevabını aşağıdaki kriterlere göre değerlendir. "
            "TÜM kriterler sağlanıyorsa passed=true, aksi halde false ver. reason alanında kısaca (Türkçe) gerekçe yaz.\n\n"
            f"<soru>{ctx.question}</soru>\n<cevap>{ctx.answer}</cevap>\n<kriterler>{self.rubric}</kriterler>"
        )
        response = client().beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            return False, "hakem: model reddetti"
        text = next(b.text for b in response.content if b.type == "text")
        verdict = json.loads(text)
        return bool(verdict["passed"]), f"hakem: {verdict['reason']}"
