"""Siri Shortcut: one dictated sentence becomes an item on someone's list.

"Susan: call the plumber Friday", "remind Magnus to renew the registration",
"buy milk, eggs and diapers", "add return the library books to Vanessa's list".
This is plain pattern matching, nothing clever. If a name isn't recognised, the reply asks
about it and nothing is added (see AGENTS.md, name matching).
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .matching import CONFIDENT, Ambiguous, Match, resolve  # noqa: F401  (CONFIDENT: duplicate check)

SELF = "__self__"
SELF_WORDS = {"me", "my", "myself", "mine", "i"}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MAX_TITLE = 120
EXAMPLE = "Try: \"Susan: call the plumber\", \"remind me to renew the registration Friday\" or \"buy milk and eggs\"."

_BUY = re.compile(
    r"\b(buy|purchase|order|shop for|restock|groceries|from (the )?(store|grocery store|pharmacy|costco|target|walmart|amazon))\b",
    re.I,
)
_BUY_PREFIX = re.compile(r"^(?:buy|purchase|order|shop for|restock|we need|i need)\s+(?:some\s+|more\s+)?", re.I)
_SHOP_MARKER = re.compile(r"^(?:buy|to buy|shopping|shopping list|groceries|grocery|grocery list)\s*[:,;]\s*", re.I)
_LEAD = re.compile(r"^(?:(?:hey|ok|okay|please|and)\s*,?\s+)+", re.I)
_DUE = re.compile(
    r"[\s,]+(?:(by|on|due|this|next|before)\s+)?(today|tonight|tomorrow|" + "|".join(WEEKDAYS) + r")$", re.I
)
_LIST = r"(?:(to-?do|to do|shopping|to-?buy|to buy|grocery)\s+)?list"
_SHOP_LIST = re.compile(r"shopping|buy|grocery", re.I)

# (pattern, groups -> (name, body, list kind), fuzzy) in the order they're tried. A fuzzy pattern
# is clearly naming a person, so an unknown name gets a question back; the others only count
# when the word is exactly someone's name (so "milk, eggs" isn't read as a person called Milk).
_PATTERNS = [
    (re.compile(r"^(?:add|put)?\s*(.+?)\s+(?:to|on|onto)\s+(?:the|our)\s+" + _LIST + r"$", re.I), lambda m: (None, m[1], m[2]), False),
    (re.compile(r"^(?:add|put)?\s*(.+?)\s+(?:to|on|onto)\s+([a-z]+?)(?:'s|s')?\s+" + _LIST + r"$", re.I), lambda m: (m[2], m[1], m[3]), True),
    (re.compile(r"^(?:remind|tell|ask)\s+([a-z]+)\s+(?:to\s+|about\s+)?(.+)$", re.I), lambda m: (m[1], m[2], None), True),
    (re.compile(r"^([a-z]+?)(?:'s)?(?:\s+" + _LIST + r")?\s*:\s*(.+)$", re.I), lambda m: (m[1], m[3], m[2]), True),
    (re.compile(r"^([a-z]+?)(?:'s)?\s+" + _LIST + r"\s*[:,]?\s+(.+)$", re.I), lambda m: (m[1], m[3], m[2]), True),
    (re.compile(r"^([a-z]+?)(?:'s)?\s*[,;-]\s*(.+)$", re.I), lambda m: (m[1], m[2], None), False),
    (re.compile(r"^([a-z]+)\s+(?:needs?|has|have|got)\s+to\s+(.+)$", re.I), lambda m: (m[1], m[2], None), False),
    (re.compile(r"^(.+?)\s+for\s+([a-z]+)$", re.I), lambda m: (m[2], m[1], None), False),
    (re.compile(r"^([a-z]+)\s+(.+)$", re.I), lambda m: (m[1], m[2], None), False),
]


@dataclass
class Parsed:
    person: str  # a household name exactly as stored, or SELF
    items: list[str] = field(default_factory=list)
    shopping: bool = False
    due_on: date | None = None


def _due(text: str, today: date) -> tuple[str, date | None]:
    m = _DUE.search(text)
    if not m or not text[: m.start()].strip():
        return text, None
    word, prefix = m[2].lower(), (m[1] or "").lower()
    if word in ("today", "tonight"):
        d = today
    elif word == "tomorrow":
        d = today + timedelta(days=1)
    else:
        ahead = (WEEKDAYS.index(word) - today.weekday()) % 7
        d = today + timedelta(days=ahead or (7 if prefix == "next" else 0))
    return text[: m.start()].rstrip(" ,"), d


def _person(word: str, names: list[str], fuzzy: bool) -> str | None:
    """Canonical name, SELF, None (not a name; only for non-fuzzy patterns), or raises Reply."""
    w = word.strip().lower()
    if w in SELF_WORDS:
        return SELF
    for n in names:
        if n.lower() == w:
            return n
    if not fuzzy:
        return None
    people = [{"id": i, "name": n} for i, n in enumerate(names)]
    res = resolve(word, people)
    if isinstance(res, Match):
        return res.item["name"]
    everyone = ", ".join(names)
    if isinstance(res, Ambiguous):
        options = " or ".join(c["name"] for c in res.candidates)
        raise Reply(f"Did you mean {options}? Nothing was added. Say it again with their name.")
    hint = f" Did you mean {res.nearest[0]['name']}?" if res.nearest else ""
    raise Reply(f"I don't know anyone called {word.strip().title()}.{hint} Lists are: {everyone}. Nothing was added.")


class Reply(Exception):
    """A sentence to say back instead of adding anything."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _split(body: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\s*,\s*", body) if p.strip()]
    if len(parts) > 1:
        last = re.split(r"\s+and\s+", parts.pop(), flags=re.I)
        parts.extend(p.strip() for p in last if p.strip())
    return [re.sub(r"^and\s+", "", p, flags=re.I) for p in parts]


