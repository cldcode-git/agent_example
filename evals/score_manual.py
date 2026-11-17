"""API anahtarı olmadan yapılan manuel çalıştırmaları puanlar.

Manuel çalıştırmada model yerine Claude Code oturumu koordinatör rolünü üstlenir, uzmanları ayrı
alt-agent'lar oynar. Her vaka için:
    evals/manual/<case_id>.jsonl   -> uzmanların araç çağrıları (superstore_agents.cli yazar)
    evals/manual/<case_id>.md      -> koordinatörün kullanıcıya verdiği son cevap

Kullanım:
    python -m evals.score_manual                # tüm vakalar
    python -m evals.score_manual --case 01

Kurala dayalı kontroller (sayı, kelime, yönlendirme, veritabanı) aynen çalışır.
LLM hakem kontrolleri API gerektirdiği için burada ATLANIR ve "manuel" olarak işaretlenir.
"""

import argparse
import json

from superstore_agents.config import PROJECT_ROOT
from superstore_agents.tracing import Tracer

from .cases import CASES
from .checks import Context, DatabaseUnchanged, LLMJudge

MANUAL = PROJECT_ROOT / "evals" / "manual"
GREEN, RED, YELLOW, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[0m"


def load_trace(case_id: str) -> Tracer:
    tracer = Tracer(verbose=False)
    path = MANUAL / f"{case_id}.jsonl"
    if path.exists():
        tracer.events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    tracer.events.insert(0, {"agent": "koordinator", "kind": "start"})
    return tracer


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--case")
    args = p.parse_args()

    summary = []
    for case in CASES:
        if args.case and not case.id.startswith(args.case):
            continue
        answer_file = MANUAL / f"{case.id}.md"
        if not answer_file.exists():
            print(f"\n━━━ {case.id}: cevap dosyası yok, atlandı")
            continue
        tracer = load_trace(case.id)
        ctx = Context(answer=answer_file.read_text(), tracer=tracer, question=case.question)
        print(f"\n━━━ {case.id}: {case.description}")
        auto_ok = True
        for check in case.checks:
            if isinstance(check, LLMJudge):
                print(f"  {YELLOW}?{RESET} hakem (API gerekir, manuel değerlendirilecek): {check.rubric}")
                continue
            if isinstance(check, DatabaseUnchanged):
                ctx.before = json.loads((MANUAL / f"{case.id}.before.json").read_text())
            ok, detail = check(ctx)
            auto_ok &= ok
            print(f"  {GREEN + '✔' if ok else RED + '✘'}{RESET} {detail}")
        n_calls = len(tracer.tool_calls())
        print(f"  → otomatik kontroller: {GREEN + 'GEÇTİ' if auto_ok else RED + 'KALDI'}{RESET} "
              f"(uzmanlar: {', '.join(sorted(tracer.agents_called() - {'koordinator'})) or '-'}; {n_calls} araç çağrısı)")
        summary.append((case.id, auto_ok))

    print("\n" + "═" * 60)
    for cid, ok in summary:
        print(f" {GREEN + '✔' if ok else RED + '✘'}{RESET} {cid}")
    print(f"Otomatik kontroller: {sum(ok for _, ok in summary)}/{len(summary)} vaka geçti")


if __name__ == "__main__":
    main()
