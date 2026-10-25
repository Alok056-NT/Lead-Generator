"""Message quality checks M01-M17 (PLAN section 14).

`check_message` is pure: it gets the message, the person, the company, the campaign, the offer
profile and an index of the campaign's other messages, and returns a list of `Issue`
(`qa_leads.Issue`; `.as_dict()` gives `{code, severity, detail}`). `drafting.save_drafts` calls it
at once when a draft is saved and `review.run_qa` calls it again for the whole campaign.
`CHANNEL_SPECS` in `drafting.py` stays the one place for the channel limits.

M16 (AI review missing) is not part of `check_message`: it does not make a draft fail. Use
`review_pending_issue` for it; `review.run_qa` and the export gate do.

Number, brand and title checks are heuristics: they warn or block on what they can see in the
text and never try to judge meaning. The AI review reads every message with its evidence.
"""

from __future__ import annotations

import functools
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from leadgen import db
from leadgen.core import countries as countries_mod
from leadgen.core.text import normalize_whitespace, similarity, truncate
from leadgen.services import drafting
from leadgen.services.qa_leads import Issue

MESSAGE_SEVERITY: dict[str, str] = {
    "M01": "block", "M02": "block", "M03": "block", "M04": "block", "M05": "block",
    "M06": "block", "M07": "warn", "M08": "warn", "M09": "block", "M10": "warn",
    "M11": "warn", "M12": "warn", "M13": "warn", "M14": "warn", "M15": "warn", "M16": "block",
    "M17": "block",
}  # fmt: skip

MESSAGE_TITLES: dict[str, str] = {
    "M01": "Greeting does not use the greeting name",
    "M02": "Company not mentioned, or another campaign company mentioned",
    "M03": "Unresolved placeholder",
    "M04": "Outside the channel length limits",
    "M05": "Forbidden claim or banned phrase",
    "M06": "Number not in the offer's proof points",
    "M07": "Brand or client name not in the offer",
    "M08": "Spam-trigger words",
    "M09": "Subject line rules",
    "M10": "No question or clear call to action at the end",
    "M11": "Link in a LinkedIn message",
    "M12": "Near-duplicate of another message",
    "M13": "Spelling does not match the locale",
    "M14": "Job title differs from the lead's title",
    "M15": "Exclamation marks, emoji or shouting",
    "M16": "AI review missing",
    "M17": "Sign-off block missing or wrong",
}  # fmt: skip

SIMILARITY_LIMIT = 0.92
# M12 speed: texts that share fewer than this part of their three-word runs cannot be 92% alike,
# and only the closest few survivors are compared character by character.
PRUNE_OVERLAP = 0.35
PRUNE_MIN_GRAMS = 4
FULL_COMPARES = 4

# --- constants (patterns are described next to each) --------------------------------------------

# M03: placeholders. TODO/TBC/TBD/XXX only in capitals, so Spanish "todo" is fine.
_PLACEHOLDER_RE = re.compile(
    r"\{\{|\}\}|\{[^{}\s][^{}]*\}|\[[^\]\n]{1,60}\]|<[^<>\n]{1,60}>|"
    r"(?-i:\b(?:TODO|TBC|TBD)\b)|\blorem ipsum\b|(?-i:\bX{3,}\b)",
    re.IGNORECASE,
)

# M05: phrases no message may use, whatever the offer says.
GLOBAL_FORBIDDEN: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern, re.IGNORECASE))
    for label, pattern in (
        ("guarantee", r"\bguarantee[ds]?\b|\bguaranteeing\b"),
        ("100%", r"100\s?%|\bhundred percent\b"),
        ("risk-free", r"\brisk[- ]free\b"),
        ("no-brainer", r"\bno[- ]brainer\b"),
        ("act now", r"\bact now\b"),
        ("limited time", r"\blimited[- ]time\b"),
    )
)
_STOPWORDS = frozenset(
    "a an the and or of to in on at for with by we our you your is are was be it this that as "
    "from have has can will i us".split()
)

