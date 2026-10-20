"""Shared pieces of the end-to-end tests (T19): the HTTP world, a fake mailbox and a fake AI.

Nothing here touches the network. Serper, Hunter and Reoon are the real provider classes
talking to respx routes that answer from `universe.py`; DNS and the website checks are
replaced; the mailbox is an in-memory object with a tiny IMAP server.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import dns.resolver
import httpx
import respx
from universe import COMPANIES, HUNTER_RESPONSES, OFFER_TEXT, REOON_RESPONSES, SEARCH_RESULTS

from leadgen.providers.base import RateLimiter
from leadgen.send.smtp_imap import SendResult
from leadgen.services import companies as companies_svc
from leadgen.services import emails

# Distinct fake key values, so a test can search every output for a leak.
SECRETS = {
    "SERPER_API_KEY": "serper-e2e-key-7f3a9c1d",
    "HUNTER_API_KEY": "hunter-e2e-key-5b8e2d40",
    "REOON_API_KEY": "reoon-e2e-key-c61f0a97",
}
GROUPS = {"hr": "HR leaders", "ld": "L&D / Training heads"}
_COMPANY_RE = re.compile(r'\) "([^"]+)" [A-Za-z ]+$')


def group_and_company(query: str) -> tuple[str, str]:
    """`(company, group label)` read back from a people-search query."""
    match = _COMPANY_RE.search(query)
    assert match, f"unexpected query shape: {query!r}"
    label = GROUPS["hr"] if '"HR director"' in query else GROUPS["ld"]
    return match.group(1), label


def expected_outcomes() -> dict[str, str]:
    """What the confirmation rules should say about each honest result: link -> `confirm` or
    `review L07 L14` (reason codes sorted). Read from the comment after every result in
    `universe.py` ("# confirm" / "# review L07 L14"), so the data and the engine are checked
    against each other."""
    import universe

    source = Path(universe.__file__).read_text(encoding="utf-8")
    start = source.index("SEARCH_RESULTS: dict")
    comments = re.findall(r"\},\s*#\s*(.*)", source[start : source.index("HUNTER_RESPONSES: dict")])
    results = [r for rows in SEARCH_RESULTS.values() for r in rows]
    assert len(comments) == len(results)
    out: dict[str, str] = {}
    for row, comment in zip(results, comments, strict=True):
        if row.get("fabricated"):
            assert comment.startswith("fabricated")
            continue
        words = comment.split()
        out[row["link"]] = " ".join([words[0], *sorted(words[1:])])
    return out


def serper_organic(company: str, label: str, *, include_fabricated: bool) -> list[dict[str, Any]]:
    """The results Google would return for one search (fabricated entries are never returned)."""
    rows = SEARCH_RESULTS.get(f"{company}|{label}", [])
    return [
        {
            "title": r["title"],
            "link": r["link"],
            "snippet": r["snippet"],
            "position": n,
        }
        for n, r in enumerate(rows, 1)
        if include_fabricated or not r.get("fabricated")
    ]


@dataclass
class World:
    """Counters of what the providers were asked, and anything the universe could not answer."""

    calls: Counter = field(default_factory=Counter)
    unexpected: list[str] = field(default_factory=list)
    reoon_emails: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    router: Any = None


class _SharedClient(httpx.Client):
    """One client for every request of a test: making a client loads the TLS certificates, which
    costs about 0.3 s, and the providers make hundreds of calls. It is never closed in between."""

    def __enter__(self) -> _SharedClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def close(self) -> None:
        return None


def fast_http(monkeypatch) -> None:
    """Make `leadgen.core.http.client()` hand out one shared client (see `_SharedClient`)."""
    from leadgen.core import http

    shared = _SharedClient(
        timeout=httpx.Timeout(20.0, connect=10.0),
        headers={"User-Agent": http.USER_AGENT},
        follow_redirects=True,
    )
    monkeypatch.setattr(http, "client", lambda request_hook=None: shared)


def install_world(monkeypatch, router: respx.MockRouter) -> World:
    """Put the keys, DNS, domain checks and the Serper/Hunter/Reoon routes in place."""
    world = World(router=router)
    fast_http(monkeypatch)
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)

    # --- DNS, website and rate limits -----------------------------------------------------
    emails._mx_cache.clear()
    emails._site_cache.clear()
    monkeypatch.setattr(emails, "_site_limiter", RateLimiter(10_000))
    monkeypatch.setattr(emails, "scrape_site_emails", lambda domain: [])
    monkeypatch.setattr(emails, "_smtp_rcpt", lambda host, address: None)
    domains = {c["domain"] for c in COMPANIES}

    class Answer:
        exchange = "aspmx.l.google.com."
        preference = 10

    def resolve(domain, rdtype="A", **kwargs):
        if domain in domains:
            return [Answer()]
        raise dns.resolver.NXDOMAIN()

    monkeypatch.setattr(emails.dns.resolver, "resolve", resolve)
    monkeypatch.setattr(
        companies_svc,
        "check_domain",
        lambda host: companies_svc.DomainCheck(True, "Domain check ok"),
    )
    from leadgen.providers import base, hunter, lemlist, reoon, serper, zerobounce

    monkeypatch.setattr(base, "_sleep", lambda seconds: None)
    for module in (serper, hunter, lemlist, reoon, zerobounce):
        monkeypatch.setattr(module, "_limiter", RateLimiter(10_000))

    # --- Serper ---------------------------------------------------------------------------
    def serper_route(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        world.calls["serper"] += 1
        world.queries.append(body["q"])
        company, label = group_and_company(body["q"])
        organic = serper_organic(company, label, include_fabricated=False)
        return httpx.Response(200, json={"searchParameters": {"q": body["q"]}, "organic": organic})

    router.post("https://google.serper.dev/search").mock(side_effect=serper_route)

    # --- Hunter ---------------------------------------------------------------------------
    def hunter_route(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rsplit("/", 1)[-1]
        query = parse_qs(urlparse(str(request.url)).query)
        arg = lambda name: query.get(name, [""])[0]  # noqa: E731
        world.calls[f"hunter:{path}"] += 1
        if path == "domain-search":
            body = HUNTER_RESPONSES["domain_search"].get(arg("domain"))
        elif path == "email-finder":
            body = HUNTER_RESPONSES["email_finder"].get(
                f"{arg('domain')}|{arg('first_name')} {arg('last_name')}"
            )
            if body is None:
                return httpx.Response(404, json={"errors": [{"details": "not found"}]})
        elif path == "email-verifier":
            body = HUNTER_RESPONSES["email_verifier"].get(arg("email"))
        elif path == "account":
            body = HUNTER_RESPONSES["account"]
        else:
            body = None
        if body is None:
            world.unexpected.append(f"hunter {path} {request.url.query.decode()}")
            return httpx.Response(404, json={"errors": [{"details": "not in the universe"}]})
        return httpx.Response(200, json=body)

    router.get(url__regex=r"https://api\.hunter\.io/v2/.*").mock(side_effect=hunter_route)

    # --- Reoon ----------------------------------------------------------------------------
    def reoon_verify(request: httpx.Request) -> httpx.Response:
        address = parse_qs(urlparse(str(request.url)).query).get("email", [""])[0]
        world.calls["reoon"] += 1
        world.reoon_emails.append(address)
        body = REOON_RESPONSES["verify"].get(address)
        if body is None:
            world.unexpected.append(f"reoon {address}")
            return httpx.Response(200, json={"status": "unknown", "is_catch_all": False})
        return httpx.Response(200, json=body)

    router.get(url__regex=r"https://emailverifier\.reoon\.com/api/v1/verify.*").mock(
        side_effect=reoon_verify
    )
    router.get(url__regex=r"https://emailverifier\.reoon\.com/api/v1/check-account.*").mock(
        return_value=httpx.Response(200, json=REOON_RESPONSES["balance"])
    )
    return world


# --- the mailbox ------------------------------------------------------------------------------


class FakeImap:
    """A tiny read-only IMAP server over a dict `{uid: raw bytes}`."""

    def __init__(self, messages: dict[int, bytes], validity: str = "7001") -> None:
        self.messages = messages
        self.validity = validity
        self.commands: list[str] = []

    def select(self, mailbox="INBOX", readonly=False):
        self.commands.append("EXAMINE" if readonly else "SELECT")
        return "OK", [str(len(self.messages)).encode()]

    def response(self, code):
        return code, [self.validity.encode()]

    def uid(self, command, *args):
        command = command.upper()
        self.commands.append(command)
        assert command in {"SEARCH", "FETCH"}, f"forbidden IMAP command {command}"
        if command == "SEARCH":
            criteria = args[1]
            found = sorted(self.messages)
            match = re.fullmatch(r"UID (\d+):\*", criteria)
            if match:
                start = int(match.group(1))
                found = [u for u in found if u >= start] or found[-1:]
            return "OK", [" ".join(str(u) for u in found).encode()]
        uid = int(args[0])
        parts = args[1]
        assert "BODY.PEEK" in parts, parts
        head, _, body = self.messages[uid].partition(b"\r\n\r\n")
        out: list = []
        if "HEADER" in parts:
            out.append(
                (f"{uid} (UID {uid} BODY[HEADER] {{{len(head)}}}".encode(), head + b"\r\n\r\n")
            )
        limit = int(re.search(r"TEXT\]<0\.(\d+)>", parts).group(1))
        text = body[:limit]
        out.append((f" BODY[TEXT]<0> {{{len(text)}}}".encode(), text))
        out.append(b")")
        return "OK", out

    def logout(self):
        return "BYE", [b""]


class FakeMailbox:
    """Stands in for `SmtpImapMailbox`: records what was sent, serves a scripted inbox."""

    user = "noor.haddad@lanternfish.example.com"
    from_email = "noor.haddad@lanternfish.example.com"
    host = "smtp.lanternfish.example.com"
    imap_host = "imap.lanternfish.example.com"

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []
        self.inbox: dict[int, bytes] = {}
        self.imap = FakeImap(self.inbox)

    def configured(self) -> bool:
        return True

    def test(self) -> tuple[bool, str]:
        return True, "SMTP and IMAP both work."

    def send(self, to, subject, body, headers=None):
        message_id = f"<e2e-{len(self.sent) + 1}@lanternfish.example.com>"
        self.sent.append({"to": to, "subject": subject, "body": body, "message_id": message_id})
        return SendResult(message_id=message_id)

    def imap_connection(self, timeout: float = 30):
        return self.imap

    def add_mail(self, raw: str) -> None:
        text = raw.replace("\r\n", "\n").replace("\n", "\r\n")
        self.inbox[len(self.inbox) + 1] = text.encode("utf-8")


# --- the offer profile and the fake AI writer ---------------------------------------------------

PROFILE: dict[str, Any] = {
    "name": "Lanternfish VR",
    "one_liner": "Short virtual reality training scenarios for frontline teams.",
    "description": (
        "Lanternfish VR is a virtual reality training studio. It builds short scenarios that "
        "run on standard headsets, so people can practise a hard moment before they meet it "
        "for real."
    ),
    "services": [
        {
            "name": "Customer handling scenarios",
            "what": "VR practice for difficult customer conversations",
            "best_for": ["L&D", "retail", "logistics"],
        },
        {
            "name": "Safety drills",
            "what": "Warehouse and site hazard walk-throughs in a headset",
            "best_for": ["safety", "operations"],
        },
        {
            "name": "Manager conversations",
            "what": "Practice for performance reviews and conduct topics",
            "best_for": ["HR"],
        },
    ],
    "audiences": ["L&D leaders", "HR leaders", "operations and safety leads"],
    "confirmed_claims": [
        "Scenarios run on standard headsets",
        "Each scenario ends with a short debrief and a score",
        "A simple dashboard shows completion, scores and retry counts",
        "Scenarios are tailored to the client's own processes",
        "Scenarios include captions and a seated mode",
    ],
    "forbidden_claims": [
        "Clients report fewer escalated complaints",
        "Shorter time to competence for new warehouse staff",
        "Volume discounts apply",
    ],
    "proof_points": [],
    "ctas": ["15-minute call", "short example"],
    "sender": {
        "name": "Noor Haddad",
        "company": "Lanternfish VR",
        "role": "Studio Lead",
        "email": "noor.haddad@lanternfish.example.com",
        "website": "https://lanternfish.example.com",
    },
    "tone": "professional, plain, no hype",
    "language": "en-GB",
}

HYPE = "This could transform your whole team overnight."
FOCUS = {
    "L&D / Training heads": (
        "A learner gets a safe place to practise the hard conversations that classroom "
        "courses rarely stick for."
    ),
    "HR leaders": (
        "Managers get a safe place to practise onboarding and conduct conversations before "
        "they have them for real."
    ),
}


def role_of(title: str) -> str:
    """The role part of a stored title ("Training Manager at Acme" -> "Training Manager")."""
    return re.split(r"\s+(?:at|@)\s+", title, maxsplit=1)[0].strip()


def linkedin_text(p: dict[str, Any], flaw: str = "") -> str:
    """The fake AI's LinkedIn message for one drafting-batch person (`flaw` plants a defect)."""
    g, company = p["greeting_name"], p["company"]["name"]
    sector = p["company"].get("sector", "").lower() or "your sector"
    greeting = {"no_greeting": "Hi there,", "placeholder": "Hi [First name],"}.get(
        flaw, f"Hi {g},"
    )
    parts = [
        f"{greeting} I saw that you are {role_of(p['title'])} at {company}.",
        "We build short VR scenarios that let teams practise a difficult moment in a headset "
        "before they face it for real, and each one ends with a debrief and a score your "
        "trainers can read.",
        FOCUS[p["designation"]],
    ]
    if flaw == "forbidden":
        parts.append("Clients report fewer escalated complaints after customer handling courses.")
    if flaw == "number":
        parts.append("We can have a prototype ready within 6 weeks.")
    if flaw == "too_long":
        parts.append("We also tailor every scenario to your own processes. " * 12)
    if flaw == "hype":
        parts.append(HYPE)
    closing = "useful?" if flaw == "no_greeting" else f"useful, {g}?"
    parts.append(f"Would a short example for {sector} teams be {closing}")
    return " ".join(parts)


