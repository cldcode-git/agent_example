"""CSV dosyalarını PostgreSQL'e yükler ve agent'ların kullanacağı özet tabloları oluşturur.

Kullanım:
    python scripts/load_data.py            # boş tabloları doldurur (~5-10 dk)
    python scripts/load_data.py --reset    # önce her şeyi boşaltır

Veri setindeki özellikler ve nasıl ele alındıkları:
  - Ondalık ayırıcı virgül ("12,5")      -> noktaya çevrilir
  - Şube koordinatları ölçeksiz ("4197583330") -> 41.97583330
  - Customers.csv noktalı virgülle ayrılmış ve kişisel veri içeriyor -> ad/e-posta/adres alınmaz
  - Orders.csv içindeki ad-soyad kolonu   -> alınmaz
  - order_details 51 milyon satır          -> COPY ile doğrudan akıtılır; indeksler yüklemeden sonra eklenir
"""

import argparse
import csv
import re
import sys
import time
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from superstore_agents.config import PROJECT_ROOT, admin_dsn  # noqa: E402

RAW = PROJECT_ROOT / "data" / "raw"
CHUNK = 8 << 20  # 8 MB
DECIMAL_COMMA = re.compile(rb'"(-?\d+),(\d+)"')  # "12,5" -> 12.5


def step(msg: str):
    print(f"• {msg}", flush=True)
    return time.time()


def done(start: float, extra: str = "") -> None:
    print(f"  tamam ({time.time() - start:.0f} sn) {extra}", flush=True)


def is_empty(cur, table: str) -> bool:
    cur.execute(f"SELECT NOT EXISTS (SELECT 1 FROM {table})")
    return cur.fetchone()[0]


def copy_rows(cur, table: str, columns: list[str], rows) -> None:
    with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)


def stream_file(cur, copy_sql: str, path: Path, fix_decimals: bool) -> None:
    """Büyük dosyayı parça parça okuyup COPY'ye akıtır (belleğe tamamını almadan)."""
    size, sent, tail = path.stat().st_size, 0, b""
    next_report = 0.1
    with open(path, "rb") as f, cur.copy(copy_sql) as copy:
        while chunk := f.read(CHUNK):
            sent += len(chunk)
            data = tail + chunk
            cut = data.rfind(b"\n") + 1          # satır ortasından bölmemek için son satırı sonraki parçaya bırak
            data, tail = data[:cut], data[cut:]
            copy.write(DECIMAL_COMMA.sub(rb"\1.\2", data) if fix_decimals else data)
            if sent / size >= next_report:
                print(f"    %{next_report * 100:.0f}", end="", flush=True)
                next_report += 0.1
        if tail:
            copy.write(DECIMAL_COMMA.sub(rb"\1.\2", tail) if fix_decimals else tail)
    print()


def load_small_tables(cur) -> None:
    if is_empty(cur, "branches"):
        # Branches.csv her satırda (şube, hizmet verdiği ilçe) çifti içerir: 161 şube, 957 satır.
        # -> branches: şube başına 1 satır (koordinat = şubenin kendi ilçesi)
        # -> branch_service_towns: şube x hizmet ilçesi
        t = step("branches + branch_service_towns")
        with open(RAW / "Branches.csv", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))

        def coord(v):
            return int(v) / 1e8 if v else None  # "4197583330" -> 41.97583330

        branches = {}
        for x in rows:
            b = branches.setdefault(x["BRANCH_ID"], [x["BRANCH_ID"], x["REGION"], x["CITY"], x["BRANCH_TOWN"],
                                                    coord(x["LAT"]), coord(x["LON"])])
            if x["TOWN"] == x["BRANCH_TOWN"]:      # şubenin kendi ilçesinin koordinatını tercih et
                b[4], b[5] = coord(x["LAT"]), coord(x["LON"])
        copy_rows(cur, "branches", ["branch_id", "region", "city", "branch_town", "lat", "lon"], branches.values())
        copy_rows(cur, "branch_service_towns", ["branch_id", "town", "lat", "lon"],
                  ((x["BRANCH_ID"], x["TOWN"], coord(x["LAT"]), coord(x["LON"])) for x in rows))
        done(t, f"{len(branches)} şube, {len(rows)} hizmet ilçesi")

    if is_empty(cur, "items"):
        t = step("items (Categories.csv)")
        cols = ["item_id", "category1", "category1_id", "category2", "category2_id", "category3",
                "category3_id", "category4", "category4_id", "brand", "item_code", "item_name"]
        with open(RAW / "Categories.csv", encoding="utf-8-sig", newline="") as f:
            r = csv.reader(f)
            next(r)
            copy_rows(cur, "items", cols, ([v if v != "" else None for v in row] for row in r))
        done(t)

    if is_empty(cur, "customers"):
        t = step("customers (kişisel veriler hariç)")
        with open(RAW / "Customers.csv", encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f, delimiter=";")
            copy_rows(cur, "customers", ["user_id", "gender", "birth_date", "region", "city", "town", "district"], (
                (x["USERID"], x["USERGENDER"], x["USERBIRTHDATE"] or None, x["REGION"], x["CITY"], x["TOWN"],
                 x["DISTRICT"]) for x in r))
        done(t)