def _tidy(item: str) -> str:
    item = item.strip(" .,!?:;-\"'")
    return item[:1].upper() + item[1:]


def parse(text: str, names: list[str], today: date) -> Parsed:
    """Raises Reply when the sentence can't be acted on."""
    s = (text or "").replace("\u2019", "'").replace("\u2018", "'").strip()
    s = _LEAD.sub("", s).strip().rstrip(".!?:;,-").strip()
    if not s:
        raise Reply("I didn't catch anything to add. " + EXAMPLE)
    alone = _person(re.sub(r"(?:'s)?(?:\s+" + _LIST + r")?$", "", s, flags=re.I), names, fuzzy=False)
    if alone is not None:
        whose = "your" if alone == SELF else f"{alone}'s"
        raise Reply(f"What should go on {whose} list? Nothing was added. " + EXAMPLE)
    s, due = _due(s, today)
    shopping = False
    m = _SHOP_MARKER.match(s)
    if m:
        shopping, s = True, s[m.end():]

    person, body = SELF, s
    for pattern, groups, fuzzy in _PATTERNS:
        m = pattern.match(s)
        if not m:
            continue
        name, rest, list_kind = groups(m)
        who = SELF if name is None else _person(name, names, fuzzy)
        if who is None or not rest.strip():
            continue
        person, body = who, rest
        if list_kind and _SHOP_LIST.search(list_kind):
            shopping = True
        break

    if due is None:
        body, due = _due(body, today)
    body = re.sub(r"^(?:to|that)(?:\s+|$)", "", body.strip(), flags=re.I)
    if not body.strip(" .,!?"):
        raise Reply("I didn't catch what to add. " + EXAMPLE)
    shopping = shopping or bool(_BUY.search(body))
    if shopping:
        body = _BUY_PREFIX.sub("", body)
        items = [_tidy(p) for p in _split(body)]
    else:
        items = [_tidy(body)]
    items = [i for i in items if i]
    if not items:
        raise Reply("I didn't catch what to add. " + EXAMPLE)
    return Parsed(person=person, items=items, shopping=shopping, due_on=due)


def join_names(items: list[str]) -> str:
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]
