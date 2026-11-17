"""Agent ekibine komut satırından soru sor.

Kullanım:
    python ask.py "2022 yılında en çok ciro yapan 5 şube hangileri?"
    python ask.py --quiet "..."          # sadece son cevabı göster
    python ask.py --effort high "..."    # daha derin düşünme (daha yavaş/pahalı)
    python ask.py                        # etkileşimli mod
"""

import argparse
import os
import sys

from superstore_agents.team import ask


def answer(question: str, quiet: bool, effort: str) -> None:
    result, tracer = ask(question, verbose=not quiet, effort=effort)
    u = tracer.total_usage()
    print("\n" + "═" * 70)
    print(result.text)
    print("═" * 70)
    print(
        f"Çalışan agent'lar: {', '.join(sorted(tracer.agents_called()))} | "
        f"token: {u['input']:,} girdi (+{u['cache_read']:,} önbellekten) / {u['output']:,} çıktı"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="*")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    args = parser.parse_args()

    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY tanımlı değil. .env dosyasına ekle (bkz. README).")

    if args.question:
        answer(" ".join(args.question), args.quiet, args.effort)
        return
    print("3A Superstore analiz ekibi. Çıkmak için boş satır gir.")
    while question := input("\nSoru> ").strip():
        answer(question, args.quiet, args.effort)


if __name__ == "__main__":
    main()
