"""Finding the user's own clients by name, the same way for the model's context, the executor's
find_client and the eval scorer.

People type names the way the sentence needs them: "Запиши Анну Петрову", "Terezu Svobodovou",
"Svobodova" without the accent. So both sides get folded (lowercase, no accents, ё -> е) and words are
compared with a few common Russian/Czech case endings cut off. No NLP library - a short hand-made
suffix list is enough for names, and a salon has at most a few thousand clients, so it runs in Python.

A Czech name typed in Cyrillic ("Мартина Новака") is compared letter by letter in Latin, and a Russian
name typed in Latin may use the Czech or the English spelling ("Kuznecova", "Kuznetsova"). Typos
("jhon smth") only help the model context find candidates - find_client never resolves on a typo,
the person picks the client from what the plan shows.
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

EXACT, STEM, PREFIX, TYPO = 4, 3, 2, 1
MIN_TYPO_WORD = 4  # "ana" vs "ann" is too short to call a typo

# Cyrillic -> Latin, Czech style, applied to folded words (so й is already и, ё is е)
_LATIN = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "z", "з": "z", "и": "i", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "ch", "ц": "c", "ч": "c", "ш": "s", "щ": "sc", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "ju",
    "я": "ja", "і": "i", "є": "je", "ґ": "g",
})

# the same, English style - "Kuznetsova", "Zhanna", "Yulia". ё is already е after folding, so the Latin side
# gets "yo"/"ye" turned into "e" before comparing ("Alyona", "Yelena")
_LATIN_EN = str.maketrans({
    **{chr(k): v for k, v in _LATIN.items()},
    "ж": "zh", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ю": "yu", "я": "ya",
})
_EN_DIGRAPHS = (("yo", "e"), ("ye", "e"))

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


def _cyrillic(word: str) -> bool:
    return any("\u0400" <= ch <= "\u04ff" for ch in word)


def _one_edit(a: str, b: str) -> bool:
    """One letter wrong, missing, extra, or two neighbours swapped ("jhon" / "john")."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return (len(diff) == 2 and diff[1] == diff[0] + 1
                and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(short) and short[i] == long_[i]:
        i += 1
    return short[i:] == long_[i + 1:]


def word_match(q: str, n: str, *, typos: bool = False) -> int:
    """How well a folded query word fits a folded name word: EXACT, STEM, PREFIX, TYPO or 0.
    TYPO only when asked for - that's for the model context, never for resolving a client."""
    if _cyrillic(q) != _cyrillic(n):
        best = 0
        for spell in (lambda w: w.translate(_LATIN), _english, _sounds):
            best = max(best, _match(spell(q), spell(n), typos))
            if best == EXACT:
                break
        return best
    return _match(q, n, typos)


_SOUNDS_LATIN = (("ck", "k"), ("ph", "f"), ("th", "t"), ("w", "v"), ("q", "k"), ("x", "ks"))


def _sounds(word: str) -> str:
    """A rough sound skeleton, for an English name typed in Cyrillic: "Джон Смит" - "John Smith",
    "Кларк" - "Clark". Only ever compared across alphabets."""
    if _cyrillic(word):
        word = word.translate(_LATIN_EN).replace("dzh", "dz")
    else:
        for latin, sound in _SOUNDS_LATIN:
            word = word.replace(latin, sound)
        word = re.sub(r"c(?!h)", "k", word).replace("j", "dz")
        word = re.sub(r"(?<![cszk])h", "", word)
    word = word.replace("y", "i")
    return re.sub(r"(.)\1+", r"\1", word)


def _english(word: str) -> str:
    if _cyrillic(word):
        return word.translate(_LATIN_EN)
    for latin, plain in _EN_DIGRAPHS:
        word = word.replace(latin, plain)
    return word


def _match(q: str, n: str, typos: bool) -> int:
    if q == n:
        return EXACT
    if len(q) < MIN_WORD or len(n) < MIN_WORD or q.isdecimal() or n.isdecimal():
        return 0
    a, b = _stems(q), _stems(n)
    if a & b:
        return STEM
    if len(q) >= 4 and n.startswith(q):
        return PREFIX
    if typos and min(len(q), len(n)) >= MIN_TYPO_WORD and _one_edit(q, n):
        return TYPO
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


# everyday words of a request ("move her to the same time", "перенеси её на завтра") - never a sign of
# a client, even when somebody's card has a sentence in the name field
_PLAIN = frozenset(fold(w) for w in """
the and for with her him his she they them this that these those after before same time day today tomorrow
tonight morning evening afternoon week next from into onto please visit appointment booking book move cancel
change make put add also too instead again yes not but can could would should will just then there here
она его её ему ей они них это эта этот тот той том тому там тоже ещё еще после перед время день утро вечер
неделя сегодня завтра послезавтра запиши записать перенеси перенести отмени отменить визит запись
пожалуйста нет да лучше давай так также
ona její jeho jim ten tom tím také ještě před čas den ráno večer týden dnes zítra pozítří objednej
přesuň zruš návštěva rezervace prosím ano radši taky stejný stejně""".split())


def rank_in_text(text: str, clients: Iterable[Row], *, limit: int) -> list[int]:
    """Ids of clients a free-text request may mean, best first: the full name in the text, then
    every word of the name found without typos, then with typos, then more exact words, then more
    words; ties by id."""
    all_words = name_words(text)
    tw = list(dict.fromkeys(w for w in all_words if len(w) >= MIN_WORD and not w.isdecimal()
                            and w not in _PLAIN))[:MAX_TEXT_WORDS]
    if not tw:
        return []
    scored = []
    for cid, name in clients:
        nw = name_words(name)
        levels = [max(word_match(q, n, typos=True) for q in tw) for n in nw]
        hits = sum(1 for lv in levels if lv)
        if not hits:
            continue
        exact = sum(1 for lv in levels if lv == EXACT)
        strong = sum(1 for lv in levels if lv > TYPO)
        key = (_contains(all_words, nw), strong == len(nw), hits == len(nw), exact, strong, hits)
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


# --- "is this maybe somebody we already have" - looser on purpose ------------------

_LOOSE_ENDINGS = sorted({fold(s) for s in "ovou ová ové ovi ova ou a u y e i o я а у ю ы и е о ой ую ей".split()},
                        key=len, reverse=True)
MIN_SKELETON = 4


def _skeleton(word: str) -> str:
    if _cyrillic(word):
        word = word.translate(_LATIN)
    for ending in _LOOSE_ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[: -len(ending)]
    return word


def _alike(a: str, b: str) -> bool:
    x, y = _skeleton(a), _skeleton(b)
    if x == y:
        return True
    short, long_ = sorted((x, y), key=len)
    return (len(short) >= MIN_SKELETON and long_.startswith(short)) or (
        min(len(x), len(y)) >= 3 and _one_edit(x, y))


def similar_names(name: str, clients: Iterable[Row]) -> list[Row]:
    """Clients whose name looks like a new card's name: every word of it fits a word of theirs,
    allowing other case endings, Czech "-ová", the other alphabet and one typo. Only for asking
    "is it her?" before a duplicate card is made - never to pick a client."""
    words = [w for w in name_words(name) if not w.isdecimal()]
    if not words:
        return []
    out = []
    for cid, full in clients:
        theirs = name_words(full)
        if theirs and all(any(_alike(w, t) for t in theirs) for w in words):
            out.append((cid, full))
    return out
