# 3A Superstore Multi-Agent Analiz Sistemi

[3A Superstore veri seti](https://www.kaggle.com/datasets/cemeraan/3a-superstore) (Türkiye'de 161 şubeli bir
süpermarket zinciri, 2021-01 → 2023-08, sentetik veri) üzerinde çalışan, **birden fazla agent'ın birlikte
çalıştığı** bir soru-cevap sistemi. Amaç agent kavramlarını çalışan ve okunabilir bir örnek üzerinden öğrenmek.
Veri Türkçe olduğu için çeviri gerekmez.

```bash
.venv/bin/python ask.py "2021'den 2022'ye ciro neden arttı, hangi kategorilerde fiyatlar en çok yükseldi?"
```

---

## 1. Kavramlar

| Kavram | Bu projede karşılığı |
|---|---|
| **LLM çağrısı** | Modele metin gönderip metin almak. Model tek başına veritabanına erişemez. |
| **Araç (tool)** | Modelin "bunu çalıştır" diyebileceği bir fonksiyon. Model sadece *ister*; fonksiyonu **bizim kodumuz** çalıştırır. Örn. `run_sql`. |
| **Agent** | Model + sistem talimatı + araçlar + **döngü**. Görev bitene kadar: düşün → araç çağır → sonucu oku → tekrar. ([agent.py](superstore_agents/agent.py)) |
| **Multi-agent** | Birden fazla agent'ın iş bölümü yapması. Burada bir **koordinatör** soruyu parçalayıp **üç uzman** agent'a dağıtıyor. ([team.py](superstore_agents/team.py)) |
| **Eval (değerlendirme)** | Sistemin doğru çalışıp çalışmadığını ölçen, tekrar çalıştırılabilir test vakaları. ([evals/](evals/)) |

### Agent döngüsü

```
  görev ──► [model] ──► cevap mı?  ── evet ──► bitti
              ▲            │
              │          hayır: "run_sql(SELECT ...)" çalıştır
              │            ▼
              └──── [bizim kodumuz aracı çalıştırır, sonucu geri yollar]
```

Model hiçbir şeyi kendisi çalıştırmaz, sadece **karar verir**. Hangi araçların var olduğu, kaç tur dönülebileceği
ve hangi tablolara erişilebileceği **bizim kodumuzun** koyduğu sınırlardır.

### Ekip

```
                         ┌───────────────────────┐
          kullanıcı ───► │      koordinator      │  soruyu parçalar, uzmanlara dağıtır,
                         │     (ekip lideri)     │  sonuçları birleştirip cevabı yazar
                         └──┬────────┬────────┬──┘
     satis_analistine_sor   │        │        │   musteri_analistine_sor
                ┌───────────┘        │        └───────────┐
                ▼        urun_analistine_sor              ▼
      ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
      │  satis_analisti  │  │  urun_analisti   │  │ musteri_analisti │
      │ ciro, trend,     │  │ kategori, marka, │  │ demografi,       │
      │ şube/bölge/il    │  │ ürün, fiyat      │  │ sadakat, segment │
      └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
               ▼                     ▼                     ▼
          kendi tabloları       kendi tabloları       kendi tabloları
                          PostgreSQL (salt-okunur)
```

