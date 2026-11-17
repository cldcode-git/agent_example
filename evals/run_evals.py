"""Deneme vakalarını çalıştırır ve sonuçları raporlar.

Kullanım (proje kök dizininden):
    python -m evals.run_evals                     # tüm vakalar
    python -m evals.run_evals --case 04           # id'si "04" ile başlayan vaka
    python -m evals.run_evals --verbose           # agent adımlarını canlı izle
    python -m evals.run_evals --effort high       # farklı ayarları karşılaştırmak için

Her çalıştırma evals/results/ altına ayrıntılı bir JSON (cevaplar, kontroller, agent izleri) yazar.
Ayar değiştirdiğinde (prompt, effort, model) önceki JSON ile karşılaştırarak iyileşip iyileşmediğini görebilirsin.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

from superstore_agents.config import MODEL, PROJECT_ROOT
from superstore_agents.team import ask

from .cases import CASES
from .checks import Context, DatabaseUnchanged

RESULTS_DIR = PROJECT_ROOT / "evals" / "results"
GREEN, RED, DIM, RESET = "\033[92m", "\033[91m", "\033[2m", "\033[0m"


def run_case(case, verbose: bool, effort: str) -> dict:
    print(f"\n━━━ {case.id}: {case.description}")
    print(f"{DIM}Soru: {case.question}{RESET}")

    before = {}
    for check in case.checks:
        if isinstance(check, DatabaseUnchanged):
            before = check.snapshot()

    start = time.time()
    try:
        result, tracer = ask(case.question, verbose=verbose, effort=effort)
        answer, error = result.text, None
    except Exception as exc:  # bir vakanın çökmesi diğerlerini durdurmasın
        from superstore_agents.tracing import Tracer
        tracer, answer, error = Tracer(verbose=False), "", f"{type(exc).__name__}: {exc}"
    duration = time.time() - start

    ctx = Context(answer=answer, tracer=tracer, question=case.question, before=before)
    check_results = []
    for check in case.checks:
        if error:
            ok, detail = False, f"çalıştırma hatası: {error}"
        else:
            try:
                ok, detail = check(ctx)
            except Exception as exc:
                ok, detail = False, f"kontrol hatası: {type(exc).__name__}: {exc}"
        check_results.append({"check": type(check).__name__, "passed": ok, "detail": detail})
        print(f"  {GREEN + '✔' if ok else RED + '✘'}{RESET} {detail}")

    usage = tracer.total_usage()
    passed = all(c["passed"] for c in check_results)
    print(f"  → {GREEN + 'GEÇTİ' if passed else RED + 'KALDI'}{RESET}  "
          f"{DIM}({duration:.0f} sn, {usage['input'] + usage['cache_read']:,} girdi / {usage['output']:,} çıktı token){RESET}")
    if not verbose and answer:
        print(f"{DIM}  Cevap: {answer[:400].replace(chr(10), ' ')}{'…' if len(answer) > 400 else ''}{RESET}")

    return {
        "id": case.id,
        "question": case.question,
        "passed": passed,
        "answer": answer,
        "error": error,
        "checks": check_results,
        "duration_s": round(duration, 1),
        "usage": usage,
        "agents_called": sorted(tracer.agents_called()),
        "trace": tracer.events,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", help="Sadece id'si bununla başlayan vakaları çalıştır")
    parser.add_argument("--verbose", action="store_true", help="Agent adımlarını canlı göster")
    parser.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    args = parser.parse_args()

    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY tanımlı değil. .env dosyasına ekle (bkz. README).")

    cases = [c for c in CASES if not args.case or c.id.startswith(args.case)]
    if not cases:
        sys.exit(f"'{args.case}' ile eşleşen vaka yok. Mevcutlar: {[c.id for c in CASES]}")

    results = [run_case(c, args.verbose, args.effort) for c in cases]

    n_pass = sum(r["passed"] for r in results)
    total_in = sum(r["usage"]["input"] + r["usage"]["cache_read"] + r["usage"]["cache_write"] for r in results)
    total_out = sum(r["usage"]["output"] for r in results)
    print("\n" + "═" * 70)
    for r in results:
        mark = f"{GREEN}✔{RESET}" if r["passed"] else f"{RED}✘{RESET}"
        print(f" {mark} {r['id']:<32} {r['duration_s']:>6.0f} sn   agent'lar: {', '.join(r['agents_called'])}")
    print("═" * 70)
    print(f"SONUÇ: {n_pass}/{len(results)} vaka geçti | model={MODEL} effort={args.effort} | "
          f"toplam {total_in:,} girdi / {total_out:,} çıktı token (hakem çağrıları hariç)")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{args.effort}.json"
    out.write_text(json.dumps(
        {"model": MODEL, "effort": args.effort, "passed": n_pass, "total": len(results), "results": results},
        ensure_ascii=False, indent=2, default=str,
    ))
    print(f"Ayrıntılı sonuçlar: {out.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