def email_parts(p: dict[str, Any], flaw: str = "") -> tuple[str, str]:
    """`(subject, body)` of the fake AI's email for one drafting-batch person."""
    g, company = p["greeting_name"], p["company"]["name"]
    sector = p["company"].get("sector", "").lower() or "your sector"
    subject = f"VR practice scenarios for {company}"
    if flaw == "bad_subject":
        subject = "RE: " + subject
    paragraphs = [
        f"Hi {g},",
        f"I saw that you are {role_of(p['title'])} at {company}, so I thought a short note "
        "might be useful.",
        "Lanternfish VR builds short virtual reality scenarios for frontline teams. A learner "
        "practises a hard moment in a standard headset, such as calming an upset customer or "
        "spotting hazards on a warehouse floor, then gets a short debrief and a score that "
        "your training team can read on a simple dashboard.",
        FOCUS[p["designation"]],
    ]
    if flaw == "forbidden":
        paragraphs.append("Clients report fewer escalated complaints after customer handling.")
    if flaw == "hype":
        paragraphs.append(HYPE)
    paragraphs.append(f"Would a 15-minute call to walk through an example for {sector} teams be "
                      f"useful, {g}?")
    paragraphs.append("Kind regards,\nNoor")
    return subject, "\n\n".join(paragraphs)


