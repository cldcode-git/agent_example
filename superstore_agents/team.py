r"""Çok-agent'lı ekip: 1 koordinatör + 3 uzman.

                         ┌───────────────────────┐
          kullanıcı ───► │      koordinator      │  soruyu parçalar, uzmanlara dağıtır,
                         │     (ekip lideri)     │  sonuçları birleştirip cevabı yazar
                         └──┬────────┬────────┬──┘
     satis_analistine_sor   │        │        │   musteri_analistine_sor
                ┌───────────┘        │        └───────────┐
                ▼        urun_analistine_sor              ▼
      ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
      │  satis_analisti  │  │  urun_analisti   │  │ musteri_analisti │
      │ ciro, trend,     │  │ kategori, marka, │  │ demografi, sadakat│
      │ şube/bölge/il    │  │ ürün, fiyat      │  │ segment, sepet   │
      └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
               ▼                     ▼                     ▼
       orders, branches,     items, order_details,  customers, customer_summary,
       branch_service_towns, monthly_item_sales,    orders, branches, order_details,
       daily_branch_sales    orders, branches       items
                         \           |           /
                          PostgreSQL (salt-okunur)

Önemli fikirler:
  * Koordinatörün araçları aslında BAŞKA AGENT'LARDIR. "satis_analistine_sor" çağrıldığında
    kodumuz satış analisti agent'ını kendi döngüsüyle çalıştırır ve cevabını koordinatöre döndürür.
  * Her uzmanın kendi talimatı, kendi araç seti, kendi tablo izinleri ve temiz bir konuşma geçmişi var.
  * Koordinatör veriye hiç dokunmaz; sadece planlar, dağıtır ve birleştirir.
"""

from .agent import Agent, AgentResult, Tool
from .tools import make_db_tools
from .tracing import Tracer

COMMON_RULES = """
Veri hakkında bilmen gerekenler:
- Türkiye'de 161 şubeli bir süpermarket zinciri (sentetik veri). Tutarlar TL.
- Veri 2021-01-01 ile 2023-08 arasını kapsar; 2021 ve 2022 tam yıl, 2023 sadece 8 ay ve Ağustos yarımdır.
  Yıllık karşılaştırmalarda bunu dikkate al (ör. 2023'ü 2022'nin aynı dönemiyle karşılaştır).
- orders (~10M satır) ve order_details (~51M satır) büyüktür. Mümkün olduğunda ÖZET TABLOLARI kullan;
  büyük tablolara doğrudan sorgu 10-60 sn sürebilir ve 60 sn'de iptal edilir.
- Metin kolonlarındaki yazımlar farklı olabilir (kategoriler Türkçe karakter olmadan BÜYÜK HARF: 'SEKERLEME';
  iller Türkçe karakterli: 'İstanbul'). Emin değilsen önce DISTINCT ile değerlere bak.

Çalışma şeklin:
- Tabloları/kolonları tahmin etme: emin değilsen list_tables ve describe_table ile incele.
- Hesaplamayı veritabanına yaptır (SUM, COUNT, AVG, GROUP BY). Sorgu hata verirse oku, düzelt, tekrar dene.
- Erişimin olmayan bir veri gerekiyorsa uydurma; cevabında "bu kısım için X verisine erişimim yok" de.

Cevabın koordinatöre gidecek; kısa ve net yaz:
1. Sonuç (sayılarla)
2. Kullandığın tanımlar/varsayımlar
3. Sonucu üreten SQL sorgu(lar)ı
Veritabanını değiştirme talepleri yerine getirilemez; veritabanı salt-okunurdur.
"""

SATIS_SYSTEM = (
    "Sen bir süpermarket zincirinin SATIŞ ANALİSTİSİN. Uzmanlık alanın: ciro, sipariş (fiş) sayısı, "
    "ortalama sepet tutarı, zaman içindeki trendler ve mevsimsellik, şube/il/bölge performansı."
    + COMMON_RULES
)

URUN_SYSTEM = (
    "Sen bir süpermarket zincirinin ÜRÜN VE KATEGORİ ANALİSTİSİN. Uzmanlık alanın: kategoriler "
    "(category1 > category2 > category3 > category4), markalar, ürünler, satış adetleri, ürün/kategori cirosu, "
    "birim fiyatlar, sepette birlikte alınan ürünler."
    + COMMON_RULES
)

MUSTERI_SYSTEM = (
    "Sen bir süpermarket zincirinin MÜŞTERİ ANALİSTİSİN. Uzmanlık alanın: müşteri demografisi (cinsiyet, yaş, "
    "yaşadığı il/bölge), alışveriş sıklığı, harcama, sadakat, müşteri segmentasyonu (ör. RFM), "
    "müşteri gruplarının tercihleri."
    + COMMON_RULES
)

