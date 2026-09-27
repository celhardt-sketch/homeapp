"""Name-or-id lookup for dictated input ("jasmine rice", "the pink bathroom").

Case-insensitive, substring and fuzzy (difflib) matching with simple plural folding.
Exactly one confident match -> Match; several plausible -> Ambiguous; none -> NoMatch.
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

STOPWORDS = {"the", "a", "an", "of", "some", "my", "our", "room"}
CONFIDENT = 0.72
PLAUSIBLE = 0.55


def _singular(word: str) -> str:
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith(("ses", "xes", "shes", "ches")):
        return word[:-2]
    if len(word) > 2 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _tokens(text: str) -> list[str]:
    words = re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split()
    return [_singular(w) for w in words if w not in STOPWORDS] or words


def normalize(text: str) -> str:
    return " ".join(_tokens(text))


def similarity(query: str, name: str) -> float:
    q, n = normalize(query), normalize(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    qt, nt = set(q.split()), set(n.split())
    if qt <= nt or nt <= qt:  # "rice" in "jasmine rice"; "jasmine rice bag" covers "jasmine rice"
        return 0.9
    if q in n or n in q:
        return 0.85
    ratio = SequenceMatcher(None, q, n).ratio()
    # token-level fuzz catches misspellings inside multi-word names ("jasmin rise" ~ "jasmine rice")
    token_hits = sum(1 for a in qt if any(SequenceMatcher(None, a, b).ratio() >= 0.8 for b in nt))
    token_score = token_hits / max(len(qt), len(nt))
    return max(ratio, token_score * 0.9)


@dataclass
class Match:
    item: dict
    score: float


@dataclass
class Ambiguous:
    candidates: list[dict]


@dataclass
class NoMatch:
    nearest: list[dict]


def resolve(query: str | int, items: list[dict], name_key: str = "name") -> Match | Ambiguous | NoMatch:
    """`query` may be an id (int or digit string) or a spoken name."""
    text = str(query).strip()
    if text.isdigit():
        for it in items:
            if it.get("id") == int(text):
                return Match(it, 1.0)
    scored = sorted(((similarity(text, it[name_key]), it) for it in items), key=lambda s: -s[0])
    if not scored or scored[0][0] < PLAUSIBLE:
        return NoMatch([it for _, it in scored[:4]])
    top = scored[0][0]
    exact = [it for s, it in scored if s == 1.0]
    if len(exact) == 1:
        return Match(exact[0], 1.0)
    plausible = [it for s, it in scored if s >= PLAUSIBLE and s >= top - 0.15]
    if len(plausible) == 1 and top >= CONFIDENT:
        return Match(plausible[0], top)
    if len(plausible) == 1:  # a single weak hit: not confident enough to act on
        return NoMatch([it for _, it in scored[:4]])
    return Ambiguous(plausible[:6])
