"""Runs the multi-turn assistant dialogs (evals/assistant/dialog_cases/dialogs.yaml) through the HTTP API
against real model slots, and checks what ended up in the database.

Usage (from the repo root, LLM_<N>_* in the environment, DATABASE_URL on a throwaway local database):
    python scripts/eval_dialogs.py --slot 3                    # every dialog on slot 3
    python scripts/eval_dialogs.py --slot 3 --slot 8 --locale ru
    python scripts/eval_dialogs.py --slot 3 --dialog ru-fix-time --pace 20

Each dialog seeds its own copy of the fixture salon and leaves it there, so this refuses any database
that isn't on localhost. One JSON file per dialog goes to <report-dir>/slot<N>/; a rerun skips dialogs
that already have a pass/fail file there (errors such as a used-up quota are retried), so a slot with a
daily limit can be finished over several runs. Free tiers may keep prompts: the fixture is made up.
Exit code: 0 all passed, 1 something failed or errored, 2 nothing to run.
"""
import argparse
import json
import logging
import os
import re
import sys
from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--slot", type=int, action="append", metavar="N", required=True, help="model slot (repeatable)")
    p.add_argument("--dialog", help="only this dialog id")
    p.add_argument("--locale", choices=("ru", "cz", "en"))
    p.add_argument("--tag", help="only dialogs with this tag")
    p.add_argument("--model", help="override the slot's model name")
    p.add_argument("--pace", type=float, default=0.0, help="pause before every model call, seconds")
    p.add_argument("--max-wait", type=float, default=120.0, help="longest single busy pause to sit out, seconds")
    p.add_argument("--give-up", type=float, default=900.0, help="most waiting per model call in total, seconds")
    p.add_argument("--report-dir", type=Path, default=Path("evals/reports/dialogs"))
    p.add_argument("--rerun", action="store_true", help="run dialogs that already have a result too")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    from app.api.core.logger import logger as app_log

    app_log.setLevel(logging.WARNING)
    url = urlparse(os.environ.get("DATABASE_URL", ""))
    if url.hostname not in ("localhost", "127.0.0.1"):
        print("DATABASE_URL must point at a throwaway local database (dialogs leave their salons behind)")
        return 2

    from app.ai.config import load_ai_config
    from app.ai.cooldown import CooldownBoard
    from app.ai.router import build_router
    from app.db.session import SessionLocal
    from evals.assistant.dialogs import DialogRunner, load_dialogs
    from evals.assistant.runner import _eval_config

    config = load_ai_config()
    if config.environment == "production":
        print("refusing to run against a production environment")
        return 2
    dialogs = [d for d in load_dialogs()
               if (not args.dialog or d.id == args.dialog) and (not args.locale or d.locale == args.locale)
               and (not args.tag or args.tag in d.tags)]
    if not dialogs:
        print("no dialog matches the filters")
        return 2
    known = {s.index: s for s in config.slots}
    missing = [n for n in args.slot if n not in known]
    if missing:
        print(f"slot(s) {missing} are not configured; configured: {sorted(known)}")
        return 2

    failed = False
    for n in args.slot:
        slot = known[n]
        if args.model:
            slot = replace(slot, model_fast=args.model, model_smart=args.model)
        router = build_router(replace(_eval_config(config), provider="pool", slots=(slot,)), board=CooldownBoard())
        tag = f"slot{n}" + (f"-{re.sub(r'[^A-Za-z0-9.]+', '-', args.model)}" if args.model else "")
        out = args.report_dir / tag
        out.mkdir(parents=True, exist_ok=True)
        runner = DialogRunner(router, session_factory=SessionLocal, pace=args.pace, max_wait=args.max_wait,
                              give_up=args.give_up)
        passed = total = errors = 0
        print(f"--> slot {n} ({slot.name} / {slot.model_fast}): {len(dialogs)} dialogs")
        try:
            for dialog in dialogs:
                path = out / f"{dialog.id}.json"
                if path.exists() and not args.rerun:
                    old = json.loads(path.read_text())
                    if old.get("status") in ("pass", "fail"):
                        total += 1
                        passed += old["status"] == "pass"
                        continue
                result = runner.run(dialog)
                data = {**asdict(result), "slot": n, "model": slot.model_fast}
                path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str))
                total += 1
                passed += result.status == "pass"
                errors += result.status == "error"
                mark = {"pass": "ok  ", "fail": "FAIL", "error": "ERR "}[result.status]
                print(f"    {mark} {dialog.id}" + (f": {'; '.join(result.reasons)[:300]}" if result.reasons else ""))
                if result.status == "error" and any("model unavailable" in r for r in result.reasons):
                    # a long rest means a used-up quota; the rest would only fail the same way
                    print("    the slot is out for longer than --max-wait - stopping it, rerun later to continue")
                    break
        finally:
            runner.close()
        pct = round(100 * passed / total, 1) if total else 0
        print(f"<-- slot {n}: {passed}/{total} passed ({pct} %), {errors} errors this run")
        failed |= passed != total
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