def batch_person(item: dict[str, Any]) -> dict[str, Any]:
    """A review-batch item in the shape the writer reads (the fake AI redrafts from evidence)."""
    person = item["person"]
    return {
        "greeting_name": person["greeting_name"],
        "title": person["title"],
        "designation": person["designation"],
        "company": {"name": item["company"]["name"], "sector": item["company"].get("sector", "")},
    }


def judge(item: dict[str, Any]) -> dict[str, Any]:
    """The fake AI reviewer: reads one review item and gives pass, fix or reject.

    It checks the greeting, the company name and a hype sentence against the evidence in the
    item, and rejects a recruitment manager (not a people leader for this offer).
    """
    person, body = item["person"], item["body"]
    verdict: dict[str, Any] = {"message_id": item["message_id"]}
    if "recruit" in person["title"].lower():
        return {
            **verdict,
            "verdict": "reject",
            "notes": "Recruitment role, not a people or training leader for this offer.",
        }
    greeting_ok = body.lstrip().startswith(f"Hi {person['greeting_name']},")
    company_ok = item["company"]["name"] in body
    if greeting_ok and company_ok and HYPE not in body:
        return {**verdict, "verdict": "pass", "notes": "Accurate, specific and polite."}
    fixed = batch_person(item)
    out = {**verdict, "verdict": "fix", "notes": "Greeting, company or tone needed a rewrite."}
    if item["channel"] == "email":
        out["corrected_subject"], out["corrected_body"] = email_parts(fixed)
    else:
        out["corrected_body"] = linkedin_text(fixed)
    return out


