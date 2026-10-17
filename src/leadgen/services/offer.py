"""Offer intake: turn a URL, pasted text or a file into clean text, and store the OfferProfile.

The host AI reads the text (`read_offer_text`) and writes the structured profile back
(`save_profile`). This module never calls an AI. Expected problems (dead page, private Google
Doc, unsupported file) come back as `{"ok": False, "error": {...}}`; a missing offer or an
invalid profile raises `OfferError`, which adapters turn into the same error shape.

Callers must run `db.init_db()` first.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError, model_validator

from leadgen import db
from leadgen.core.text import normalize_whitespace, truncate
from leadgen.services import offer_files, offer_web

MAX_CHUNK_CHARS = 40_000
MAX_RAW_CHARS = 1_000_000
MAX_SNIPPETS = 50
SNIPPET_CHARS = 300

_PASTE_HINT = (
    "Ask the user to paste the text of their offer, or to share a PDF, DOCX, TXT or MD file."
)
_GDOC_HINT = (
    "Ask the user to download the document as .docx (File > Download) and give the file path, "
    "or to paste the text."
)

# Markers meaning "this fact is not confirmed yet". Case-insensitive except where noted.
_UNCONFIRMED_RE = re.compile(
    r"\[\s*(?:confirm|provide|insert|tbd|tbc|todo|placeholder|highlight)"
    r"|\bTBC\b|\bTBD\b|\bTODO\b|to be confirmed|to be determined"
    r"|\{\{|<\s*insert\b"
    r"|(?<![A-Za-z0-9])X{2,}(?![A-Za-z])",
    re.IGNORECASE,
)
_XX_RE = re.compile(r"(?<![A-Za-z0-9])X{2,}(?:[kKmM](?![A-Za-z]))?(?![A-Za-z])")


class OfferError(Exception):
    """A request about an offer failed. `code` is stable text; `hint` tells the AI what to do."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def as_response(self) -> dict[str, Any]:
        """The `{"ok": False, "error": {...}}` shape used by every tool."""
        return _error(self.code, self.message, self.hint)


def _error(code: str, message: str, hint: str = "") -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "hint": hint}}


# --- OfferProfile (PLAN section 6) ------------------------------------------------------------


def _clean_text(value: str) -> str:
    return normalize_whitespace(value)


def _required_text(value: str) -> str:
    value = normalize_whitespace(value)
    if not value:
        raise ValueError("must not be empty")
    return value


def _optional_text(value: str | None) -> str | None:
    value = normalize_whitespace(value or "")
    return value or None


def _clean_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        value = normalize_whitespace(value)
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _language(value: str) -> str:
    value = normalize_whitespace(value).replace("_", "-")
    match = re.fullmatch(r"([A-Za-z]{2,3})(?:-([A-Za-z]{2}|[A-Za-z]{4}))?", value)
    if not match:
        raise ValueError("must look like 'en' or 'en-GB'")
    lang, region = match.group(1).lower(), match.group(2)
    if region is None:
        return lang
    return f"{lang}-{region.upper() if len(region) == 2 else region.title()}"


Text = Annotated[str, AfterValidator(_required_text)]
OptText = Annotated[str, AfterValidator(_clean_text)]
MaybeText = Annotated[str | None, AfterValidator(_optional_text)]
TextList = Annotated[list[str], AfterValidator(_clean_list)]


class Service(BaseModel):
    """One service or product line."""

    model_config = ConfigDict(extra="forbid")

    name: Text
    what: Text
    best_for: TextList = []


class ProofPoint(BaseModel):
    """A number or case study the messages may use, with where it came from."""

    model_config = ConfigDict(extra="forbid")

    claim: Text
    source: Text


class Sender(BaseModel):
    """Who the messages come from."""

    model_config = ConfigDict(extra="forbid")

    name: Text
    company: Text
    role: Text
    email: MaybeText = None
    website: MaybeText = None