# M06: numbers, percentages and money. Currency symbols are read but not compared (a price is
# only ever allowed by a proof point); a unit of k/m/bn multiplies the value, "%" / "percent" and
# "x" / "times" are kept apart from plain numbers.
_NUMBER_RE = re.compile(
    r"(?<![\w.])(?P<cur>S\$|US\$|A\$|NZ\$|HK\$|[$\u00a3\u20ac\u20b9])?\s?(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"(?!\d)(?:\s?(?P<unit>%|percent\b|per cent\b|x\b|times\b|k\b|mn\b|m\b|bn\b|b\b))?"
    r"(?!\s?(?:st|nd|rd|th)\b)",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"(?:https?://|www\.)\S+|\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE)
_MULTIPLIER = {"k": 1e3, "m": 1e6, "mn": 1e6, "b": 1e9, "bn": 1e9}

# M07: a run of two or more capitalised words is read as a possible brand or client name.
# Sentence starters and sign-off words are trimmed from either end first.
_BRAND_RE = re.compile(r"\b[A-Z][\w'\u2019\-]*(?:[ \t]+(?:&[ \t]+)?[A-Z][\w'\u2019\-]*)+")
_BRAND_TRIM = frozenset(
    "hi hello hey dear thanks thank kind best warm regards cheers sincerely would could should "
    "are is if we our i the a an in at on for with it this that glad hope happy please also and "
    "but so as my your you do does can will may might from by to of or not no yes when while "
    "monday tuesday wednesday thursday friday saturday sunday january february march april may "
    "june july august september october november december".split()
)

# M08: wording that trips spam filters.
SPAM_WORDS = (
    "free", "winner", "cash", "earn money", "make money", "no obligation", "no cost", "buy now",
    "order now", "click here", "urgent", "congratulations", "exclusive offer", "discount",
    "cheap", "lowest price", "special promotion", "once in a lifetime", "double your",
    "dear friend", "amazing deal", "incredible deal", "$$$", "!!!",
)  # fmt: skip
_SPAM_RE = re.compile(
    "|".join(
        (r"(?<!\w)" + re.escape(w) + r"(?!\w)") if w[0].isalnum() else re.escape(w)
        for w in SPAM_WORDS
    ),
    re.IGNORECASE,
)

_FEEL_FREE_RE = re.compile(r"(?<!\w)feel\s+free(?!\w)", re.IGNORECASE)

# M09
_SUBJECT_FAKE_RE = re.compile(r"^\s*(?:re|fwd?|fw)\s*:", re.IGNORECASE)

# M10: how a message may close.
_SIGNOFF_RE = re.compile(
    r"^(?:kind regards|best regards|warm regards|regards|best wishes|best|thanks|thank you|"
    r"cheers|sincerely|yours sincerely|many thanks|warmly)\b[,.!]?$",
    re.IGNORECASE,
)
_CTA_RE = re.compile(
    r"\b(?:let me know|happy to|glad to|feel free|reply|get in touch|send (?:you|over)|share|"
    r"book|schedule|call|chat|connect|talk|discuss|open to|if (?:useful|helpful|relevant)|"
    r"would you|are you|do you|can i|could i|shall i)\b",
    re.IGNORECASE,
)

# M11: a link (scheme, www. or a bare domain with a common ending).
_LINK_RE = re.compile(
    r"https?://\S+|\bwww\.\S+|(?<![@\w.-])[a-z0-9-]+\.(?:com|net|org|io|co|ai|sg|uk|in|app|dev|"
    r"biz|info|ly|me)\b(?:/\S*)?",
    re.IGNORECASE,
)

# M13: British and American spellings (see `_build_locale_words`).
_ISE_STEMS = (
    "organis recognis realis specialis customis prioritis summaris minimis maximis optimis "
    "utilis standardis finalis personalis apologis modernis familiaris capitalis categoris "
    "centralis digitalis"
).split()  # fmt: skip
_ISE_ENDINGS = ("e", "es", "ed", "ing", "ation", "ations", "er", "ers")
_OUR_STEMS = "colour behaviour favour honour labour neighbour humour flavour".split()
_OUR_ENDINGS = ("", "s", "ed", "ing", "ful", "able", "ite", "ites")
_BRITISH_REGIONS = frozenset(
    "GB UK AU NZ IE IN SG HK ZA MY PK KE NG MT CY".split()
)  # fmt: skip
_AMERICAN_REGIONS = frozenset({"US", "PH", "PR"})
# US words that British English also uses for another meaning: warned, with a note.
_AMBIGUOUS_US = frozenset({"program", "programs", "license", "licenses"})

# M14
_RANK_CLASSES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("exec", re.compile(
        r"\b(?:chief(?:\s+\w+)?\s+officer|ceo|coo|cfo|cto|chro|cmo|cio|clo|president|founder|"
        r"co-founder|owner|chairman|managing\s+director|general\s+manager)\b", re.I)),
    ("senior", re.compile(r"\b(?:vice[- ]president|vp|svp|evp|head|director)\b", re.I)),
    ("manager", re.compile(r"\bmanager\b", re.I)),
)  # fmt: skip
_ROLE_PHRASE_RE = re.compile(
    r"\b(?P<rank>vice[- ]president|vp|svp|evp|head|director|manager|president|"
    r"chief(?:\s+\w+)?\s+officer)\s+(?:of|for)\s+(?P<rest>(?:[A-Za-z&]+[ ,]*){1,5})",
    re.IGNORECASE,
)
_ROLE_STOP = frozenset(
    "at in who which that to with from on for as is are you your where so but then so we our "
    "the a an his her their it this".split()
)
_ACRONYMS = (
    (re.compile(r"\bl\s*&\s*d\b", re.I), "learning and development"),
    (re.compile(r"\bh\s*&\s*s\b", re.I), "health and safety"),
    (re.compile(r"\behs\b", re.I), "environment health safety"),
    (re.compile(r"\bhr\b", re.I), "human resources"),
    (re.compile(r"\bit\b"), "information technology"),
)

