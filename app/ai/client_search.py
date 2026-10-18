"""Finding the user's own clients by name, the same way for the model's context, the executor's
find_client and the eval scorer.

People type names the way the sentence needs them: "Запиши Анну Петрову", "Terezu Svobodovou",
"Svobodova" without the accent. So both sides get folded (lowercase, no accents, ё -> е) and words are
compared with a few common Russian/Czech case endings cut off. No NLP library - a short hand-made
suffix list is enough for names, and a salon has at most a few thousand clients, so it runs in Python.
"""
import re
import unicodedata
from functools import lru_cache
from typing import Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.core.permissions import get_accessible_client_ids
from app.db.models.client import Client

MIN_WORD = 3  # shorter words ("на", "do", "к") only ever match a whole word
MAX_TEXT_WORDS = 24
_WORD = re.compile(r"[^\W_]+", re.UNICODE)

# endings cut off when comparing; written the usual way and folded below (й -> и, ý -> y and so on)
_RU = ("иями ями ами иях ях ах ией ого его ому ему ыми ими ой ою ею ей ый ий ым им ом ем ую юю ая яя ые ие ых их "
       "ия ию ии ам ям а я у ю ы и е о ь").split()
_CZ = "ovi ech ami emi iho eho imu emu ých ými ou ým ím em um ie ii a u y e i o".split()

EXACT, STEM, PREFIX = 3, 2, 1

Row = tuple[int, str]


def fold(text) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).casefold()
    return "".join(ch for ch in text if not unicodedata.combining(ch))


_SUFFIXES = sorted({fold(s) for s in _RU + _CZ}, key=len, reverse=True)


def name_words(text) -> list[str]:
    return _WORD.findall(fold(text))


@lru_cache(maxsize=8192)
def _stems(word: str) -> frozenset:
    # the word itself plus every way to cut an ending off it. Keeping all of them (not just the
    # longest cut) is what lets "артем" meet "артема" and "сергеи" meet "сергея"
    out = {word}
    for s in _SUFFIXES:
        if word.endswith(s) and len(word) - len(s) >= 2:
            out.add(word[: -len(s)])
    return frozenset(out)


def word_match(q: str, n: str) -> int:
    """How well a folded query word fits a folded name word: EXACT, STEM, PREFIX or 0."""
    if q == n:
        return EXACT
    if len(q) < MIN_WORD or len(n) < MIN_WORD or q.isdecimal() or n.isdecimal():
        return 0
    a, b = _stems(q), _stems(n)
    if a & b:
        return STEM
    if len(q) >= 4 and n.startswith(q):
        return PREFIX
    return 0


def resolve_name(query: str, clients: Iterable[Row]) -> list[Row]:
    """Clients a find_client name means, best group only: one row = found, several = ambiguous.

    1. the exact full name (folded), if anybody has it
    2. a plain substring when one of its candidates also contains every whole query word
       ("Clarke" is Emma Clarke, while "Clark" still finds both Clark and Clarke)
    3. otherwise every query word fits a name word with only case endings removed
    4. finally a plain substring or partial name, if there is no stronger match
    A query that only fits some of the words finds nobody - that would be guessing.
    """
    qw = name_words(query)
    if not qw:
        return []
    rows = [(cid, name, name_words(name)) for cid, name in clients]
    exact = [r for r in rows if r[2] == qw]
    if exact:
        return _rows(exact)
    flat = " ".join(qw)
    plain = []
    if len(flat) >= MIN_WORD:
        plain = [r for r in rows if flat in " ".join(r[2]) and
                 all(q in r[2] for q in qw if q.isdecimal())]
        if any(all(q in r[2] for q in qw) for r in plain):
            return _rows(plain)
    stemmed = [r for r in rows if r[2] and all(any(word_match(q, n) >= STEM for n in r[2]) for q in qw)]
    if stemmed:
        return _rows(stemmed)
    if plain:
        return _rows(plain)
    return _rows([r for r in rows if r[2] and all(any(word_match(q, n) for n in r[2]) for q in qw)])


def _rows(rows) -> list[Row]:
    return sorted(((cid, name) for cid, name, _ in rows), key=lambda r: r[0])


def _contains(words: list[str], run: list[str]) -> bool:
    k = len(run)
    return bool(k) and any(words[i:i + k] == run for i in range(len(words) - k + 1))


def rank_in_text(text: str, clients: Iterable[Row], *, limit: int) -> list[int]:
    """Ids of clients a free-text request may mean, best first: the full name in the text, then
    every word of the name found, then more exact words, then more words; ties by id."""
    all_words = name_words(text)
    tw = list(dict.fromkeys(w for w in all_words if len(w) >= MIN_WORD and not w.isdecimal()))[:MAX_TEXT_WORDS]
    if not tw:
        return []
    scored = []
    for cid, name in clients:
        nw = name_words(name)
        levels = [max(word_match(q, n) for q in tw) for n in nw]
        hits = sum(1 for lv in levels if lv)
        if not hits:
            continue
        exact = sum(1 for lv in levels if lv == EXACT)
        key = (_contains(all_words, nw), hits == len(nw), exact, hits)
        scored.append((tuple(-int(k) for k in key), cid))
    scored.sort()
    return [cid for _, cid in scored[:limit]]


# --- the database side: the user's own clients only -------------------------------

def own_clients(db: Session, user, *, phone: Optional[str] = None, email: Optional[str] = None) -> list[Row]:
    """(id, full name) of the clients this user may see - same scope as the clients API,
    without deleted or archived ones."""
    ids = get_accessible_client_ids(db, user) or set()
    if not ids:
        return []
    q = db.query(Client.id, Client.full_name).filter(Client.id.in_(ids), Client.deleted_at.is_(None),
                                                     Client.archived_at.is_(None))
    if phone:
        q = q.filter(Client.contact_phone == phone)
    if email:
        q = q.filter(func.lower(Client.contact_email) == email.lower())
    return [(cid, name or "") for cid, name in q.order_by(Client.id).all()]


def find_clients(db: Session, user, *, name: Optional[str] = None, phone: Optional[str] = None,
                 email: Optional[str] = None) -> list[Row]:
    rows = own_clients(db, user, phone=phone, email=email)
    return resolve_name(name, rows) if name else rows


def clients_in_text(db: Session, user, text: str, *, limit: int) -> list[dict]:
    rows = own_clients(db, user)
    names = dict(rows)
    return [{"id": cid, "name": names[cid]} for cid in rank_in_text(text, rows, limit=limit)]
