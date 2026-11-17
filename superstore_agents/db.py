"""Agent'ların veritabanına erişim katmanı.

Güvenlik katmanları:
  1. validate_select(): sadece tek bir SELECT sorgusu, yasaklı komut yok.
  2. check_tables(): her agent sadece KENDİSİNE İZİN VERİLEN tablolara erişebilir
     (ör. ürün analisti müşteri tablosunu göremez). "En az yetki" prensibi.
  3. Veritabanı kullanıcısı (superstore_reader) zaten sadece SELECT yetkisine sahip, oturum salt-okunur.
"""

import datetime as dt
import decimal
import re

import psycopg

from .config import reader_dsn

MAX_ROWS = 100
MAX_CELL_CHARS = 200

# Veritabanındaki tüm tablolar ve özet tablolar
ALL_TABLES = frozenset({
    "branches", "branch_service_towns", "items", "customers", "orders", "order_details",
    "daily_branch_sales", "monthly_item_sales", "customer_summary",
})

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|vacuum|"
    r"call|merge|comment|lock|refresh|execute|prepare|listen|notify|do|set|reset)\b"
    r"|\bpg_(sleep|read|ls|write|terminate|cancel)\w*|\blo_\w+|\bdblink",
    re.IGNORECASE,
)


class SQLRejected(ValueError):
    """Sorgu güvenlik kontrolünden geçemedi."""


def _strip_literals_and_comments(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    return sql


def validate_select(sql: str) -> str:
    """Sadece tek bir SELECT (veya WITH ... SELECT) sorgusuna izin verir."""
    cleaned = _strip_literals_and_comments(sql).strip().rstrip(";").strip()
    if not cleaned:
        raise SQLRejected("Boş sorgu.")
    if ";" in cleaned:
        raise SQLRejected("Tek seferde yalnızca bir sorgu çalıştırılabilir.")
    if not re.match(r"^(select|with)\b", cleaned, re.IGNORECASE):
        raise SQLRejected("Yalnızca SELECT sorgularına izin var (WITH ... SELECT dahil).")
    match = _FORBIDDEN.search(cleaned)
    if match:
        raise SQLRejected(f"Yasaklı ifade: '{match.group(0)}'. Veritabanı salt-okunurdur.")
    return sql.strip().rstrip(";")


def check_tables(sql: str, allowed: frozenset[str]) -> None:
    """Sorguda geçen tabloların bu agent'a izin verilen tablolar olduğunu kontrol eder."""
    words = set(re.findall(r"[a-z_][a-z0-9_]*", _strip_literals_and_comments(sql).lower()))
    if words & {"pg_catalog", "information_schema"} or any(w.startswith("pg_") for w in words):
        raise SQLRejected("Sistem kataloglarına erişim yok; şema için list_tables/describe_table kullan.")
    denied = (words & ALL_TABLES) - allowed
    if denied:
        raise SQLRejected(
            f"Bu agent'ın {sorted(denied)} tablosuna erişim yetkisi yok. "
            f"İzin verilen tablolar: {sorted(allowed)}"
        )


def _fmt(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, decimal.Decimal):
        return format(value.normalize(), "f") if value == value.to_integral() else str(round(value, 4))
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, dt.date):
        return value.isoformat()
    text = str(value).replace("\n", " ").replace("\r", " ")
    return text if len(text) <= MAX_CELL_CHARS else text[:MAX_CELL_CHARS] + "…"


def query(sql: str, params: tuple | None = None) -> tuple[list[str], list[tuple], bool]:
    """Salt-okunur bağlantıda sorguyu çalıştırır. (kolonlar, satırlar, kırpıldı_mı) döner."""
    with psycopg.connect(reader_dsn(), autocommit=False) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(sql, params)
            columns = [d.name for d in cur.description] if cur.description else []
            rows = cur.fetchmany(MAX_ROWS + 1)
        conn.rollback()
    return columns, rows[:MAX_ROWS], len(rows) > MAX_ROWS


def format_table(columns: list[str], rows: list[tuple], truncated: bool) -> str:
    """Sonucu modelin kolay okuyacağı, az token harcayan bir metin tablosuna çevirir."""
    if not rows:
        return "(sonuç boş - 0 satır)"
    lines = [" | ".join(columns)]
    lines += [" | ".join(_fmt(v) for v in row) for row in rows]
    footer = f"\n({len(rows)} satır" + (f"; ilk {MAX_ROWS} satır gösterildi, sonuç kırpıldı)" if truncated else ")")
    return "\n".join(lines) + footer