class OfferProfile(BaseModel):
    """What is being sold, in the form the drafting and QA steps rely on."""

    model_config = ConfigDict(extra="forbid")

    name: Text
    one_liner: Text
    description: Text
    services: list[Service] = []
    audiences: TextList = []
    confirmed_claims: TextList = []
    forbidden_claims: TextList = []
    proof_points: list[ProofPoint] = []
    ctas: TextList = []
    sender: Sender
    tone: Text
    language: Annotated[str, AfterValidator(_language)]

    @model_validator(mode="after")
    def _tidy_and_check(self) -> OfferProfile:
        services: dict[str, Service] = {}
        for service in self.services:
            services.setdefault(service.name.casefold(), service)
        self.services = list(services.values())
        proofs: dict[str, ProofPoint] = {}
        for proof in self.proof_points:
            proofs.setdefault(proof.claim.casefold(), proof)
        self.proof_points = list(proofs.values())
        confirmed = {_claim_key(c): c for c in self.confirmed_claims}
        clash = [confirmed[k] for k in map(_claim_key, self.forbidden_claims) if k in confirmed]
        if clash:
            shown = "; ".join(truncate(c, 80) for c in clash[:5])
            raise ValueError(f"confirmed_claims and forbidden_claims overlap: {shown}")
        return self


def _claim_key(claim: str) -> str:
    return normalize_whitespace(claim).casefold().rstrip(".!;, ")


# --- detecting "to confirm" text ------------------------------------------------------------