# M15
_EMOJI_RE = re.compile(
    "[\U0001f300-\U0001faff\U0001f1e6-\U0001f1ff\u2600-\u27bf\u2b50\u2b55\ufe0f]"
)

_EXACT_CONNECTORS = frozenset({"of", "the"})
_LEGAL = frozenset(
    "ltd limited pte inc incorporated llc plc corp corporation co company gmbh ag sa bv nv pty "
    "llp bhd sdn pvt private".split()
)


# --- helpers ------------------------------------------------------------------------------------


def _issue(code: str, detail: str, severity: str | None = None) -> Issue:
    return Issue(code, severity or MESSAGE_SEVERITY[code], detail)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9%$\u00a3\u20ac\u20b9]+", text.casefold())


def _strings(value: Any) -> Iterable[str]:
    """Every string inside nested lists and dicts."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _first_line(body: str) -> str:
    for line in body.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _company_names(company: Mapping[str, Any]) -> list[str]:
    """The company's name, the name without legal endings, and its aliases (longest first)."""
    aliases = company.get("aliases")
    if aliases is None:
        aliases = company.get("aliases_json") or []
    names: dict[str, None] = {}
    for raw in (company.get("name"), *aliases):
        name = normalize_whitespace(str(raw or ""))
        if not name:
            continue
        names.setdefault(name, None)
        words = name.split()
        while len(words) > 1 and words[-1].strip(".,").casefold() in _LEGAL:
            words.pop()
        names.setdefault(" ".join(words), None)
    return sorted(names, key=len, reverse=True)


@functools.lru_cache(maxsize=8192)
def _name_pattern(name: str, exact: bool = False) -> re.Pattern[str] | None:
    """Whole-word pattern for a company name: `&` and `and` interchangeable, spaces flexible.
    Short all-caps names (`ADP`) are case-sensitive. With `exact`, every name is: each content
    word must be written as in the name, in ALL CAPS, or with a capital first letter ("Dhl" for
    "DHL", "Acme Freight" for a stored "acme freight"), so ordinary lower-case words ("a total of")
    do not match. Connector words (and, &, of, the) match in any case."""
    words = re.findall(r"[^\W_][\w'’]*|&", name)
    if not words:
        return None
    parts = []
    for w in words:
        if w == "&" or w.lower() == "and":
            parts.append(r"(?i:&|and)" if exact else r"(?:&|and)")
        elif exact and w.lower() in _EXACT_CONNECTORS:
            parts.append(f"(?i:{re.escape(w)})")
        elif exact:
            first = w[0].upper() if len(w[0].upper()) == 1 else w[0]
            parts.append(f"(?:{re.escape(w)}|{re.escape(first)}(?i:{re.escape(w[1:])}))")
        else:
            parts.append(re.escape(w))
    flags = 0 if (exact or (len(name) <= 5 and name.isupper())) else re.IGNORECASE
    return re.compile(r"(?<!\w)" + r"[\s\-]+".join(parts) + r"(?!\w)", flags)


def _mentions(text: str, names: Iterable[str], exact: bool = False) -> bool:
    for name in names:
        pattern = _name_pattern(name, exact)
        if pattern and pattern.search(text):
            return True
    return False


def _mask(text: str, names: Iterable[str], token: str = " ") -> str:
    for name in names:
        pattern = _name_pattern(name)
        if pattern:
            text = pattern.sub(token, text)
    return text


def _campaign_companies(campaign_id: Any) -> list[dict[str, Any]]:
    if not isinstance(campaign_id, int):
        return []
    with db.session() as conn:
        return db.query(conn, "SELECT * FROM companies WHERE campaign_id = ?", (campaign_id,))


# --- similarity index (M12) ---------------------------------------------------------------------


def _grams_of(text: str) -> frozenset[str]:
    """The set of three-word runs in `text` (a cheap fingerprint to find look-alikes)."""
    words = text.split()
    return frozenset(" ".join(words[i : i + 3]) for i in range(max(0, len(words) - 2)))


@dataclass
class IndexEntry:
    """One message in the campaign index used for the near-duplicate check (M12). `text` is the
    normalised text (see `similarity_text`); `grams` is filled in from it."""

    id: int
    channel: str
    text: str
    grams: frozenset[str] = field(default=frozenset(), compare=False, repr=False)

    def __post_init__(self) -> None:
        if not self.grams:
            self.grams = _grams_of(self.text)