def load_orders(cur) -> None:
    if not is_empty(cur, "orders"):
        print("• orders zaten dolu, atlanıyor")
        return
    t = step("orders (~10 milyon satır): ham veri geçici tabloya kopyalanıyor")
    cur.execute("""CREATE TEMP TABLE orders_raw (order_id BIGINT, branch_id TEXT, date_ TEXT,
                   user_id INTEGER, namesurname TEXT, total_basket NUMERIC)""")
    stream_file(cur, "COPY orders_raw FROM STDIN WITH (FORMAT csv, HEADER true)",
                RAW / "Orders.csv", fix_decimals=True)
    cur.execute("""INSERT INTO orders (order_id, branch_id, order_date, user_id, total_basket)
                   SELECT order_id, branch_id, date_::date, user_id, round(total_basket, 2) FROM orders_raw""")
    cur.execute("DROP TABLE orders_raw")
    done(t)


def load_order_details(cur) -> None:
    if not is_empty(cur, "order_details"):
        print("• order_details zaten dolu, atlanıyor")
        return
    t = step("order_details (~51 milyon satır, birkaç dakika sürer)")
    stream_file(cur, """COPY order_details (order_id, order_detail_id, amount, unit_price, total_price, item_id, item_code)
                        FROM STDIN WITH (FORMAT csv, HEADER true)""",
                RAW / "Order_Details.csv", fix_decimals=True)
    done(t)


POST_LOAD_SQL = [
    ("orders birincil anahtar", "ALTER TABLE orders ADD PRIMARY KEY (order_id)"),
    ("order_details birincil anahtar", "ALTER TABLE order_details ADD PRIMARY KEY (order_detail_id)"),
    ("indeksler", """
        CREATE INDEX IF NOT EXISTS idx_orders_date   ON orders(order_date);
        CREATE INDEX IF NOT EXISTS idx_orders_branch ON orders(branch_id);
        CREATE INDEX IF NOT EXISTS idx_orders_user   ON orders(user_id);
        CREATE INDEX IF NOT EXISTS idx_details_order ON order_details(order_id);
        CREATE INDEX IF NOT EXISTS idx_details_item  ON order_details(item_id);
    """),
]

