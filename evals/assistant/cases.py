"""Loading the phrase set (plan А4) and the attack cases (plan Е) from evals/assistant/cases/*.yaml.

A file has optional `defaults` (locale, now, tz) and a list of `cases`. A case is:

    id, locale, text, now (local, no offset), tz, tags,
    assistant_name   - optional, set on the fixture business for this case only
    expect           - what the answer must look like (see scoring.py), or a list of alternatives
    plan             - one good answer; --fake replies with it, and it must pass its own expect

Fixture things are named by key: "@client.anna_petrova" in expect/plan becomes the id,
"{visit.b_visit}" in the text becomes the id as text.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import yaml

CASES_DIR = Path(__file__).resolve().parent / "cases"
LOCALES = ("ru", "cz", "en")
_ID = re.compile(r"^[a-z]{2}-[a-z0-9-]{2,40}$")
_REF = re.compile(r"^@([a-z]+\.[a-z0-9_]+)$")
_TEXT_REF = re.compile(r"\{([a-z]+\.[a-z0-9_]+)\}")
EXPECT_KEYS = {"actions", "allow_extra", "any_order", "questions", "no_actions", "read_only", "forbid_actions",
               "forbid_in_actions", "forbid_in_reply"}


class CaseError(ValueError):
    pass


@dataclass
class Case:
    id: str
    locale: str
    text: str
    now: str
    tz: str
    expect: list  # alternatives, each a dict
    plan: dict
    tags: list = field(default_factory=list)
    assistant_name: Optional[str] = None
    source: str = ""

    @property
    def now_utc(self) -> datetime:
        """Naive UTC, what make_plan(now=...) takes."""
        local = datetime.fromisoformat(self.now).replace(tzinfo=ZoneInfo(self.tz))
        return local.astimezone(timezone.utc).replace(tzinfo=None)

    def text_for(self, ids: dict[str, int]) -> str:
        return _TEXT_REF.sub(lambda m: str(_lookup(ids, m.group(1), self.id)), self.text)

    def keys_used(self) -> set[str]:
        found = set(_TEXT_REF.findall(self.text))
        _walk_refs([self.expect, self.plan, self.assistant_name], found)
        return found


def _lookup(ids: dict, key: str, case_id: str) -> int:
    if key not in ids:
        raise CaseError(f"{case_id}: unknown fixture key {key!r}")
    return ids[key]


def _walk_refs(obj: Any, found: set) -> None:
    if isinstance(obj, str):
        m = _REF.match(obj)
        if m:
            found.add(m.group(1))
    elif isinstance(obj, dict):
        for v in obj.values():
            _walk_refs(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _walk_refs(v, found)


def resolve(obj: Any, ids: dict[str, int], case_id: str = "?") -> Any:
    """Copy of obj with every "@kind.key" string replaced by its id."""
    if isinstance(obj, str):
        m = _REF.match(obj)
        return _lookup(ids, m.group(1), case_id) if m else obj
    if isinstance(obj, dict):
        return {k: resolve(v, ids, case_id) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve(v, ids, case_id) for v in obj]
    return obj


def _plain(obj: Any) -> Any:
    # YAML turns 2026-10-22 into a date; keep everything as the strings JSON would carry
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj


def case_from_dict(raw: dict, defaults: Optional[dict] = None, source: str = "inline") -> Case:
    raw = {**_plain(defaults or {}), **_plain(raw)}
    cid = raw.get("id") or "?"
    missing = [k for k in ("id", "locale", "text", "now", "tz", "expect", "plan") if k not in raw]
    if missing:
        raise CaseError(f"{source}:{cid}: missing {', '.join(missing)}")
    if not _ID.match(cid):
        raise CaseError(f"{source}:{cid}: id looks like ru-something-01")
    if raw["locale"] not in LOCALES:
        raise CaseError(f"{cid}: locale must be one of {LOCALES}")
    text = raw["text"]
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise CaseError(f"{cid}: text must be 1..2000 characters")
    try:
        ZoneInfo(raw["tz"])
        parsed = datetime.fromisoformat(raw["now"])
    except Exception as exc:
        raise CaseError(f"{cid}: bad now/tz ({exc})") from None
    if parsed.tzinfo is not None:
        raise CaseError(f"{cid}: now is local time without an offset, tz says where")
    expect = raw["expect"] if isinstance(raw["expect"], list) else [raw["expect"]]
    for alt in expect:
        if not isinstance(alt, dict) or not alt:
            raise CaseError(f"{cid}: expect must be a mapping or a list of them")
        unknown = set(alt) - EXPECT_KEYS
        if unknown:
            raise CaseError(f"{cid}: unknown expect keys {sorted(unknown)}")
    if not isinstance(raw["plan"], dict):
        raise CaseError(f"{cid}: plan must be a mapping")
    tags = list(raw.get("tags") or [])
    name = raw.get("assistant_name")
    if name is not None and (not isinstance(name, str) or len(name) > 30):
        raise CaseError(f"{cid}: assistant_name is up to 30 characters")
    return Case(id=cid, locale=raw["locale"], text=text, now=raw["now"], tz=raw["tz"], expect=expect,
                plan=raw["plan"], tags=tags, assistant_name=name, source=source)


def load_file(path: Path) -> list[Case]:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        raise CaseError(f"{path.name}: expected a mapping with a 'cases' list")
    defaults = _plain(data.get("defaults") or {})
    return [case_from_dict(raw, defaults, path.name) for raw in data["cases"]]


def load_cases(directory: Path = CASES_DIR) -> list[Case]:
    cases: list[Case] = []
    for path in sorted(directory.glob("*.yaml")):
        cases.extend(load_file(path))
    seen = set()
    for c in cases:
        if c.id in seen:
            raise CaseError(f"duplicate case id {c.id}")
        seen.add(c.id)
    return cases


def select(cases: list[Case], *, case_id: Optional[str] = None, tag: Optional[str] = None,
           locale: Optional[str] = None) -> list[Case]:
    out = cases
    if case_id:
        out = [c for c in out if c.id == case_id]
    if tag:
        out = [c for c in out if tag in c.tags]
    if locale:
        out = [c for c in out if c.locale == locale]
    return out
