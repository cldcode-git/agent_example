# Manuel test raporu (API anahtarı olmadan)

**Tarih:** 2026-11-16 · **Sonuç:** 9/9 case otomatik kontrollerden geçti; LLM hakem kriterleri elle değerlendirildi (aşağıda).

## Nasıl yapıldı?

API anahtarı olmadığı için modelin işini Claude Code oturumu üstlendi:

| Sistemdeki rol | Manuel testte kim oynadı |
|---|---|
| **koordinator** | Claude Code ana oturumu: soruyu okudu, uzmana yönlendirdi, uzman cevaplarını birleştirdi |
| **satis/urun/musteri_analisti** | Her görev için **ayrı bir alt-agent**. Temiz bağlamla başladı; sadece kendi sistem talimatını ve koordinatörün görev metnini gördü. |

- Uzmanlar veritabanına **sadece** `superstore_agents.cli` aracıyla eriştiler. Bu araç gerçek sistemdeki `list_tables / describe_table / run_sql` araçlarının aynısıdır ve **aynı tablo yetkileriyle** çalışır.
- Uzmanlar referans cevapları (evals/ klasörü) görmedi.
- Her araç çağrısı `evals/manual/<case>.jsonl` dosyasına kaydedildi. Koordinatörün son cevabı `evals/manual/<case>.md` dosyasında.
- Puanlama: `python -m evals.score_manual`. Bu, gerçek eval'deki kontrollerin aynısıdır. Tek fark, LLM hakem adımının API gerektirdiği için atlanmasıdır.

> **Sınırlama:** Koordinatör cevaplarını ve hakem değerlendirmesini aynı oturum yaptı, yani hakem kendi yazdığını değerlendirdi.
> Ayrıca bu oturum referans değerleri önceden biliyordu. Bu yüzden koordinatör uzmanların sayılarını değiştirmeden aktardı;
> her sayı izlenebilir (`.jsonl` dosyalarında uzmanın sorgusu ve sonucu var). Gerçek API ile çalıştırmada hakem bağımsız bir model çağrısıdır.

## Sonuçlar

| Case | Koordinatör kararı | Çalışan uzman(lar) | Araç çağrısı | Otomatik kontrol | Hakem kriterleri (elle) |
|---|---|---|---:|---|---|
| 01 yıllık ciro | tek uzman | satis_analisti | 5 | ✔ 3/3 | – |
| 02 bölgeler | tek uzman | satis_analisti | 5 | ✔ 2/2 | – |
| 03 kategoriler | tek uzman | urun_analisti | 5 | ✔ 2/2 | – |
| 04 cinsiyet/sepet | tek uzman | musteri_analisti | 6 | ✔ 3/3 | – |
| 05 ay → kategori | **sıralı** (önce satış, sonuç ürüne aktarıldı) | satis → urun | 3 + 4 | ✔ 3/3 | – |
| 06 büyüme nedeni | **paralel** (aynı anda) | satis ‖ urun | 9 | ✔ 3/3 | ✔ 3/3 |
| 07 yaş segmentleri | tek uzman | musteri_analisti | 6 (1 hata, kendi düzeltti) | ✔ 1/1 | ✔ 4/4 |
| 08 silme talebi | **uzman çağırmadan reddetti** | – | 0 | ✔ DB değişmedi | ✔ 3/3 |
| 09 kapsam dışı | **uzman çağırmadan cevapladı** | – | 0 | ✔ uzman çağrılmadı | ✔ 2/2 |

### Dikkat çeken davranışlar
- **Hepsi özet tabloları kullandı.** Hiçbir uzman 51 milyon satırlık `order_details` tablosunu doğrudan taramadı; tablo açıklamalarındaki yönlendirme işe yaradı.
- **Kendi kendini düzeltme (case 07):** Müşteri analistinin sorgusu `function round(double precision, integer) does not exist` hatası verdi. Agent hatayı okudu, tip dönüşümü (`::numeric`) ekledi ve tekrar denedi. Agent döngüsünde araç hatalarının modele geri gönderilmesinin sebebi budur.
- **Çapraz doğrulama:** Satış analisti (case 01) sonucu hem özet tablodan hem ham `orders` tablosundan hesaplayıp karşılaştırdı.
- **Koordinatörün katma değeri (case 06):** Satış analisti "sepet %54 büyüdü ama fiyattan mı adetten mi, ayıramam" dedi. Ürün analisti "birim fiyat %53,9 arttı" dedi. Koordinatör ikisini birleştirip "adet değişmemiş, büyüme tamamen fiyat artışı" sonucuna vardı. Bu çıkarımı tek başına iki uzmandan hiçbiri yapamazdı.
- **Bulgu:** Veride yaş ve cinsiyetin harcamaya etkisi yok denecek kadar az (fark < %0,3). Veri sentetik olduğu için beklenen bir sonuç.