KOORDINATOR_SYSTEM = """\
Sen bir veri analizi ekibinin lideri olan KOORDİNATÖR agent'sın. Kullanıcı, Türkiye'de 161 şubeli bir süpermarket
zincirinin (3A Superstore, 2021-01 ile 2023-08 arası) verisi hakkında sorular sorar.
Veriye kendin erişemezsin; işi ekibindeki üç uzmana dağıtırsın:

- satis_analistine_sor: ciro, sipariş sayısı, ortalama sepet, zaman trendleri, şube/il/bölge performansı.
- urun_analistine_sor: kategori, marka, ürün, satış adedi, fiyat, birlikte alınan ürünler.
- musteri_analistine_sor: müşteri demografisi (cinsiyet, yaş, il), sadakat, segmentasyon, müşteri gruplarının tercihleri.

Kurallar:
- Soruyu parçalara ayır ve her parçayı doğru uzmana gönder. Tek bir uzmanın cevaplayabileceği soruyu bölme.
- Birbirinden bağımsız parçaları AYNI ANDA (tek seferde birden fazla araç çağrısıyla) gönder. Bir parça diğerinin
  sonucuna ihtiyaç duyuyorsa (ör. "en çok satan ayda hangi kategori öne çıktı?") önce ilkini sor, sonucu
  ikinci görevin içine yaz.
- Uzmanlar senin konuşma geçmişini görmez: her görevi kendi başına anlaşılır, net ve eksiksiz yaz
  (dönem, tanım, istenen çıktı).
- Uzmanların verdiği sayıları değiştirme veya uydurma. Çelişki veya eksik görürsen uzmana tekrar sor.
- Soru bu süpermarketin verisiyle ilgili değilse (hava durumu, genel sohbet vb.) uzmanları çağırma;
  kibarca kapsam dışı olduğunu söyle.
- Veritabanını değiştirme/silme taleplerini reddet: sistem salt-okunurdur. Uzmanlara böyle bir görev gönderme.

Son cevabı kullanıcıya Türkçe, kısa ve okunaklı yaz: önce doğrudan cevap (anahtar sayılarla), sonra gerekiyorsa
kısa açıklama ve yorum. Sayıları uzmanın verdiği hassasiyetle aktar.
"""

# Her uzmanın erişebildiği tablolar ("en az yetki" prensibi)
SATIS_TABLES = frozenset({"orders", "branches", "branch_service_towns", "daily_branch_sales"})
URUN_TABLES = frozenset({"items", "order_details", "monthly_item_sales", "orders", "branches"})
MUSTERI_TABLES = frozenset({"customers", "customer_summary", "orders", "branches", "order_details", "items"})


def build_team(tracer: Tracer, effort: str = "medium") -> Agent:
    """Koordinatörü ve uzmanları kurar, koordinatörü döndürür."""
    specialists = {
        "satis_analisti": (SATIS_SYSTEM, SATIS_TABLES),
        "urun_analisti": (URUN_SYSTEM, URUN_TABLES),
        "musteri_analisti": (MUSTERI_SYSTEM, MUSTERI_TABLES),
    }
    agents = {
        name: Agent(name=name, system=system, tools=make_db_tools(tables), tracer=tracer, effort=effort, depth=1)
        for name, (system, tables) in specialists.items()
    }

    def delegate(agent: Agent):
        def _call(task: str) -> str:
            result: AgentResult = agent.run(task)
            return result.text or "(uzman boş cevap döndürdü)"
        return _call

    task_schema = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "Uzmana verilecek, kendi başına anlaşılır görev tanımı"}
        },
        "required": ["task"],
        "additionalProperties": False,
    }

    delegation_tools = [
        Tool("satis_analistine_sor",
             "Satış analisti agent'ına ciro/sipariş/trend/şube-bölge görevi verir ve sonucunu döndürür.",
             task_schema, delegate(agents["satis_analisti"])),
        Tool("urun_analistine_sor",
             "Ürün ve kategori analisti agent'ına kategori/marka/ürün görevi verir ve sonucunu döndürür.",
             task_schema, delegate(agents["urun_analisti"])),
        Tool("musteri_analistine_sor",
             "Müşteri analisti agent'ına demografi/sadakat/segment görevi verir ve sonucunu döndürür.",
             task_schema, delegate(agents["musteri_analisti"])),
    ]

    return Agent(
        name="koordinator",
        system=KOORDINATOR_SYSTEM,
        tools=delegation_tools,
        tracer=tracer,
        effort=effort,
        depth=0,
    )


def ask(question: str, verbose: bool = True, effort: str = "medium") -> tuple[AgentResult, Tracer]:
    """Ekibe bir soru sorar. (son cevap, izleme kaydı) döndürür."""
    tracer = Tracer(verbose=verbose)
    return build_team(tracer, effort=effort).run(question), tracer
