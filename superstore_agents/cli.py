"""Agent araçlarını komut satırından çalıştırır (API anahtarı OLMADAN manuel test için).

Normalde araçları model çağırır. Bu araç, aynı araçları aynı tablo yetkileriyle bir insanın
(veya Claude Code içindeki bir alt-agent'ın) çağırabilmesini sağlar. Her çağrı bir iz (trace)
dosyasına yazılır; değerlendirme scripti bu izden hangi agent'ların çalıştığını okur.

Kullanım:
    python -m superstore_agents.cli --agent satis_analisti system
    python -m superstore_agents.cli --agent satis_analisti --trace evals/manual/01.jsonl list_tables
    python -m superstore_agents.cli --agent satis_analisti --trace ... describe_table daily_branch_sales
    python -m superstore_agents.cli --agent satis_analisti --trace ... run_sql "SELECT ..."
"""

import argparse
import json
import sys
import time
from pathlib import Path

from .team import MUSTERI_SYSTEM, MUSTERI_TABLES, SATIS_SYSTEM, SATIS_TABLES, URUN_SYSTEM, URUN_TABLES
from .tools import make_db_tools

SPECIALISTS = {
    "satis_analisti": (SATIS_SYSTEM, SATIS_TABLES),
    "urun_analisti": (URUN_SYSTEM, URUN_TABLES),
    "musteri_analisti": (MUSTERI_SYSTEM, MUSTERI_TABLES),
}


def log(trace: str | None, agent: str, kind: str, **data) -> None:
    if not trace:
        return
    Path(trace).parent.mkdir(parents=True, exist_ok=True)
    with open(trace, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": round(time.time(), 3), "agent": agent, "kind": kind, **data}, ensure_ascii=False) + "\n")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--agent", required=True, choices=sorted(SPECIALISTS))
    p.add_argument("--trace", help="İz dosyası (JSONL)")
    p.add_argument("command", choices=["system", "start", "list_tables", "describe_table", "run_sql", "end"])
    p.add_argument("arg", nargs="?", default="")
    a = p.parse_args()

    system, tables = SPECIALISTS[a.agent]
    if a.command == "system":
        print(system)
        return
    if a.command in ("start", "end"):          # görev başlangıcı/bitişi iz kaydına
        log(a.trace, a.agent, a.command, task=a.arg) if a.command == "start" else log(a.trace, a.agent, "end", text=a.arg)
        return

    tools = {t.name: t for t in make_db_tools(tables)}
    inputs = {"list_tables": {}, "describe_table": {"table_name": a.arg}, "run_sql": {"sql": a.arg}}[a.command]
    log(a.trace, a.agent, "tool_call", tool=a.command, input=inputs)
    try:
        output, is_error = tools[a.command].func(**inputs), False
    except Exception as exc:
        output, is_error = f"HATA: {exc}", True
    log(a.trace, a.agent, "tool_result", tool=a.command, output=output[:2000], is_error=is_error)
    print(output)
    sys.exit(1 if is_error else 0)


if __name__ == "__main__":
    main()
