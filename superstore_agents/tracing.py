"""Agent'ların ne yaptığını kaydeden ve ekrana basan basit bir izleyici (tracer).

Agent sistemlerini anlamanın en iyi yolu "içeride ne oldu?" sorusunu görebilmektir:
hangi agent çalıştı, hangi aracı hangi girdiyle çağırdı, ne sonuç aldı, kaç token harcadı.
Değerlendirme (eval) scripti de bu kayıtları kullanarak örneğin "doğru agent'a mı
yönlendirildi?" sorusunu kontrol eder.
"""

import threading
import time
from dataclasses import dataclass, field

COLORS = {
    "koordinator": "\033[95m",       # mor
    "satis_analisti": "\033[94m",    # mavi
    "urun_analisti": "\033[92m",     # yeşil
    "musteri_analisti": "\033[93m",  # sarı
}
DIM, RESET = "\033[2m", "\033[0m"


@dataclass
class Tracer:
    verbose: bool = True
    events: list[dict] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def log(self, agent: str, kind: str, depth: int = 0, **data) -> None:
        event = {"t": round(time.time(), 3), "agent": agent, "kind": kind, **data}
        with self._lock:
            self.events.append(event)
            if self.verbose:
                self._print(agent, kind, depth, data)

    def _print(self, agent: str, kind: str, depth: int, data: dict) -> None:
        color = COLORS.get(agent, "")
        prefix = "    " * depth + f"{color}[{agent}]{RESET} "
        if kind == "start":
            print(f"{prefix}▶ görev: {_short(data['task'], 160)}")
        elif kind == "thinking":
            print(f"{prefix}{DIM}💭 {_short(data['text'], 220)}{RESET}")
        elif kind == "tool_call":
            print(f"{prefix}🔧 {data['tool']}({_short(_fmt_input(data['input']), 220)})")
        elif kind == "tool_result":
            mark = "❌" if data.get("is_error") else "↩"
            print(f"{prefix}{DIM}{mark} {_short(data['output'], 220)}{RESET}")
        elif kind == "end":
            u = data["usage"]
            print(f"{prefix}■ bitti ({data['turns']} tur, {u['input']:,} girdi / {u['output']:,} çıktı token)")
        elif kind == "refusal":
            print(f"{prefix}⚠ model isteği reddetti: {data.get('category')}")

    # --- değerlendirme için yardımcılar ---
    def agents_called(self) -> set[str]:
        return {e["agent"] for e in self.events if e["kind"] == "start"}

    def tool_calls(self, agent: str | None = None, tool: str | None = None) -> list[dict]:
        return [
            e for e in self.events
            if e["kind"] == "tool_call"
            and (agent is None or e["agent"] == agent)
            and (tool is None or e["tool"] == tool)
        ]

    def total_usage(self) -> dict:
        total = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        for e in self.events:
            if e["kind"] == "end":
                for k in total:
                    total[k] += e["usage"].get(k, 0)
        return total


def _fmt_input(data: dict) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in data.items())


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + "…"