def read_csv(path: str | Path) -> tuple[list[str], list[dict[str, str]]]:
    """A CSV written by the tool: UTF-8 with a BOM (Excel), header and rows."""
    raw = Path(path).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "exports must be UTF-8 with a BOM"
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        return list(reader.fieldnames or []), rows


# --- helpers that drive the tools the way the AI does ------------------------------------------


class Clock:
    """The sending clock: starts at 08:00 UTC (daytime in Singapore and London; yesterday's if
    today's is too recent) and moves forward only when the worker sleeps, so the random gaps
    cost no real time and everything it stamps lies in the past."""

    def __init__(self) -> None:
        now = datetime.now(UTC)
        start = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if start + timedelta(hours=5) > now:  # everything the worker does must lie in the past
            start -= timedelta(days=1)
        self.now = start
        self.start = self.now
        self.naps: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.naps.append(seconds)
        self.now += timedelta(seconds=seconds)


def new_offer(mcp) -> tuple[int, dict[str, Any]]:
    """Ingest the offer text and save the profile; returns the offer id and the ingest reply."""
    ingested = mcp("ingest_offer_source", text=OFFER_TEXT, name="Lanternfish VR")
    assert ingested["ok"]
    saved = mcp("save_offer_profile", offer_id=ingested["offer_id"], profile=PROFILE)
    assert saved["ok"]
    return ingested["offer_id"], ingested


