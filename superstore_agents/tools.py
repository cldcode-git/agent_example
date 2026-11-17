"""Uzman agent'ların kullandığı veritabanı araçları.

Her agent kendi araç setini make_db_tools(izinli_tablolar) ile alır. Araçların adı aynıdır
ama her agent sadece kendi tablolarını görebilir ve sorgulayabilir.

Her araç = (modelin göreceği açıklama + girdi şeması) + (bizim çalıştırdığımız Python fonksiyonu).
Açıklamalar önemlidir: model bir aracı ne zaman ve nasıl kullanacağını SADECE bu metinden öğrenir.
"""

import time

from . import db
from .agent import Tool


def _schema(properties: dict) -> dict:
    # strict modda tüm alanlar "required" listesinde olmalı
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def make_db_tools(allowed: frozenset[str]) -> list[Tool]:
    allowed_list = ", ".join(sorted(allowed))

    def list_tables() -> str:
        cols, rows, _ = db.query(
            """
            SELECT c.relname AS tablo,
                   CASE c.relkind WHEN 'm' THEN 'özet tablo' ELSE 'tablo' END AS tur,
                   c.reltuples::bigint AS yaklasik_satir,
                   obj_description(c.oid) AS aciklama
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'm') AND c.relname = ANY(%s)
            ORDER BY c.relkind DESC, c.relname
            """,
            (sorted(allowed),),
        )
        return db.format_table(cols, rows, False)

    def describe_table(table_name: str) -> str:
        if table_name not in allowed:
            raise db.SQLRejected(f"'{table_name}' tablosuna erişim yok. Erişebildiğin tablolar: {allowed_list}")
        cols, rows, _ = db.query(
            """
            SELECT a.attname AS kolon,
                   format_type(a.atttypid, a.atttypmod) AS tip,
                   col_description(a.attrelid, a.attnum) AS aciklama
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
            """,
            (table_name,),
        )
        _, sample, _ = db.query(f'SELECT * FROM "{table_name}" LIMIT 3')
        return (
            db.format_table(cols, rows, False)
            + "\n\nÖrnek 3 satır:\n"
            + db.format_table([r[0] for r in rows], sample, False)
        )

    def run_sql(sql: str) -> str:
        safe_sql = db.validate_select(sql)
        db.check_tables(safe_sql, allowed)
        start = time.time()
        result = db.format_table(*db.query(safe_sql))
        return result + f"\n[sorgu süresi: {time.time() - start:.1f} sn]"

    return [
        Tool(
            name="list_tables",
            description=f"Erişebildiğin tabloları ({allowed_list}), yaklaşık satır sayılarını ve açıklamalarını "
                        "listeler. 'özet tablo' olanlar önceden toplanmış verilerdir ve çok daha hızlıdır.",
            input_schema=_schema({}),
            func=list_tables,
        ),
        Tool(
            name="describe_table",
            description="Bir tablonun kolonlarını, tiplerini, kolon açıklamalarını ve 3 örnek satırı gösterir. "
                        "SQL yazmadan önce kullanacağın tabloları bununla incele.",
            input_schema=_schema({"table_name": {"type": "string", "description": "Tablo adı"}}),
            func=describe_table,
        ),
        Tool(
            name="run_sql",
            description=(
                "PostgreSQL üzerinde SALT-OKUNUR bir SELECT sorgusu çalıştırır. Kurallar: tek sorgu; sadece "
                f"SELECT/WITH; sadece şu tablolar: {allowed_list}. En fazla 100 satır döner, bu yüzden toplama "
                "(COUNT/SUM/AVG/GROUP BY) ve ORDER BY ... LIMIT kullan. 60 saniyeyi aşan sorgular iptal edilir: "
                "mümkünse özet tabloları kullan."
            ),
            input_schema=_schema({"sql": {"type": "string", "description": "Çalıştırılacak SELECT sorgusu"}}),
            func=run_sql,
        ),
    ]
