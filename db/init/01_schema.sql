-- 3A Superstore şeması
-- Kaynak: https://www.kaggle.com/datasets/cemeraan/3a-superstore (Türkiye'de bir süpermarket zinciri, sentetik veri)
-- Tablo/kolon açıklamaları (COMMENT) agent'lar tarafından describe_table aracıyla okunur.
-- Kişisel veriler (ad-soyad, e-posta, açık adres) analiz için gerekmediğinden veritabanına HİÇ alınmaz.

CREATE TABLE branches (
    branch_id    TEXT PRIMARY KEY,
    region       TEXT NOT NULL,
    city         TEXT NOT NULL,
    branch_town  TEXT NOT NULL,
    lat          DOUBLE PRECISION,
    lon          DOUBLE PRECISION
);
COMMENT ON TABLE branches IS 'Mağaza şubeleri (161 şube). Her şube bir coğrafi bölgede (region) ve ildedir (city).';
COMMENT ON COLUMN branches.region IS 'Coğrafi bölge: Akdeniz, Doğu Anadolu, Ege, Güneydoğu Anadolu, Karadeniz, Marmara, İç Anadolu';
COMMENT ON COLUMN branches.city IS 'İl adı (ör. İstanbul, Ankara)';
COMMENT ON COLUMN branches.branch_town IS 'Şubenin bulunduğu ilçe (BÜYÜK HARF)';

CREATE TABLE branch_service_towns (
    branch_id  TEXT NOT NULL REFERENCES branches(branch_id),
    town       TEXT NOT NULL,
    lat        DOUBLE PRECISION,
    lon        DOUBLE PRECISION,
    PRIMARY KEY (branch_id, town)
);
COMMENT ON TABLE branch_service_towns IS
  'Hangi şubenin hangi ilçelere hizmet verdiği (bir şube kendi ilindeki birden fazla ilçeye hizmet verir). Koordinatlar ilçenin merkezidir.';

CREATE TABLE items (
    item_id       INTEGER PRIMARY KEY,
    category1     TEXT,
    category1_id  TEXT,
    category2     TEXT,
    category2_id  TEXT,
    category3     TEXT,
    category3_id  TEXT,
    category4     TEXT,
    category4_id  TEXT,
    brand         TEXT,
    item_code     INTEGER,
    item_name     TEXT
);
COMMENT ON TABLE items IS
  'Ürün kataloğu. Kategoriler 4 seviyelidir: category1 (ana kategori, ör. GIDA, KOZMETIK) > category2 > category3 > category4 (en detaylı). Metinler Türkçe karakter içermeyen BÜYÜK HARFLE yazılmıştır (ör. SEKERLEME, SUT).';
COMMENT ON COLUMN items.category1 IS 'Ana kategori, ör. EV, KOZMETIK, GIDA, OYUNCAK, SEKERLEME, KAHVALTILIK, TEMIZLIK, BEBEK, DETERJAN, SUT';
COMMENT ON COLUMN items.brand IS 'Marka';

CREATE TABLE customers (
    user_id     INTEGER PRIMARY KEY,
    gender      CHAR(1) NOT NULL,
    birth_date  DATE,
    region      TEXT,
    city        TEXT,
    town        TEXT,
    district    TEXT
);
COMMENT ON TABLE customers IS 'Müşteriler (ad, e-posta ve açık adres gizlilik nedeniyle yüklenmedi).';
COMMENT ON COLUMN customers.gender IS 'E = erkek, K = kadın';
COMMENT ON COLUMN customers.birth_date IS 'Doğum tarihi. Yaş için: EXTRACT(YEAR FROM age(DATE ''2023-08-31'', birth_date)) (verinin son tarihi referans alınır)';
COMMENT ON COLUMN customers.region IS 'Müşterinin yaşadığı bölge (alışveriş yaptığı şubenin bölgesinden farklı olabilir)';

-- NOT: orders ve order_details için PRIMARY KEY ve indeksler, toplu yüklemeden SONRA
-- scripts/load_data.py tarafından eklenir (milyonlarca satırda yüklemeden sonra eklemek çok daha hızlıdır).
CREATE TABLE orders (
    order_id      BIGINT NOT NULL,
    branch_id     TEXT NOT NULL,
    order_date    DATE NOT NULL,
    user_id       INTEGER NOT NULL,
    total_basket  NUMERIC(12, 2) NOT NULL
);
COMMENT ON TABLE orders IS
  'Siparişler / alışveriş fişleri (~10 milyon satır, 2021-01-01 ile 2023-08 arası). branches.branch_id ve customers.user_id ile bağlanır. BÜYÜK TABLO: toplam ciro/sipariş sayısı soruları için önce daily_branch_sales özet tablosunu kullan.';
COMMENT ON COLUMN orders.total_basket IS 'Sepet (fiş) toplam tutarı, TL. Ciro = SUM(total_basket).';
COMMENT ON COLUMN orders.order_date IS 'Sipariş tarihi (2023 yılı Ağustos ayı yarımdır, ay karşılaştırmalarında dikkat)';

CREATE TABLE order_details (
    order_detail_id  BIGINT NOT NULL,
    order_id         BIGINT NOT NULL,
    amount           INTEGER NOT NULL,
    unit_price       NUMERIC(12, 2) NOT NULL,
    total_price      NUMERIC(12, 2) NOT NULL,
    item_id          INTEGER NOT NULL,
    item_code        INTEGER
);
COMMENT ON TABLE order_details IS
  'Sipariş kalemleri (~51 milyon satır). orders.order_id ve items.item_id ile bağlanır. ÇOK BÜYÜK TABLO: ürün/kategori/marka bazında aylık satış soruları için önce monthly_item_sales özet tablosunu kullan; bu tabloya doğrudan sorgu 10-60 sn sürebilir.';
COMMENT ON COLUMN order_details.amount IS 'Adet';
COMMENT ON COLUMN order_details.unit_price IS
  'Etiket birim fiyatı, TL. DİKKAT: amount * unit_price, total_price ile genelde EŞİT DEĞİLDİR (indirim/fiyat farkı); ciro için total_price kullan.';
COMMENT ON COLUMN order_details.total_price IS
  'Kalemin gerçekte ödenen tutarı, TL. Ürün/kategori cirosu = SUM(total_price). Bir siparişin kalemlerinin total_price toplamı orders.total_basket değerine eşittir. Gerçekleşen ortalama birim fiyat = SUM(total_price) / SUM(amount).';