def new_campaign(mcp, offer_id: int, name: str, *, channels, **options) -> int:
    """Create a campaign for both countries, add the 12 companies and approve them."""
    created = mcp(
        "create_campaign", name=name, offer_id=offer_id, countries=["SG", "GB"],
        sectors=["logistics", "healthcare", "retail"], company_size="200+ employees",
        channels=list(channels), designations=["ld", "hr"], locale="en-GB", **options,
    )  # fmt: skip
    assert created["ok"], created
    cid = created["id"]
    added = mcp(
        "add_companies", campaign_id=cid,
        companies=[
            {
                "name": c["name"], "aliases": c["aliases"], "domain": c["domain"],
                "sector": c["sector"], "country": c["country_code"], "source": "user",
            }
            for c in COMPANIES
        ],
    )  # fmt: skip
    assert added["added"] == len(COMPANIES), added
    approved = mcp("approve_companies", campaign_id=cid)
    assert approved["approved"] == len(COMPANIES), approved
    return cid


def run_chunked(mcp, tool: str, cid: int, **args) -> list[dict[str, Any]]:
    """Call a chunked tool again and again with `next_cursor` until it says done."""
    replies: list[dict[str, Any]] = []
    cursor = None
    while True:
        extra = {} if cursor is None else {"cursor": cursor}
        reply = mcp(tool, campaign_id=cid, **args, **extra)
        assert reply["ok"], reply
        replies.append(reply)
        if reply.get("done"):
            return replies
        cursor = reply["next_cursor"]
        assert len(replies) < 50, "the tool never reported done"