def similarity_text(
    subject: str, body: str, greeting: str, company: Mapping[str, Any], full_name: str = ""
) -> str:
    """The message with the greeting name, the person's name and the company name replaced by
    tokens, so two messages that differ only in names compare as equal."""
    text = f"{subject}\n{body}"
    text = _mask(text, _company_names(company), " COMPANY ")
    for name in (full_name, greeting):
        if name.strip():
            text = _mask(text, [name], " NAME ")
    return normalize_whitespace(text).casefold()


def index_entry(
    message: Mapping[str, Any], person: Mapping[str, Any], company: Mapping[str, Any]
) -> IndexEntry:
    """The index entry for one stored message (see `build_index`)."""
    greeting = str(person.get("greeting_name") or person.get("first_name") or "")
    full = normalize_whitespace(f"{person.get('first_name') or ''} {person.get('last_name') or ''}")
    return IndexEntry(
        id=int(message["id"]),
        channel=str(message["channel"]),
        text=similarity_text(
            str(message.get("subject") or ""),
            str(message.get("body") or ""),
            greeting,
            company,
            full,
        ),
    )


def build_index(campaign_id: int) -> dict[int, IndexEntry]:
    """Index of the campaign's messages (message id -> entry), rejected messages left out."""
    with db.session() as conn:
        rows = db.query(
            conn,
            "SELECT m.id AS id, m.channel AS channel, m.subject AS subject, m.body AS body, "
            "p.greeting_name AS greeting_name, p.first_name AS first_name, "
            "p.last_name AS last_name, c.name AS name, c.aliases_json AS aliases_json "
            "FROM messages m JOIN people p ON p.id = m.person_id "
            "JOIN companies c ON c.id = p.company_id "
            "WHERE p.campaign_id = ? AND m.status != 'rejected' ORDER BY m.id",
            (campaign_id,),
        )
    index: dict[int, IndexEntry] = {}
    for r in rows:
        company = {"name": r["name"], "aliases_json": r["aliases_json"]}
        index[r["id"]] = index_entry(r, r, company)
    return index


def _most_similar(
    text: str, channel: str, own_id: Any, index: Mapping[int, IndexEntry]
) -> tuple[float, int | None]:
    best, best_id = 0.0, None
    if not text:
        return best, best_id
    grams = _grams_of(text)
    candidates: list[tuple[float, IndexEntry]] = []
    for entry in index.values():
        if entry.channel != channel or entry.id == own_id or not entry.text:
            continue
        if 2 * min(len(text), len(entry.text)) / (len(text) + len(entry.text)) < SIMILARITY_LIMIT:
            continue  # the ratio cannot reach the limit with such different lengths
        small = min(len(grams), len(entry.grams))
        if small < PRUNE_MIN_GRAMS:
            candidates.append((1.0, entry))  # too short to fingerprint: compare in full
            continue
        overlap = len(grams & entry.grams) / small
        if overlap >= PRUNE_OVERLAP:
            candidates.append((overlap, entry))
    # difflib is slow, so only the look-alikes with the most shared wording are compared in full
    candidates.sort(key=lambda item: -item[0])
    for _, entry in candidates[:FULL_COMPARES]:
        score = similarity(text, entry.text)
        if score > best:
            best, best_id = score, entry.id
    return best, best_id


# --- the checks ---------------------------------------------------------------------------------


def _check_greeting(body: str, greeting: str) -> list[Issue]:
    name = greeting.strip()
    if not name:
        return [_issue("M01", "This person has no greeting_name, so no greeting is checked.")]
    if not body.strip():
        return []
    pattern = r"(?<!\w)" + re.escape(name) + r"(?!\w)"
    if re.search(pattern, _first_line(body), re.IGNORECASE):
        return []
    return [
        _issue(
            "M01",
            f"The first line must greet the person as '{name}' (their greeting_name). "
            f"It reads: '{truncate(_first_line(body), 60)}'.",
        )
    ]


def _check_company(
    text: str,
    company: Mapping[str, Any],
    others: Sequence[Mapping[str, Any]],
    offer_names: Sequence[str],
) -> list[Issue]:
    """M02: the person's company is mentioned (warn) and no other campaign company is (block).
    Other companies only count when written capitalised as in their name (or in ALL CAPS)."""
    own = _company_names(company)
    out: list[Issue] = []
    if not _mentions(text, own):
        shown = company.get("name") or "their company"
        out.append(_issue("M02", f"The message does not mention {shown}.", "warn"))
    rest = _mask(text, own)
    protected = {n.casefold() for n in offer_names}
    for other in others:
        if other.get("id") is not None and other.get("id") == company.get("id"):
            continue
        names = [n for n in _company_names(other) if len(n) >= 3 and n.casefold() not in protected]
        if names and _mentions(rest, names, exact=True):
            out.append(
                _issue(
                    "M02",
                    f"The message mentions {other.get('name')}, which is another company in "
                    "this campaign.",
                    "block",
                )
            )
            break
    return out


