"""Veritabanı kurulumunu ve agent araçlarını doğrular (API anahtarı gerekmez, çalışan Postgres gerekir).

    pytest tests/test_database.py
"""

import psycopg
import pytest

from superstore_agents import db
from superstore_agents.config import reader_dsn
from superstore_agents.team import SATIS_TABLES, URUN_TABLES
from superstore_agents.tools import make_db_tools

try:
    psycopg.connect(reader_dsn(), connect_timeout=3).close()
except Exception as exc:
    pytest.skip(f"Veritabanına bağlanılamadı: {exc}", allow_module_level=True)


def count(sql: str) -> int:
    return db.query(sql)[1][0][0]


@pytest.mark.parametrize("table,expected", [
    ("branches", 161),
    ("branch_service_towns", 957),
    ("items", 27000),
    ("customers", 99998),
    ("orders", 10235193),
    ("order_details", 51185032),
])
def test_row_counts(table, expected):
    assert count(f"SELECT COUNT(*) FROM {table}") == expected


def test_summary_tables_match_base_tables():
    """Özet tablolar büyük tablolarla aynı toplamları vermeli (yoksa agent'lar yanlış cevap verir)."""
    assert count("SELECT SUM(order_count) FROM daily_branch_sales") == count("SELECT COUNT(*) FROM orders")
    assert count("SELECT SUM(line_count) FROM monthly_item_sales") == count("SELECT COUNT(*) FROM order_details")
    assert count("SELECT SUM(order_count) FROM customer_summary") == count("SELECT COUNT(*) FROM orders")


def test_decimal_and_coordinates_parsed():
    lat, lon = db.query("SELECT lat, lon FROM branches WHERE branch_id = '637-KA1'")[1][0]
    assert 35 < lat < 43 and 25 < lon < 45          # Türkiye sınırları içinde
    assert count("SELECT COUNT(*) FROM order_details WHERE unit_price <> TRUNC(unit_price)") > 0  # ondalık korunmuş


def test_reader_cannot_write():
    with psycopg.connect(reader_dsn()) as conn:
        with pytest.raises(psycopg.Error):
            conn.execute("DELETE FROM branches")


def test_tools_respect_permissions():
    list_tables, describe_table, run_sql = make_db_tools(SATIS_TABLES)
    listed = {line.split(" | ")[0] for line in list_tables.func().splitlines()}
    assert "daily_branch_sales" in listed and "customers" not in listed
    assert "revenue" in describe_table.func("daily_branch_sales")
    assert "161" in run_sql.func("SELECT COUNT(*) FROM branches")
    with pytest.raises(db.SQLRejected):
        run_sql.func("SELECT COUNT(*) FROM customers")
    with pytest.raises(db.SQLRejected):
        describe_table.func("customers")

    _, _, urun_sql = make_db_tools(URUN_TABLES)
    assert "SEKERLEME" in urun_sql.func("SELECT DISTINCT category1 FROM items ORDER BY 1")