def all_people(mcp, cid: int, status: str) -> list[dict[str, Any]]:
    """Every person of a campaign with this status (all pages)."""
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        reply = mcp("list_people", campaign_id=cid, status=status, page_size=50, page=page)
        rows += reply["people"]
        if len(rows) >= reply["total"]:
            return rows
        page += 1


def draft_channel(mcp, cid: int, channel: str, planted: dict[int, str] | None = None):
    """The fake AI writes every message of one channel.

    `planted` maps the position of a person (0, 1, 2 ...) to a defect the writer puts in on
    purpose. A draft the instant checks refuse is rewritten without the defect and saved again,
    as the guide tells the AI to do. Returns `{"failed": [(flaw, [codes])], "saved": n}`.
    """
    planted = planted or {}
    failed: list[tuple[str, list[str]]] = []
    saved = 0
    position = 0
    cursor = None
    while True:
        batch = mcp(
            "get_drafting_batch", campaign_id=cid, channel=channel, batch_size=20,
            **({} if cursor is None else {"cursor": cursor}),
        )  # fmt: skip
        by_id = {p["person_id"]: p for p in batch["people"]}
        flaws: dict[int, str] = {}
        drafts = []
        for person in batch["people"]:
            flaws[person["person_id"]] = planted.get(position, "")
            position += 1
            drafts.append(_draft(person, channel, flaws[person["person_id"]]))
        reply = mcp("save_drafts", campaign_id=cid, drafts=drafts)
        redo = []
        for result in reply["results"]:
            if result["status"] == "qa_fail":
                codes = [i["code"] for i in result["issues"]]
                failed.append((flaws[result["person_id"]], codes))
                redo.append(_draft(by_id[result["person_id"]], channel, ""))
            else:
                assert result["status"] == "qa_pass", result
                saved += 1
        if redo:
            again = mcp("save_drafts", campaign_id=cid, drafts=redo)
            assert all(r["status"] == "qa_pass" for r in again["results"]), again
            saved += len(redo)
        if batch["done"]:
            return {"failed": failed, "saved": saved}
        cursor = batch["next_cursor"]


def _draft(person: dict[str, Any], channel: str, flaw: str) -> dict[str, Any]:
    if channel == "email":
        subject, body = email_parts(person, flaw)
        return {"person_id": person["person_id"], "channel": channel, "subject": subject,
                "body": body}  # fmt: skip
    return {"person_id": person["person_id"], "channel": channel,
            "body": linkedin_text(person, flaw)}  # fmt: skip


def review_all(mcp, cid: int) -> Counter:
    """The fake AI reads every waiting message and records its verdicts, pass after pass,
    until nothing is waiting (a message sent back with `fix` returns to the queue)."""
    verdicts: Counter = Counter()
    for _ in range(6):
        reviewed = 0
        cursor = None
        while True:
            batch = mcp(
                "get_review_batch", campaign_id=cid, batch_size=15,
                **({} if cursor is None else {"cursor": cursor}),
            )  # fmt: skip
            results = [judge(item) for item in batch["items"]]
            if results:
                recorded = mcp("record_review", campaign_id=cid, results=results)
                assert recorded["errors"] == 0, recorded
                verdicts.update(r["verdict"] for r in results)
                reviewed += len(results)
            if batch["done"]:
                break
            cursor = batch["next_cursor"]
        if reviewed == 0:
            return verdicts
    raise AssertionError("the review queue never emptied")
