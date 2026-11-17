"""Agent döngüsünü SAHTE bir modelle test eder (API anahtarı ve veritabanı gerekmez).

Gerçek model yerine, önceden yazılmış cevapları sırayla döndüren bir sahte istemci kullanıyoruz.
Bu testler agent döngüsünün mantığını adım adım gösterir:
  - koordinatör iki uzmanı AYNI ANDA çağırır (paralel tool_use), sonra cevapları birleştirir
  - bir uzmanın aracı hata verirse hata modele geri bildirilir, model düzeltebilir
  - model durmadan araç çağırırsa tur sınırı devreye girer
"""

import threading
from types import SimpleNamespace as NS

import pytest

from superstore_agents import agent as agent_module
from superstore_agents.team import build_team
from superstore_agents.tracing import Tracer


def _usage():
    return NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0)


def _text(text):
    return NS(content=[NS(type="text", text=text)], stop_reason="end_turn", usage=_usage(), stop_details=None)


def _tool_calls(*calls):
    blocks = [NS(type="tool_use", id=f"tu_{i}", name=name, input=inp) for i, (name, inp) in enumerate(calls)]
    return NS(content=blocks, stop_reason="tool_use", usage=_usage(), stop_details=None)


class FakeClient:
    """Sistem talimatına bakarak hangi agent'ın konuştuğunu anlar ve senaryodaki sıradaki cevabı verir."""

    def __init__(self, scripts):
        self.scripts = scripts
        self.requests = []
        self._lock = threading.Lock()
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kwargs):
        system = kwargs["system"]
        who = ("koordinator" if "KOORDİNATÖR" in system
               else "satis_analisti" if "SATIŞ ANALİSTİSİN" in system
               else "urun_analisti" if "ÜRÜN VE KATEGORİ" in system
               else "musteri_analisti")
        with self._lock:
            # agent aynı listeyi sonradan büyüttüğü için istek anındaki halinin kopyasını sakla
            self.requests.append((who, {**kwargs, "messages": list(kwargs["messages"])}))
            return self.scripts[who].pop(0)


@pytest.fixture
def fake(monkeypatch):
    def install(scripts):
        client = FakeClient(scripts)
        monkeypatch.setattr(agent_module, "_client", client)
        return client
    return install


def test_coordinator_delegates_in_parallel_and_combines(fake):
    client = fake({
        "koordinator": [
            _tool_calls(("satis_analistine_sor", {"task": "2022 toplam ciro?"}),
                        ("musteri_analistine_sor", {"task": "Kadın/erkek müşteri oranı?"})),
            _text("2022 cirosu 2,61 milyar TL; müşterilerin %55'i kadın."),
        ],
        "satis_analisti": [_text("2022 cirosu 2,61 milyar TL")],
        "musteri_analisti": [_text("%55 kadın, %45 erkek")],
        "urun_analisti": [],
    })
    tracer = Tracer(verbose=False)
    result = build_team(tracer).run("2022 cirosu ve müşteri cinsiyet dağılımı?")

    assert "2,61 milyar" in result.text
    assert tracer.agents_called() == {"koordinator", "satis_analisti", "musteri_analisti"}

    # Koordinatörün 2. isteğinde iki tool_result TEK user mesajında ve doğru id'lerle gitmeli
    coord_requests = [kw for who, kw in client.requests if who == "koordinator"]
    last_user = coord_requests[1]["messages"][-1]
    assert last_user["role"] == "user"
    assert [r["tool_use_id"] for r in last_user["content"]] == ["tu_0", "tu_1"]
    assert last_user["content"][0]["content"] == "2022 cirosu 2,61 milyar TL"

    # Uzmanlar koordinatörün geçmişini görmez: sadece kendi görevleri ile başlar
    satis_req = next(kw for who, kw in client.requests if who == "satis_analisti")
    assert satis_req["messages"] == [{"role": "user", "content": "2022 toplam ciro?"}]


def test_permission_error_is_reported_back_to_model(fake):
    """Satış analisti yetkisi olmayan tabloya sorgu yazarsa hata modele is_error=True olarak döner.
    (Bu test veritabanına bağlanmaz: kontrol sorgu çalışmadan ÖNCE yapılır.)"""
    client = fake({
        "koordinator": [_tool_calls(("satis_analistine_sor", {"task": "kadın müşteri sayısı"})), _text("...")],
        "satis_analisti": [
            _tool_calls(("run_sql", {"sql": "SELECT COUNT(*) FROM customers WHERE gender = 'K'"})),
            _text("Müşteri tablosuna erişimim yok."),
        ],
        "urun_analisti": [], "musteri_analisti": [],
    })
    build_team(Tracer(verbose=False)).run("Kaç kadın müşteri var?")

    satis_requests = [kw for who, kw in client.requests if who == "satis_analisti"]
    tool_result = satis_requests[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] is True
    assert "erişim yetkisi yok" in tool_result["content"]


def test_max_turns_guard(fake):
    fake({
        "koordinator": [_tool_calls(("urun_analistine_sor", {"task": "x"})) for _ in range(20)],
        "urun_analisti": [_text("ok") for _ in range(20)],
        "satis_analisti": [], "musteri_analisti": [],
    })
    coordinator = build_team(Tracer(verbose=False))
    coordinator.max_turns = 3
    assert coordinator.run("sonsuz döngü").stop_reason == "max_turns"