def _check_placeholders(subject: str, body: str) -> list[Issue]:
    out = []
    for label, text in (("subject", subject), ("body", body)):
        found = _PLACEHOLDER_RE.search(text)
        if found:
            out.append(_issue("M03", f"Unresolved placeholder in the {label}: '{found.group(0)}'."))
    return out


def _check_length(channel: str, body: str) -> list[Issue]:
    spec = drafting.CHANNEL_SPECS[channel]
    if not body.strip():
        return [_issue("M04", "The message body is empty.")]
    size = len(body) if spec.body_unit == "chars" else len(body.split())
    if size > spec.body_max:
        return [
            _issue(
                "M04",
                f"Too long: {size} {spec.body_unit}, the limit for {channel} is "
                f"{spec.body_max}. Cut {size - spec.body_max} {spec.body_unit}.",
            )
        ]
    if size < spec.body_min:
        return [
            _issue(
                "M04",
                f"Too short: {size} {spec.body_unit}, {channel} needs at least {spec.body_min}.",
            )
        ]
    return []


def _grams(tokens: Sequence[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def _claim_hits(text_tokens: Sequence[str], claim: str) -> str:
    """The wording of `claim` found in the message, or "". A claim of three or more words also
    matches on any three words in a row that contain two content words."""
    words = _tokens(claim)
    if not words:
        return ""
    joined = " ".join(words)
    if f" {joined} " in f" {' '.join(text_tokens)} ":
        return joined
    if len(words) >= 3:
        present = _grams(text_tokens, 3)
        for gram in (tuple(words[i : i + 3]) for i in range(len(words) - 2)):
            if sum(1 for w in gram if w not in _STOPWORDS) >= 2 and gram in present:
                return " ".join(gram)
    return ""


def _forbidden(profile: Mapping[str, Any]) -> list[str]:
    return [str(c) for c in profile.get("forbidden_claims") or [] if str(c).strip()]


def _check_forbidden(text: str, profile: Mapping[str, Any]) -> list[Issue]:
    out: list[Issue] = []
    tokens = _tokens(text)
    for claim in _forbidden(profile):
        hit = _claim_hits(tokens, claim)
        if hit:
            out.append(
                _issue(
                    "M05",
                    f"Contains a forbidden claim ('{hit}'; the offer forbids "
                    f"'{truncate(claim, 80)}').",
                )  # fmt: skip
            )
    for _label, pattern in GLOBAL_FORBIDDEN:
        found = pattern.search(text)
        if found:
            out.append(_issue("M05", f"Contains the banned phrase '{found.group(0)}'."))
    return out


def _numbers(text: str) -> list[tuple[str, tuple[float, str], bool]]:
    """(as written, (value, kind), is_money) for each number in `text`; kind is "%", "x" or "";
    is_money is true when the number is written with a currency sign."""
    found = []
    for m in _NUMBER_RE.finditer(_URL_RE.sub(" ", text)):
        try:
            value = float(m.group("num").replace(",", ""))
        except ValueError:
            continue
        unit = re.sub(r"\s+", "", (m.group("unit") or "").lower())
        kind = "%" if unit in {"%", "percent"} else "x" if unit in {"x", "times"} else ""
        value *= _MULTIPLIER.get(unit, 1.0)
        found.append((m.group(0).strip(), (round(value, 6), kind), bool(m.group("cur"))))
    return found


def _shares_claim(a: str, b: str) -> bool:
    return bool(_claim_hits(_tokens(a), b))


def _check_numbers(
    text: str, person: Mapping[str, Any], company: Mapping[str, Any], profile: Mapping[str, Any]
) -> list[Issue]:
    forbidden = _forbidden(profile)
    allowed: set[tuple[float, str]] = set()
    for point in profile.get("proof_points") or []:
        claim = str(point.get("claim") if isinstance(point, Mapping) else point or "")
        if any(_shares_claim(claim, f) or _shares_claim(f, claim) for f in forbidden):
            continue  # a forbidden claim wins over a proof point
        allowed.update(key for _, key, _ in _numbers(claim))
    # loose: numbers from CTAs, names and titles excuse a number of the same (value, kind) only
    # ("15-minute" is not "15%") and never a price: "$15" needs a proof point
    loose: set[tuple[float, str]] = set()
    for cta in profile.get("ctas") or []:
        loose.update(key for _, key, _ in _numbers(str(cta)))
    context_bits = [
        person.get("raw_name"), person.get("first_name"), person.get("last_name"),
        person.get("title"), company.get("name"), *_company_names(company),
    ]  # fmt: skip
    for bit in context_bits:
        loose.update(key for _, key, _ in _numbers(str(bit or "")))
    bad: list[str] = []
    for shown, (value, kind), money in _numbers(text):
        if (value, kind) in allowed or ((value, kind) in loose and not money):
            continue
        if shown not in bad:
            bad.append(shown)
    if not bad:
        return []
    return [
        _issue(
            "M06",
            f"Number(s) not in the offer's proof_points: {', '.join(repr(b) for b in bad[:4])}.",
        )
    ]


def _known_text(
    person: Mapping[str, Any],
    company: Mapping[str, Any],
    campaign: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> str:
    parts = [json.dumps(profile, ensure_ascii=False, default=str)]
    for key in ("raw_name", "first_name", "last_name", "title", "designation", "evidence_title",
                "evidence_snippet", "country"):  # fmt: skip
        parts.append(str(person.get(key) or ""))
    parts += [str(company.get(k) or "") for k in ("name", "sector", "size_note", "domain")]
    parts += _company_names(company)
    parts += [str(s) for s in campaign.get("sectors") or []]
    parts += [str(c) for c in campaign.get("countries") or []]
    return normalize_whitespace(" ".join(parts)).casefold()


def _brand_candidates(text: str) -> list[str]:
    out = []
    for m in _BRAND_RE.finditer(text):
        words = m.group(0).split()
        while words and words[0].strip(",.;:").casefold() in _BRAND_TRIM:
            words.pop(0)
        while words and words[-1].strip(",.;:").casefold() in _BRAND_TRIM:
            words.pop()
        words = [re.sub(r"['’]s$", "", w.strip(",.;:")) for w in words]
        if len(words) >= 2:
            out.append(" ".join(words))
    return out


def _is_place(name: str) -> bool:
    try:
        countries_mod.get(name)
    except countries_mod.UnknownCountryError:
        return False
    return True


def _check_brands(
    text: str,
    person: Mapping[str, Any],
    company: Mapping[str, Any],
    campaign: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> list[Issue]:
    known = _known_text(person, company, campaign, profile)
    unknown: list[str] = []
    for cand in _brand_candidates(text):
        if cand.casefold() in known or _is_place(cand) or cand in unknown:
            continue
        unknown.append(cand)
    if not unknown:
        return []
    return [
        _issue(
            "M07",
            "Name(s) not found in the offer or the lead's evidence (a client or brand?): "
            f"{', '.join(repr(u) for u in unknown[:3])}.",
        )
    ]


def _check_spam(text: str) -> list[Issue]:
    text = _FEEL_FREE_RE.sub(" ", text)  # "feel free to reply" is a call to action, not spam
    found = sorted({m.group(0).lower() for m in _SPAM_RE.finditer(text)})
    if not found:
        return []
    return [_issue("M08", f"Spam-trigger wording: {', '.join(repr(f) for f in found[:4])}.")]


def _check_subject(channel: str, subject: str) -> list[Issue]:
    spec = drafting.CHANNEL_SPECS[channel]
    if not spec.has_subject:
        return []
    if not subject:
        return [_issue("M09", "An email needs a subject.")]
    out = []
    if len(subject) > spec.subject_max:
        out.append(
            _issue(
                "M09",
                f"Subject is {len(subject)} characters, the limit is {spec.subject_max}.",
            )
        )
    if _SUBJECT_FAKE_RE.match(subject):
        out.append(_issue("M09", "Subject must not start with Re:, Fwd: or Fw:."))
    letters = [ch for ch in subject if ch.isalpha()]
    if len(letters) >= 6 and all(ch.isupper() for ch in letters):
        out.append(_issue("M09", "Subject must not be in ALL CAPS."))
    return out


def _closing_text(body: str) -> str:
    """The message without its sign-off (a closing phrase and/or the sender's name)."""
    lines = [ln.strip() for ln in body.strip().splitlines() if ln.strip()]
    if lines and _SIGNOFF_RE.match(lines[-1]):
        lines.pop()
    elif len(lines) > 1 and _SIGNOFF_RE.match(lines[-2]) and len(lines[-1].split()) <= 3:
        lines = lines[:-2]  # "Kind regards," followed by the sender's name
    return " ".join(lines)


def _check_ending(body: str) -> list[Issue]:
    if not body.strip():
        return []
    text = _closing_text(body)
    sentences = [s for s in re.split(r"(?<=[.?!])\s+", text) if s.strip()]
    tail = " ".join(sentences[-2:])
    if "?" in tail or _CTA_RE.search(tail):
        return []
    return [_issue("M10", "The message does not end with a question or a clear next step.")]


def _check_links(channel: str, body: str) -> list[Issue]:
    if drafting.CHANNEL_SPECS[channel].links_allowed:
        return []
    found = _LINK_RE.search(body)
    if not found:
        return []
    return [
        _issue("M11", f"{channel} messages should not contain links ('{found.group(0)[:40]}').")
    ]


def _check_similar(
    channel: str, text: str, own_id: Any, index: Mapping[int, IndexEntry]
) -> list[Issue]:
    score, other = _most_similar(text, channel, own_id, index)
    if other is None or score < SIMILARITY_LIMIT:
        return []
    return [
        _issue(
            "M12",
            f"{score:.0%} similar to message {other}. Rewrite the middle so it reads as written "
            "for this person.",
        )
    ]


def _build_locale_words() -> tuple[dict[str, str], dict[str, str]]:
    """(British -> American, American -> British) spelling pairs."""
    gb_us: dict[str, str] = {}
    for stem in _ISE_STEMS:
        for end in _ISE_ENDINGS:
            gb_us[stem + end] = stem[:-1] + "z" + end
    for stem in _OUR_STEMS:
        for end in _OUR_ENDINGS:
            gb_us[stem + end] = stem.replace("our", "or") + end
    for gb, us in (
        ("analyse", "analyze"), ("analysed", "analyzed"), ("analysing", "analyzing"),
        ("analyser", "analyzer"), ("programme", "program"), ("programmes", "programs"),
        ("centre", "center"), ("centres", "centers"), ("centred", "centered"),
        ("theatre", "theater"), ("fibre", "fiber"), ("litre", "liter"),
        ("catalogue", "catalog"), ("catalogues", "catalogs"), ("defence", "defense"),
        ("licence", "license"), ("licences", "licenses"), ("travelled", "traveled"),
        ("travelling", "traveling"), ("cancelled", "canceled"), ("cancelling", "canceling"),
        ("labelled", "labeled"), ("modelling", "modeling"), ("counsellor", "counselor"),
        ("jewellery", "jewelry"), ("tyre", "tire"), ("aluminium", "aluminum"),
        ("fulfil", "fulfill"), ("skilful", "skillful"), ("ageing", "aging"),
        ("judgement", "judgment"), ("sceptic", "skeptic"), ("practised", "practiced"),
        ("practising", "practicing"), ("practise", "practice"),
    ):  # fmt: skip
        gb_us[gb] = us
    us_gb = {us: gb for gb, us in gb_us.items() if us not in {"practice"}}
    return gb_us, us_gb


_GB_TO_US, _US_TO_GB = _build_locale_words()


def _check_locale(locale: str, text: str, known: str) -> list[Issue]:
    parts = re.split(r"[-_]", locale or "")
    if len(parts) < 2 or parts[0].lower() != "en":
        return []
    region = parts[1].upper()
    if region in _BRITISH_REGIONS:
        table, wrong, label = _US_TO_GB, "US", "British"
    elif region in _AMERICAN_REGIONS:
        table, wrong, label = _GB_TO_US, "British", "American"
    else:
        return []
    known_words = set(re.findall(r"[a-z]+", known))
    found: list[tuple[str, str]] = []
    for word in dict.fromkeys(re.findall(r"[A-Za-z]+", text.lower())):
        if word in table and word not in known_words:
            found.append((word, table[word]))
    if region in _BRITISH_REGIONS and re.search(r"\bto practice\b", text, re.IGNORECASE):
        found.append(("practice", "practise"))
    if not found:
        return []
    shown = ", ".join(f"'{w}' (use '{r}')" for w, r in found[:4])
    note = ""
    if region in _BRITISH_REGIONS and any(w in _AMBIGUOUS_US for w, _ in found):
        note = " Ignore 'program' if you mean software."
    return [
        _issue(
            "M13",
            f"{wrong} spelling for the {locale} campaign, which uses {label} spelling: {shown}."
            f"{note}",
        )
    ]


def _expand(text: str) -> str:
    for pattern, full in _ACRONYMS:
        text = pattern.sub(full, text)
    return text


def _stem(word: str) -> str:
    return word[:5]


def _rank_classes(text: str) -> set[str]:
    return {name for name, pattern in _RANK_CLASSES if pattern.search(text)}


def _check_titles(text: str, person: Mapping[str, Any], known: str) -> list[Issue]:
    """M14: a role named in the message ("Head of Operations") that the lead's title does not
    contain: a different function or a different rank."""
    title = _expand(f"{person.get('title') or ''} {person.get('designation') or ''}")
    title_words = {_stem(w) for w in re.findall(r"[a-z]+", title.lower())}
    title_classes = _rank_classes(title)
    for m in _ROLE_PHRASE_RE.finditer(_expand(text)):
        shown = []
        for w in re.findall(r"[A-Za-z&]+", m.group("rest")):
            if w.lower() in _ROLE_STOP:
                break
            shown.append(w)
        while shown and shown[-1].lower() in {"and", "&"}:
            shown.pop()
        words = [w.lower() for w in shown]
        content = [w for w in words if w not in {"and", "&", "of", "the"} and len(w) >= 3]
        phrase = f"{m.group('rank')} of {' '.join(shown)}".strip()
        if not content or phrase.casefold() in known:
            continue
        missing = [w for w in content if _stem(w) not in title_words]
        classes = _rank_classes(m.group("rank"))
        rank_wrong = bool(classes) and not (classes & title_classes) and bool(title_classes)
        if missing or rank_wrong:
            return [
                _issue(
                    "M14",
                    f"The message names the role '{phrase}', but this person's title is "
                    f"'{truncate(str(person.get('title') or ''), 60)}'.",
                )
            ]
    return []


def _check_sign_off(channel: str, body: str, sender: Mapping[str, Any] | None) -> list[Issue]:
    if not sender or not str(sender.get("name") or "").strip() or channel == "linkedin_invite":
        return []
    want = drafting.sign_off_lines(sender)
    lines = [normalize_whitespace(ln) for ln in body.splitlines() if ln.strip()]
    if [ln.casefold() for ln in lines[-len(want):]] == [w.casefold() for w in want]:
        return []
    return [_issue("M17", "End the message with the sign-off, each part on its own line: "
                   + " / ".join(want) + ".")]


def _without_sign_off(body: str, sender: Mapping[str, Any] | None) -> str:
    """The body without the trailing sign-off lines (so they do not trip M07, M10 or M14)."""
    want = drafting.sign_off_lines(sender) if sender else []
    lines = body.rstrip().splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    tail = [normalize_whitespace(ln).casefold() for ln in lines[-len(want):]] if want else []
    if want and tail == [w.casefold() for w in want]:
        lines = lines[: -len(want)]
    return "\n".join(lines)


def _check_tone(text: str) -> list[Issue]:
    problems = []
    if text.count("!") >= 2:
        problems.append(f"{text.count('!')} exclamation marks (use at most one)")
    if _EMOJI_RE.search(text):
        problems.append("an emoji")
    shouting = [w for w in re.findall(r"\b[A-Z]{4,}\b", text) if w not in {"TODO", "TBC", "TBD"}]
    if len(shouting) >= 2:
        problems.append("words in ALL CAPS")
    if not problems:
        return []
    return [_issue("M15", f"Tone: {', '.join(problems)}.")]


# --- public API ---------------------------------------------------------------------------------


def review_pending_issue(message: Mapping[str, Any], strict: bool) -> list[Issue]:
    """M16: the message has no AI `pass` yet. A block when strict mode is on, else a warning."""
    if message.get("ai_review") == "pass":
        return []
    return [
        _issue(
            "M16",
            "The AI has not reviewed this message yet (verdict pass needed).",
            "block" if strict else "warn",
        )
    ]


def check_message(
    message: Mapping[str, Any],
    person: Mapping[str, Any],
    company: Mapping[str, Any],
    campaign: Mapping[str, Any],
    offer: Mapping[str, Any],
    all_messages_index: Mapping[int, IndexEntry] | None = None,
    *,
    companies: Sequence[Mapping[str, Any]] | None = None,
    sender: Mapping[str, Any] | None = None,
) -> list[Issue]:
    """Checks M01-M15 for one message (M16 is `review_pending_issue`). Returns the issues.

    `message` has `channel`, `subject`, `body` (and `id` once stored); `person` and `company`
    are database rows; `campaign` is the `campaign.get_campaign` dict; `offer` is the offer
    profile dict; `all_messages_index` is `build_index(campaign_id)` for the near-duplicate
    check (M12), `companies` the campaign's companies for M02 (read from the database when
    not given), and `sender` the project's sign-off (M17; no check when it has no name).
    """
    channel = str(message.get("channel") or "")
    if channel not in drafting.CHANNEL_SPECS:
        raise ValueError(f"unknown channel: {channel!r}")
    subject = str(message.get("subject") or "")
    body = str(message.get("body") or "")
    text = f"{subject}\n{body}" if subject else body
    main = _without_sign_off(body, sender)
    text_main = f"{subject}\n{main}" if subject else main
    greeting = str(person.get("greeting_name") or person.get("first_name") or "")
    others = companies if companies is not None else _campaign_companies(campaign.get("id"))
    known = _known_text(person, company, campaign, offer)
    offer_names = [
        str(offer.get("name") or ""),
        str((offer.get("sender") or {}).get("company") or ""),
    ]

    issues: list[Issue] = []
    issues += _check_greeting(body, greeting)
    issues += _check_company(text, company, others, offer_names)
    issues += _check_placeholders(subject, body)
    issues += _check_length(channel, body)
    issues += _check_forbidden(text, offer)
    issues += _check_numbers(text, person, company, offer)
    issues += _check_brands(text_main, person, company, campaign, offer)
    if channel == "email":
        issues += _check_spam(text)
    issues += _check_subject(channel, subject)
    issues += _check_ending(main)
    issues += _check_links(channel, body)
    if all_messages_index:
        full = normalize_whitespace(
            f"{person.get('first_name') or ''} {person.get('last_name') or ''}"
        )
        sim_text = similarity_text(subject, body, greeting, company, full)
        issues += _check_similar(channel, sim_text, message.get("id"), all_messages_index)
    issues += _check_locale(str(campaign.get("locale") or ""), text, known)
    issues += _check_titles(main, person, known)
    issues += _check_tone(text)
    issues += _check_sign_off(channel, body, sender)
    return issues