| Agent | Görevi | Erişebildiği tablolar |
|---|---|---|
| **koordinator** | Planlar, dağıtır, birleştirir. Veriye hiç dokunmaz. | yok (araçları diğer agent'lar) |
| **satis_analisti** | Ciro, sipariş sayısı, sepet, trend, şube/il/bölge | orders, branches, branch_service_towns, daily_branch_sales |
| **urun_analisti** | Kategori, marka, ürün, adet, fiyat | items, order_details, monthly_item_sales, orders, branches |
| **musteri_analisti** | Cinsiyet, yaş, il, sadakat, segment | customers, customer_summary, orders, branches, order_details, items |

Önemli noktalar:
- **Koordinatörün araçları başka agent'lardır.** `satis_analistine_sor` çağrıldığında kod satış analistini kendi
  döngüsüyle çalıştırır ve onun son cevabını koordinatöre "araç sonucu" olarak verir.
- **Paralel ve sıralı delegasyon:** Bağımsız alt sorular aynı anda gönderilir (kod bunları paralel çalıştırır).
  Bir adım diğerinin sonucuna bağlıysa (ör. "en iyi ay hangisi → o ay hangi kategori?") sırayla gönderilir.
- **Her uzmanın temiz bir geçmişi var.** Uzman, koordinatörün konuşmasını görmez; koordinatör görevi kendi başına
  anlaşılır yazmak zorundadır.
- **En az yetki:** Satış analisti `customers` tablosunu sorgulamaya çalışırsa araç hata döndürür ve agent bunu görür.
  Böylece her agent sadece işinin gerektirdiği veriye erişir.
- **Neden ayrı agent'lar?** Her biri dar bir işe odaklanır: daha net talimat, daha az araç, daha kısa bağlam.
  Uzmanların okuduğu ham sorgu çıktıları koordinatörün bağlamını doldurmaz; koordinatör sadece özetleri görür.

### Güvenlik katmanları
1. `run_sql` sorguyu çalıştırmadan önce kontrol eder: tek bir `SELECT`/`WITH`, yasaklı komut yok ([db.py](superstore_agents/db.py)).
2. Her agent sadece kendi tablo listesine erişebilir (`check_tables`).
3. Veritabanı kullanıcısı `superstore_reader` yalnızca SELECT yetkisine sahip, oturum salt-okunur, sorgular 60 sn'de kesilir.
4. Her agent en fazla 15 tur dönebilir (sonsuz döngüye karşı).
5. Kişisel veriler (ad-soyad, e-posta, açık adres) veritabanına hiç yüklenmez.

---

## 2. Veritabanı

| Tablo | Satır | İçerik |
|---|---:|---|
| branches | 161 | şube, bölge, il, ilçe, koordinat |
| branch_service_towns | 957 | şubenin hizmet verdiği ilçeler |
| items | 27,000 | ürün kataloğu: 4 seviyeli kategori, marka |
| customers | 99,998 | cinsiyet (E/K), doğum tarihi, il/bölge |
| orders | 10,235,193 | sipariş (fiş): tarih, şube, müşteri, sepet tutarı |
| order_details | 51,185,032 | sipariş kalemleri: ürün, adet, fiyat, tutar |
| **daily_branch_sales** | 153,911 | *özet:* gün × şube sipariş sayısı ve ciro |
| **monthly_item_sales** | 864,000 | *özet:* ay × ürün adet ve ciro |
| **customer_summary** | 99,996 | *özet:* müşteri başına sipariş, harcama, ilk/son alışveriş |

**Özet tablolar neden var?** `order_details` 51 milyon satır. Agent'ın her soruda bu tabloyu taraması 10-60 saniye
sürer. Özet tablolar (materialized view) bu toplamları önceden hesaplar, sorgular milisaniyeler içinde biter.
Agent'lara tablo açıklamalarında "önce özet tabloları kullan" yazıyor. Testler, özet tabloların büyük tablolarla
aynı toplamları verdiğini doğrular.

Yükleme sırasında bulunan veri özellikleri (agent'lar bunları tablo açıklamalarından okur):
- Ondalık ayırıcı virgül (`"12,5"`): yüklerken noktaya çevrildi.
- `Branches.csv` her satırda *(şube, hizmet ilçesi)* çifti tutuyor: iki tabloya ayrıldı.
- `amount × unit_price` ≠ `total_price` (indirim/fiyat farkı): ciro için `total_price` kullanılmalı.
- 2023 sadece 8 ay, Ağustos yarım: yıllık karşılaştırmalarda dikkat.

---

## 3. Kurulum

Gereksinimler: Docker Desktop, Python 3.12 (`.venv` hazır), Anthropic API anahtarı.

```bash
cd ~/Github/superstore-multi-agent
```

**a) API anahtarı:** `.env` dosyasında `ANTHROPIC_API_KEY=` satırına anahtarını ekle (https://console.anthropic.com → API Keys).

**b) PostgreSQL** (5433 portunda; Olist projesi 5432'de):

```bash
docker compose up -d
```

> `docker: command not found` alırsan: Docker Desktop → Settings → Advanced → komut satırı araçlarını sisteme kur,
> ya da `export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"`

**c) Veriyi indir ve yükle** (zaten yapıldı; sıfırdan kurulumda ~3 GB indirme + ~2 dk yükleme):

```bash
.venv/bin/python scripts/download_data.py
```

```bash
.venv/bin/python scripts/load_data.py
```

Veritabanına kendin bakmak istersen:

```bash
docker exec -it superstore-postgres psql -U superstore_admin -d superstore
```

---

## 4. Kullanım

```bash
.venv/bin/python ask.py "2022'de en çok ciro yapan 5 şube hangileri?"
```

Ekranda her agent'ın adımlarını renkli ve girintili görürsün:

```
[koordinator] ▶ görev: ...
[koordinator] 🔧 satis_analistine_sor(task='...')
[koordinator] 🔧 urun_analistine_sor(task='...')          ← iki uzman aynı anda
    [satis_analisti] ▶ görev: ...
    [urun_analisti] ▶ görev: ...
    [satis_analisti] 💭 (düşünce özeti)
    [satis_analisti] 🔧 run_sql(sql='SELECT ... FROM daily_branch_sales ...')
    [satis_analisti] ↩ year | revenue ...
    [satis_analisti] ■ bitti (3 tur, ...)
    ...
[koordinator] ■ bitti
```

Seçenekler: `--quiet` (sadece cevap), `--effort high` (daha derin düşünme), argümansız çalıştırınca etkileşimli mod.

Denemek için soru fikirleri:
- "Hangi ilde şube başına ciro en yüksek?"
- "Kadın ve erkek müşterilerin en çok harcama yaptığı 3 kategori farklı mı?" (müşteri + ürün)
- "Hafta sonu ile hafta içi ortalama sepet tutarı farklı mı?"
- "En sadık müşteriler (en çok sipariş verenler) hangi bölgede yaşıyor ve ortalama yaşları kaç?"

---

## 5. Test ve değerlendirme

### a) Birim testleri (API anahtarı gerekmez)

```bash
.venv/bin/python -m pytest
```

- `test_offline.py`: SQL güvenlik kontrolü, **agent tablo yetkileri**, sayı ayrıştırma ("2,61 milyar"), Türkçe karakter karşılaştırma
- `test_database.py`: satır sayıları, özet tabloların doğruluğu, ondalık/koordinat dönüşümü, salt-okunur kullanıcı, araç yetkileri
- `test_agent_loop.py`: **sahte bir modelle** agent döngüsü (paralel delegasyon, yetki hatasının modele dönmesi, tur sınırı).
  Döngünün nasıl çalıştığını anlamak için okunacak en iyi dosya.

### b) Agent değerlendirmesi (eval): gerçek modelle

```bash
.venv/bin/python -m evals.run_evals --case 01
```

Tümü için `--case` olmadan çalıştır; adımları izlemek için `--verbose` ekle.

| Vaka | Neyi ölçüyor | Kontrol |
|---|---|---|
| 01_yillik_ciro | Basit toplam, özet tablo | 2022 sipariş sayısı ve ciro + satış uzmanına gitti mi |
| 02_bolgeler | JOIN + sıralama, Türkçe karakterler | İlk 3 bölge |
| 03_kategoriler | Kategori hiyerarşisi | İlk 3 ana kategori + ürün uzmanına gitti mi |
| 04_cinsiyet_sepet | Demografi, E/K kodu | İki ortalama (±0,1 TL) + müşteri uzmanına gitti mi |
| 05_ay_sonra_kategori | **Sıralı** çok adım | En iyi ay + o ayın lider kategorisi |
| 06_buyume_nedeni | **Paralel** çok-agent + yorum | Büyüme %, fiyatı en çok artan kategoriler, iki uzman da çağrıldı mı, LLM hakem |
| 07_yas_segmentleri | Nitel analiz | LLM hakem (rubrik) |
| 08_guvenlik_silme | Veri silme talebi | Satır sayıları değişmedi mi + LLM hakem |
| 09_kapsam_disi | Konu dışı soru | Hiçbir uzman çağrılmadı mı + LLM hakem |

Üç tür kontrol var ([checks.py](evals/checks.py)):
- **Sonuç kontrolü:** Doğru cevap elle yazılmaz, her çalıştırmada **referans SQL** ile veritabanından hesaplanır.
- **Süreç kontrolü:** İzleme kaydından hangi agent'ların çalıştığına bakılır. Doğru cevap ama yanlış yol
  (ör. maç sorusuna satış uzmanını çağırmak) da hata sayılır.
- **LLM hakem:** Kurala dökülmesi zor nitelikler için başka bir model çağrısı somut kriterlere göre geçti/kaldı verir.

Her çalıştırma `evals/results/` altına cevapları, kontrolleri ve tüm agent izlerini içeren bir JSON yazar.

### c) API anahtarı olmadan manuel test

Model yerine bir insan ya da Claude Code alt-agent'ı uzman rolünü oynayabilir. Aynı araçları aynı tablo yetkileriyle çağırmak için:

```bash
.venv/bin/python -m superstore_agents.cli --agent satis_analisti --trace evals/manual/01_yillik_ciro.jsonl run_sql "SELECT ..."
```

Koordinatör cevabı `evals/manual/<case>.md` dosyasına yazılır. Ardından puanlama yapılır:

```bash
.venv/bin/python -m evals.score_manual
```

Yapılmış bir manuel çalıştırmanın sonuçları: [evals/MANUEL_TEST_RAPORU.md](evals/MANUEL_TEST_RAPORU.md).

> **Maliyet:** Tüm agent'lar ve hakem `claude-opus-5-5` kullanır (`.env` içindeki `CLAUDE_MODEL` ile değiştirilebilir).
> Tam eval onlarca model çağrısı yapar; sonunda toplam token kullanımı yazdırılır. İlk denemeyi tek vakayla yap.

---

## 6. Kendi başına deneyebileceklerin

1. **Prompt'u boz, eval'e bak:** `COMMON_RULES` içinden "özet tabloları kullan" satırını sil, `--case 03 --verbose`
   çalıştır. Agent 51M satırlık tabloya gidip yavaşlıyor mu, zaman aşımına mı uğruyor?
2. **Yetkiyi değiştir:** `URUN_TABLES` içinden `monthly_item_sales`'ı çıkar. Agent alternatif yol buluyor mu?
3. **Yeni uzman ekle:** ör. `lojistik_analisti` (`branch_service_towns`, şube koordinatları). [team.py](superstore_agents/team.py)'de
   bir `Agent` tanımla, tablo izinlerini ver, koordinatöre `lojistik_analistine_sor` aracı olarak ekle.
4. **Doğrulayıcı (reviewer) agent:** Koordinatör son cevabı yazmadan önce sayıları bağımsız SQL ile kontrol eden
   bir agent ekle. Çok-agent sistemlerde yaygın bir kalıptır.
5. **effort karşılaştır:** `--effort low` ve `--effort high` arasındaki başarı oranı, süre ve token farkına bak.

## Proje yapısı

```
docker-compose.yml          PostgreSQL 16 (5433 portu)
db/init/                    şema (tablo açıklamalarıyla) + salt-okunur kullanıcı
scripts/                    veri indirme ve yükleme (özet tablolar dahil)
superstore_agents/
  agent.py                  agent döngüsü (çekirdek)
  team.py                   koordinatör + 3 uzman, talimatlar ve tablo yetkileri
  tools.py                  veritabanı araçları (agent başına yetkili)
  db.py                     salt-okunur sorgu katmanı, SQL ve yetki kontrolü
  tracing.py                adımları kaydetme/gösterme
ask.py                      komut satırı arayüzü
evals/                      değerlendirme vakaları ve çalıştırıcı
tests/                      birim testleri
```
