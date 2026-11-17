"""API anahtarı ve veritabanı GEREKTİRMEYEN testler.

    pytest tests/test_offline.py
"""

import pytest

from evals.checks import _norm, extract_numbers
from superstore_agents.db import SQLRejected, check_tables, validate_select
from superstore_agents.team import MUSTERI_TABLES, SATIS_TABLES, URUN_TABLES


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM orders",
    "select 1;",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
    "SELECT COUNT(*) FROM items WHERE item_name ILIKE '%delete%'",   # metin içindeki kelime sorun değil
])
def test_select_allowed(sql):
    validate_select(sql)


@pytest.mark.parametrize("sql", [
    "DELETE FROM orders WHERE order_date < '2022-01-01'",
    "DROP TABLE orders",
    "UPDATE items SET brand = 'x'",
    "SELECT 1; DELETE FROM orders",
    "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d",
    "SELECT pg_sleep(100)",
    "REFRESH MATERIALIZED VIEW daily_branch_sales",
    "",
])
def test_dangerous_rejected(sql):
    with pytest.raises(SQLRejected):
        validate_select(sql)


def test_table_permissions():
    """Her uzman sadece kendi tablolarına erişebilir."""
    check_tables("SELECT SUM(revenue) FROM daily_branch_sales d JOIN branches b USING (branch_id)", SATIS_TABLES)
    check_tables("SELECT * FROM monthly_item_sales JOIN items USING (item_id)", URUN_TABLES)
    check_tables("SELECT gender, AVG(avg_basket) FROM customers JOIN customer_summary USING (user_id) GROUP BY 1",
                 MUSTERI_TABLES)
    with pytest.raises(SQLRejected, match="customers"):
        check_tables("SELECT gender FROM customers", SATIS_TABLES)       # satış analisti müşteri göremez
    with pytest.raises(SQLRejected, match="customers"):
        check_tables("SELECT COUNT(*) FROM customers", URUN_TABLES)      # ürün analisti de göremez
    with pytest.raises(SQLRejected):
        check_tables("SELECT * FROM pg_catalog.pg_tables", MUSTERI_TABLES)


@pytest.mark.parametrize("text,expected", [
    ("Toplam 10.235.193 sipariş var", 10235193),
    ("Total 10,235,193 orders", 10235193),
    ("ortalama sepet 254,37 TL", 254.37),
    ("average 254.37", 254.37),
    ("ciro 2,61 milyar TL", 2.61e9),
    ("yaklaşık 845 milyon TL", 845e6),
])
def test_extract_numbers(text, expected):
    assert any(abs(n - expected) < 1e-6 * max(1, expected) for n in extract_numbers(text))


def test_turkish_normalization():
    assert _norm("İç Anadolu") == _norm("IC ANADOLU") == "ic anadolu"
    assert _norm("ŞEKERLEME") == _norm("sekerleme")
