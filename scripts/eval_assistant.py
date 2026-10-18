"""Runs the assistant phrase set (evals/assistant/cases) against the model slots and scores it.

Usage (from the repo root, with LLM_<N>_* set in .env or the environment, see evals/assistant/README.md):
    python scripts/eval_assistant.py                  # every configured slot, one after another
    python scripts/eval_assistant.py --slot 3 --tag attack --locale cz
    python scripts/eval_assistant.py --case ru-plan-example --all-slots
    python scripts/eval_assistant.py --fake           # no model: scorer self-test, must be 100%
    python scripts/eval_assistant.py --fake-bad       # no model: broken plans, must fail

The fixture salon is created in one transaction that is rolled back, so DATABASE_URL may point at any
dev database - nothing stays behind. Free tiers may keep prompts: the fixture is made-up data only.
Exit code: 0 everything passed, 1 something failed, 2 nothing to run (no slots, unknown slot or case).
"""
import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    which = p.add_mutually_exclusive_group()
    which.add_argument("--slot", type=int, action="append", metavar="N", help="run only slot N (repeatable)")
    which.add_argument("--all-slots", action="store_true", help="run every configured slot (the default)")
    which.add_argument("--fake", action="store_true", help="answer each case with its expected plan")
    which.add_argument("--fake-bad", action="store_true", help="answer each case with a broken plan")
    p.add_argument("--case", help="only this case id")
    p.add_argument("--tag", help="only cases with this tag, e.g. attack")
    p.add_argument("--locale", choices=("ru", "cz", "en"))
    p.add_argument("--role", choices=("fast", "smart"), default="fast", help="which model of the slot")
    p.add_argument("--max-wait", type=float, default=90.0, help="longest rate-limit pause to sit out, seconds")
    p.add_argument("--report-dir", type=Path, default=None, help="where the JSON report goes")
    p.add_argument("--verbose", action="store_true", help="keep the app's info logs")
    return p.parse_args(argv)


def main(argv=None, *, engine=None, log=print) -> int:
    args = parse_args(argv)
    from app.api.core.logger import logger as app_log  # sets its own level on import, so import first

    level = app_log.level
    if not args.verbose:
        # the app logs every plan at info level, which drowns the table
        app_log.setLevel(logging.WARNING)
    try:
        return _main(args, engine=engine, log=log)
    finally:
        app_log.setLevel(level)


def _main(args, *, engine, log) -> int:
    from app.ai.config import load_ai_config
    from evals.assistant import runner
    from evals.assistant.cases import load_cases, select

    config = load_ai_config()
    if config.environment == "production":
        log("refusing to run against a production environment")
        return 2
    cases = select(load_cases(), case_id=args.case, tag=args.tag, locale=args.locale)
    if not cases:
        log("no case matches the filters")
        return 2
    mode = "fake" if args.fake else "fake-bad" if args.fake_bad else None
    slots = []
    if mode is None:
        slots = list(config.slots)
        if not slots:
            log("No model slots are configured. Fill LLM_1_BASE_URL / LLM_1_API_KEY / LLM_1_MODEL (and so on "
                "up to LLM_30_*) in .env - see evals/assistant/README.md. Or try --fake.")
            return 2
        if args.slot:
            known = {s.index: s for s in slots}
            missing = [n for n in args.slot if n not in known]
            if missing:
                log(f"slot(s) {missing} are not configured; configured: {sorted(known)}")
                return 2
            slots = [known[n] for n in args.slot]

    reports = runner.run(cases, config=config, slots=slots, mode=mode, role=args.role, engine=engine,
                         max_wait=args.max_wait, log=log)
    log("")
    log(runner.format_table(reports))
    meta = {"mode": mode, "role": args.role, "case": args.case, "tag": args.tag, "locale": args.locale,
            "cases": [c.id for c in cases]}
    path = runner.write_report(reports, meta, args.report_dir or runner.REPORTS_DIR)
    log(f"\nreport: {path}")
    return 0 if all(r.passed == r.total for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