def find_unconfirmed(text: str, limit: int = MAX_SNIPPETS) -> tuple[list[str], int]:
    """Lines of `text` that look unconfirmed: `([snippets], total_matching_lines)`.

    Matches `[CONFIRM`, `[PROVIDE`, `[HIGHLIGHT:` (highlighted DOCX text), `TBC`, `TODO`, `TBD`,
    "to be confirmed", `{{ }}`, `<insert ...>` and `XX` placeholders. Each snippet is the line,
    or a window of `SNIPPET_CHARS` characters around the first marker in a long line.
    """
    snippets: list[str] = []
    seen: set[str] = set()
    total = 0
    for raw in text.splitlines():
        line = normalize_whitespace(raw)
        if not line or line.startswith("# http"):
            continue
        match = _UNCONFIRMED_RE.search(line) or _XX_RE.search(line)
        if match is None:
            continue
        total += 1
        if len(line) > SNIPPET_CHARS:
            start = max(0, match.start() - SNIPPET_CHARS // 2)
            line = line[start : start + SNIPPET_CHARS]
        if line not in seen and len(snippets) < limit:
            seen.add(line)
            snippets.append(line)
    return snippets, total


# --- ingest ---------------------------------------------------------------------------------


def _default_name(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.lstrip("#- ").strip()
        if line and not line.startswith("http"):
            return truncate(line, 60)
    return fallback


def _store(
    name: str,
    source_type: str,
    source_ref: str,
    raw_text: str,
    pages: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    warnings = list(warnings or [])
    if len(raw_text) > MAX_RAW_CHARS:
        raw_text = raw_text[:MAX_RAW_CHARS]
        warnings.append("The text was very long and was cut at 1,000,000 characters.")
    with db.session() as conn:
        offer_id = db.insert(
            conn,
            "offers",
            name=name,
            source_type=source_type,
            source_ref=source_ref,
            raw_text=raw_text,
        )
    snippets, count = find_unconfirmed(raw_text)
    result: dict[str, Any] = {
        "ok": True,
        "offer_id": offer_id,
        "name": name,
        "source_type": source_type,
        "source_ref": source_ref,
        "total_chars": len(raw_text),
        "unconfirmed_snippets": snippets,
        "unconfirmed_count": count,
        "warnings": warnings,
    }
    if pages is not None:
        result["pages"] = pages
    return result


def ingest_url(url: str, max_pages: int = 15) -> dict[str, Any]:
    """Read a website (up to `max_pages`, hard cap 15) or a public Google Doc into a new offer.

    Returns `{"ok": True, "offer_id", "name", "total_chars", "pages", "unconfirmed_snippets",
    "unconfirmed_count", "warnings", ...}`. A dead, blocked or empty page returns the structured
    error `{"ok": False, "error": {"code", "message", "hint"}}` suggesting pasted text or a file.
    Fetching takes roughly 1 second per page.
    """
    try:
        export_url = offer_web.google_doc_export_url(url)
        if export_url is not None:
            text = offer_web.fetch_google_doc(export_url)
            return _store(
                _default_name(text, "Google Doc"),
                "url",
                url.strip(),
                f"# {url.strip()}\n{text}",
                pages=[{"url": url.strip(), "chars": len(text)}],
            )
        start = offer_web.normalize_start_url(url)
        crawled, warnings = offer_web.crawl(start, max_pages=max_pages)
    except offer_web.WebError as exc:
        google = exc.code in ("private_document", "unsupported_google_link")
        return _error(exc.code, exc.message, _GDOC_HINT if google else _PASTE_HINT)
    raw = "\n\n".join(f"# {page.url}\n{page.text}" for page in crawled)
    host = urlparse(start).hostname or start
    name = host[4:] if host.startswith("www.") else host
    pages = [{"url": page.url, "chars": len(page.text)} for page in crawled]
    return _store(name, "url", start, raw, pages=pages, warnings=warnings)


def ingest_text(text: str, name: str | None = None) -> dict[str, Any]:
    """Store pasted text as a new offer (same result shape as `ingest_url`, without pages)."""
    cleaned = offer_files.tidy(text or "")
    if not cleaned:
        return _error("empty_text", "There is no text to read.", _PASTE_HINT)
    label = normalize_whitespace(name or "") or _default_name(cleaned, "Pasted text")
    return _store(truncate(label, 80), "text", "", cleaned)


def ingest_file(path: str | Path) -> dict[str, Any]:
    """Read a `.pdf`, `.docx`, `.txt` or `.md` file (max 20 MB) into a new offer."""
    try:
        text = offer_files.read_file_text(path)
    except offer_files.FileSourceError as exc:
        return _error(exc.code, exc.message, _PASTE_HINT)
    file = Path(path).expanduser()
    return _store(truncate(file.stem, 80) or "File", "file", str(file.resolve()), text)


# --- reading --------------------------------------------------------------------------------


def _get_row(conn, offer_id: int) -> dict[str, Any]:
    row = db.get(conn, "offers", int(offer_id))
    if row is None:
        raise OfferError(
            "not_found", f"There is no offer with id {offer_id}.", "Use list_offers to see them."
        )
    return row


def read_offer_text(
    offer_id: int, cursor: int = 0, max_chars: int = MAX_CHUNK_CHARS
) -> dict[str, Any]:
    """One chunk of the offer's text: `{"text", "next_cursor" | None, "total_chars"}`.

    Chunks are at most `max_chars` (hard cap 40,000) and end at a line break when possible.
    Call again with `next_cursor` until it is None; the chunks join back into the full text.
    """
    if cursor < 0:
        raise OfferError("invalid_cursor", "cursor must be 0 or more.")
    max_chars = max(1, min(int(max_chars), MAX_CHUNK_CHARS))
    with db.session() as conn:
        text = _get_row(conn, offer_id)["raw_text"]
    total = len(text)
    if cursor >= total:
        return {"text": "", "next_cursor": None, "total_chars": total}
    end = min(cursor + max_chars, total)
    if end < total:
        cut = text.rfind("\n", cursor + max_chars // 2, end)
        if cut != -1:
            end = cut + 1
    return {
        "text": text[cursor:end],
        "next_cursor": end if end < total else None,
        "total_chars": total,
    }


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "source_type": row["source_type"],
        "source_ref": row["source_ref"],
        "total_chars": len(row["raw_text"]),
        "has_profile": row["profile_json"] is not None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_offer(offer_id: int) -> dict[str, Any]:
    """The offer's details and saved profile (`profile` is None until saved), without its text."""
    with db.session() as conn:
        row = _get_row(conn, offer_id)
    return {**_summary(row), "profile": row["profile_json"]}


def list_offers() -> list[dict[str, Any]]:
    """All offers, newest first (without their text)."""
    with db.session() as conn:
        rows = db.query(conn, "SELECT * FROM offers ORDER BY id DESC")
    return [_summary(row) for row in rows]


# --- profile --------------------------------------------------------------------------------


def _problems(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors(include_input=False, include_url=False):
        where = ".".join(str(p) for p in err["loc"]) or "profile"
        message = str(err["msg"]).removeprefix("Value error, ")
        parts.append(f"{where}: {message}")
    return "; ".join(parts[:12])


def save_profile(offer_id: int, profile: dict[str, Any]) -> OfferProfile:
    """Validate and store the AI-written profile; returns the cleaned `OfferProfile`.

    Whitespace is normalised, lists are de-duplicated, and the profile is rejected when
    `confirmed_claims` and `forbidden_claims` share a claim. Raises `OfferError`
    (`invalid_profile`, `not_found`).
    """
    with db.session() as conn:
        _get_row(conn, offer_id)
    try:
        parsed = OfferProfile.model_validate(profile)
    except ValidationError as exc:
        raise OfferError(
            "invalid_profile",
            f"The profile is not valid: {_problems(exc)}",
            "Fix these fields and call save_offer_profile again.",
        ) from None
    with db.session() as conn:
        db.update(
            conn,
            "offers",
            int(offer_id),
            profile_json=parsed.model_dump(mode="json"),
            name=parsed.name,
        )
    return parsed


def ingest_source(
    *,
    url: str | None = None,
    text: str | None = None,
    file_path: str | None = None,
    name: str | None = None,
    offer_id: int | None = None,
    cursor: str | int | None = None,
    file_root: Path | None = None,
) -> dict[str, Any]:
    """The entry point of the `ingest_offer_source` tool: read a new source, or read on.

    Give exactly one of `url`, `text` or `file_path` to create an offer; the reply is the
    `ingest_*` result plus the first chunk (`text`, `next_cursor`). Give `offer_id` and the
    `cursor` of the previous reply to read the next chunk of an offer already stored. With
    `file_root` set (remote mode) a file must lie inside that folder.
    """
    if offer_id is not None:
        if any((url, text, file_path)):
            return _error(
                "conflicting_input",
                "Give either offer_id (to read more of an offer) or a new source, not both.",
                "To read more of an offer pass only offer_id and cursor.",
            )
        try:
            start = int(cursor or 0)
        except (TypeError, ValueError):
            raise OfferError("invalid_cursor", "cursor must be a number.") from None
        return {"ok": True, "offer_id": int(offer_id), **read_offer_text(int(offer_id), start)}
    given = [value for value in (url, text, file_path) if value and str(value).strip()]
    if len(given) != 1:
        return _error(
            "one_source_needed",
            "Give exactly one of url, text or file_path.",
            "Ask the user for a website address, pasted text or a document path.",
        )
    if url and url.strip():
        result = ingest_url(url)
    elif text and text.strip():
        result = ingest_text(text, name)
    else:
        path = Path(str(file_path)).expanduser()
        if file_root is not None:
            try:
                inside = path.resolve().is_relative_to(file_root.resolve())
            except OSError:
                inside = False
            if not inside:
                return _error(
                    "file_outside_workspace",
                    "In this connection only files inside the Lead Generator folder can be read.",
                    f"Ask the user to copy the file into {file_root} or to paste its text.",
                )
        result = ingest_file(path)
    if not result.get("ok"):
        return result
    first = read_offer_text(result["offer_id"], 0)
    return {**result, "text": first["text"], "next_cursor": first["next_cursor"]}


def guide_text() -> str:
    """The instructions that tell the AI how to write the profile (`guides/offer_profile.md`)."""
    guide = resources.files("leadgen").joinpath("guides", "offer_profile.md")
    return guide.read_text(encoding="utf-8")