# Özet (materialized view) tablolar: büyük tabloları her soruda taramak yerine önceden toplanmış veri.
# Veri değişirse: REFRESH MATERIALIZED VIEW <ad>;
SUMMARY_SQL = [
    ("daily_branch_sales", """
        CREATE MATERIALIZED VIEW daily_branch_sales AS
        SELECT order_date, branch_id, COUNT(*) AS order_count, SUM(total_basket) AS revenue
        FROM orders GROUP BY order_date, branch_id;
        CREATE INDEX ON daily_branch_sales(order_date);
        CREATE INDEX ON daily_branch_sales(branch_id);
        COMMENT ON MATERIALIZED VIEW daily_branch_sales IS
          'ÖZET: gün x şube bazında sipariş sayısı ve ciro (orders tablosundan). Ciro/sipariş sayısı trendleri ve şube/bölge/il karşılaştırmaları için ilk tercih; branches ile branch_id üzerinden birleştir. Toplamlar orders tablosuyla birebir aynıdır.';
        COMMENT ON COLUMN daily_branch_sales.revenue IS 'Ciro, TL = SUM(orders.total_basket)';
    """),
    ("monthly_item_sales (51M satır gruplanıyor, birkaç dakika)", """
        CREATE MATERIALIZED VIEW monthly_item_sales AS
        SELECT date_trunc('month', o.order_date)::date AS month, d.item_id,
               SUM(d.amount) AS quantity, SUM(d.total_price) AS revenue, COUNT(*) AS line_count
        FROM order_details d JOIN orders o ON o.order_id = d.order_id
        GROUP BY 1, 2;
        CREATE INDEX ON monthly_item_sales(month);
        CREATE INDEX ON monthly_item_sales(item_id);
        COMMENT ON MATERIALIZED VIEW monthly_item_sales IS
          'ÖZET: ay x ürün bazında satış adedi ve ciro (order_details + orders). Kategori/marka/ürün bazında satış soruları için ilk tercih; items ile item_id üzerinden birleştir. month = ayın ilk günü.';
        COMMENT ON COLUMN monthly_item_sales.quantity IS 'Satılan adet = SUM(order_details.amount)';
        COMMENT ON COLUMN monthly_item_sales.revenue IS 'Ürün cirosu, TL = SUM(order_details.total_price)';
        COMMENT ON COLUMN monthly_item_sales.line_count IS 'Ürünün geçtiği sipariş kalemi sayısı';
    """),
    ("customer_summary", """
        CREATE MATERIALIZED VIEW customer_summary AS
        SELECT user_id, COUNT(*) AS order_count, SUM(total_basket) AS total_spent,
               ROUND(AVG(total_basket), 2) AS avg_basket,
               MIN(order_date) AS first_order_date, MAX(order_date) AS last_order_date,
               COUNT(DISTINCT branch_id) AS branch_count
        FROM orders GROUP BY user_id;
        CREATE UNIQUE INDEX ON customer_summary(user_id);
        COMMENT ON MATERIALIZED VIEW customer_summary IS
          'ÖZET: müşteri başına sipariş sayısı, toplam harcama, ortalama sepet, ilk/son sipariş tarihi. Müşteri segmentasyonu (RFM vb.) için ilk tercih; customers ile user_id üzerinden birleştir. Hiç siparişi olmayan müşteriler burada yer almaz.';
    """),
]


def main(reset: bool) -> None:
    missing = [f for f in ["Branches.csv", "Categories.csv", "Customers.csv", "Orders.csv", "Order_Details.csv"]
               if not (RAW / f).exists()]
    if missing:
        sys.exit(f"Eksik dosyalar: {missing}\nÖnce çalıştır: python scripts/download_data.py")

    total = time.time()
    with psycopg.connect(admin_dsn(), autocommit=True) as conn:
        cur = conn.cursor()
        if reset:
            cur.execute("DROP MATERIALIZED VIEW IF EXISTS daily_branch_sales, monthly_item_sales, customer_summary")
            cur.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_pkey")
            cur.execute("ALTER TABLE order_details DROP CONSTRAINT IF EXISTS order_details_pkey")
            cur.execute("TRUNCATE branch_service_towns, branches, items, customers, orders, order_details")
            print("Tüm tablolar boşaltıldı.")

        # Her adım kendi transaction'ında (autocommit) - yarıda kalırsa tekrar çalıştırınca kaldığı yerden devam eder
        with conn.transaction():
            load_small_tables(cur)
        with conn.transaction():
            load_orders(cur)
        with conn.transaction():
            load_order_details(cur)

        cur.execute("SELECT conname FROM pg_constraint WHERE conname IN ('orders_pkey', 'order_details_pkey')")
        existing = {r[0] for r in cur.fetchall()}
        for label, sql in POST_LOAD_SQL:
            if "birincil" in label and sql.split()[2] + "_pkey" in existing:
                continue
            t = step(label)
            cur.execute(sql)
            done(t)

        cur.execute("SELECT matviewname FROM pg_matviews")
        existing = {r[0] for r in cur.fetchall()}
        for label, sql in SUMMARY_SQL:
            if label.split()[0] in existing:
                continue
            t = step(f"özet tablo: {label}")
            with conn.transaction():
                cur.execute(sql)
            done(t)

        t = step("ANALYZE (sorgu planlayıcısı istatistikleri)")
        cur.execute("ANALYZE")
        done(t)

        print("\nSatır sayıları:")
        for table in ["branches", "branch_service_towns", "items", "customers", "orders", "order_details",
                      "daily_branch_sales", "monthly_item_sales", "customer_summary"]:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            print(f"  {table:<22} {cur.fetchone()[0]:>12,}")
    print(f"\nYükleme tamamlandı ({(time.time() - total) / 60:.1f} dk).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true")
    main(parser.parse_args().reset)
