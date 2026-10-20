import re
import unicodedata

_CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "і": "i", "ї": "yi", "є": "ye", "ґ": "g",
}

SLUG_MAX_LENGTH = 64
SLUG_MIN_LENGTH = 3
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _ascii_fold(value: str) -> str:
    value = "".join(_CYRILLIC.get(ch, ch) for ch in value.lower())
    value = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def _hyphenate(value: str) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", value)).strip("-")


def normalize_slug(value: str) -> str:
    """What the owner typed, reduced to the one shape a public URL can safely use."""
    return _hyphenate(_ascii_fold(str(value)))


def slugify_business_name(name: str) -> str:
    base = _hyphenate(_ascii_fold(name or ""))[: SLUG_MAX_LENGTH - 9].strip("-")
    return base or "salon"


def is_valid_slug(value: str) -> bool:
    return (
        SLUG_MIN_LENGTH <= len(value) <= SLUG_MAX_LENGTH
        and SLUG_PATTERN.fullmatch(value) is not None
    )
