"""Companies: normalisation, aliases, dedupe, exclusions, domain check, CSV, search_web."""

from __future__ import annotations

import dns.exception
import dns.resolver
import httpx
import pytest
import respx

from leadgen import db
from leadgen.providers.base import ProviderError
from leadgen.providers.fake import FakeSearchProvider
from leadgen.providers.search import SearchResult
from leadgen.services import budget
from leadgen.services import campaign as camp
from leadgen.services import companies as co
from leadgen.services.campaign import CampaignError
from leadgen.services.companies import CompanyError

# Hosts that exist in the fake DNS: host -> record types that answer.
DNS: dict[str, set[str]] = {}


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    """No real DNS in tests: only hosts listed in DNS answer."""
    DNS.clear()

    def resolve(qname, rdtype="A", **kwargs):
        host = str(qname)
        if host.endswith(".timeout.test"):
            raise dns.resolver.LifetimeTimeout(timeout=1, errors=[])
        if host not in DNS:
            raise dns.resolver.NXDOMAIN()
        if rdtype not in DNS[host]:
            raise dns.resolver.NoAnswer()
        return object()

    monkeypatch.setattr(dns.resolver, "resolve", resolve)
    yield DNS
    DNS.clear()


@pytest.fixture(autouse=True)
def no_real_http():
    """Any HTTP request a test did not mock fails (respx), so tests never use the network."""
    with respx.mock:
        yield


@pytest.fixture
def offer_id(ready_db):
    with db.session() as conn:
        return db.insert(conn, "offers", name="Northwind Training", source_type="text")


def make_campaign(offer_id, **overrides):
    args = {
        "name": "SG logistics",
        "offer_id": offer_id,
        "countries": ["SG"],
        "sectors": ["logistics"],
        "company_size": "200-1000",
        "channels": ["linkedin"],
        "designations": ["ld"],
    }
    args.update(overrides)
    return camp.create_campaign(**args)["id"]


@pytest.fixture
def cid(offer_id):
    return make_campaign(offer_id)


def live(host, types=("A",)):
    DNS[host] = set(types)


# --- domain normalisation -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example.com", "example.com"),
        ("https://www.Example.com/about?x=1#top", "example.com"),
        ("HTTP://EXAMPLE.COM", "example.com"),
        ("www.example.com/", "example.com"),
        ("example.com:8080/path", "example.com"),
        ("example.com.", "example.com"),
        ("aria.tan@example.com", "example.com"),
        ("  shop.example.co.uk  ", "shop.example.co.uk"),
        ("BÜCHER.de", "xn--bcher-kva.de"),
        ("https://www.bücher.de/x", "xn--bcher-kva.de"),
        ("例え.jp", "xn--r8jz45g.jp"),
        ("straße.de", "xn--strae-oqa.de"),
        ("https://www.Straße.DE/x", "xn--strae-oqa.de"),
        ("xn--bcher-kva.de", "xn--bcher-kva.de"),
        ("bü..de", ""),
        ("-bü.de", ""),
        ("www.com", "www.com"),
        ("", ""),
        ("   ", ""),
        ("Northwind Logistics", ""),
        ("localhost", ""),
        ("192.168.1.1", ""),
        ("-bad-.com", ""),
        ("exa mple.com", ""),
        ("a..b.com", ""),
        ("http://[::1", ""),
    ],
)
def test_normalize_domain(raw, expected):
    assert co.normalize_domain(raw) == expected


def test_normalize_domain_long_label_is_invalid():
    assert co.normalize_domain("a" * 64 + ".com") == ""


# --- alias hygiene --------------------------------------------------------------------------


def test_aliases_name_first_short_and_generic_dropped():
    kept, reports, warnings = co._clean_aliases(
        "Northwind Logistics Pte Ltd",
        ["Northwind", "NW", "Group", "Holdings", "Singapore", "northwind", "Northwind Group"],
    )
    assert kept == ["Northwind Logistics Pte Ltd", "Northwind", "Northwind Group"]
    dropped = {r["alias"]: r["reason"] for r in reports if r["action"] == "dropped"}
    assert set(dropped) == {"NW", "Group", "Holdings", "Singapore"}
    assert "shorter" in dropped["NW"]
    assert "generic" in dropped["Group"]
    assert "country" in dropped["Singapore"]
    assert warnings == []


def test_aliases_ambiguous_caps_are_kept_and_flagged():
    kept, reports, warnings = co._clean_aliases("Salford Industrial Trading", ["SAL", "SIT", "BD"])
    assert kept == ["Salford Industrial Trading", "SAL", "SIT"]  # BD is dropped (2 letters)
    flagged = [r for r in reports if r["action"] == "kept"]
    assert [r["alias"] for r in flagged] == ["SAL", "SIT"]
    assert len(warnings) == 2
    assert all(w.startswith(co.WARNING_PREFIX) for w in warnings)


def test_aliases_all_caps_not_on_stoplist_is_not_flagged():
    kept, reports, warnings = co._clean_aliases("Acme Freight Services", ["ACMEF", "TMC"])
    assert kept == ["Acme Freight Services", "ACMEF", "TMC"]
    assert reports == [] and warnings == []


def test_ambiguous_company_name_is_flagged_but_kept():
    kept, _, warnings = co._clean_aliases("SAL", [])
    assert kept == ["SAL"]
    assert len(warnings) == 1


def test_add_stores_cleaned_aliases_and_reports_them(cid):
    live("northwind.example.com")
    result = co.add_companies(
        cid,
        [
            {
                "name": "  Northwind   Logistics ",
                "aliases": ["NW", "Northwind", "Group", "SAL"],
                "domain": "https://www.Northwind.example.com/en",
            }
        ],
    )
    assert result["added"] == 1
    row = co.list_companies(cid)["companies"][0]
    assert row["name"] == "Northwind Logistics"
    assert row["aliases"] == ["Northwind Logistics", "Northwind", "SAL"]
    assert row["domain"] == "northwind.example.com"
    reports = {(w["alias"], w["action"]) for w in result["alias_warnings"]}
    assert reports == {("NW", "dropped"), ("Group", "dropped"), ("SAL", "kept")}
    assert any("ambiguous" in w for w in row["warnings"])


# --- add: input handling --------------------------------------------------------------------


def test_add_rejects_bad_items_but_keeps_good_ones(cid):
    result = co.add_companies(
        cid,
        [
            {"name": ""},
            "Northwind",
            {"name": "Contoso Freight", "source": "magic"},
            {"name": "Fabrikam Cargo", "aliases": [1, 2]},
            {"name": "Tailspin Shipping"},
        ],
    )
    assert result["added"] == 1
    assert [r["index"] for r in result["rejected"]] == [0, 1, 2, 3]


def test_add_input_limits_and_errors(cid):
    with pytest.raises(CompanyError) as exc:
        co.add_companies(cid, [])
    assert exc.value.code == "invalid_value"
    with pytest.raises(CompanyError) as exc:
        co.add_companies(cid, [{"name": f"Co {i}"} for i in range(co.MAX_BATCH + 1)])
    assert exc.value.code == "too_many"
    assert exc.value.as_response()["ok"] is False
    with pytest.raises(CampaignError):
        co.add_companies(999, [{"name": "Northwind"}])


def test_country_defaults_to_single_campaign_country(offer_id):
    one = make_campaign(offer_id)
    two = make_campaign(offer_id, name="Two countries", countries=["SG", "MY"])
    co.add_companies(one, [{"name": "Northwind"}])
    co.add_companies(two, [{"name": "Northwind"}, {"name": "Contoso", "country": "Malaysia"}])
    assert co.list_companies(one)["companies"][0]["country"] == "SG"
    rows = co.list_companies(two)["companies"]
    assert [r["country"] for r in rows] == ["", "MY"]


def test_bad_domain_is_reported_not_stored(cid):
    result = co.add_companies(cid, [{"name": "Northwind", "domain": "not a domain"}])
    assert result["added"] == 1
    assert result["domain_failures"][0]["note"].startswith("not a valid domain")
    row = co.list_companies(cid)["companies"][0]
    assert row["domain"] == "" and row["domain_ok"] is None
    assert row["warnings"]


def test_unknown_country_is_a_warning(cid):
    co.add_companies(cid, [{"name": "Northwind", "country": "Atlantis"}])
    assert "not recognised" in co.list_companies(cid)["companies"][0]["warnings"][0]


# --- dedupe ---------------------------------------------------------------------------------


def test_dedupe_by_domain_existing_and_in_batch(cid):
    live("northwind.example.com")
    live("contoso.example.com")
    first = co.add_companies(cid, [{"name": "Northwind", "domain": "northwind.example.com"}])
    existing_id = first["companies"][0]["id"]
    result = co.add_companies(
        cid,
        [
            {"name": "Northwind Logistics", "domain": "https://www.northwind.example.com/"},
            {"name": "Contoso", "domain": "contoso.example.com"},
            {"name": "Contoso Ltd", "domain": "CONTOSO.example.com"},
        ],
    )
    assert result["added"] == 1
    assert len(result["duplicates"]) == 2
    assert result["duplicates"][0]["existing_id"] == existing_id
    assert result["duplicates"][0]["reason"] == "same domain"
    assert result["duplicates"][1]["existing_id"] is not None


def test_dedupe_by_name_when_no_domain(cid):
    co.add_companies(cid, [{"name": "Northwind Logistics Pte Ltd"}])
    result = co.add_companies(
        cid,
        [
            {"name": "northwind  logistics"},
            {"name": "Northwind Logistics Inc."},
            {"name": "Northwind Trading"},
        ],
    )
    assert result["added"] == 1
    assert [d["reason"] for d in result["duplicates"]] == ["same name", "same name"]
    assert co.list_companies(cid)["total"] == 2


def test_name_without_domain_matches_existing_company_with_no_domain(cid):
    live("northwind.example.com")
    co.add_companies(cid, [{"name": "Northwind"}])
    result = co.add_companies(cid, [{"name": "Northwind", "domain": "northwind.example.com"}])
    assert result["added"] == 0
    assert result["duplicates"][0]["reason"] == "same name as a company with no domain"


def test_same_name_different_domains_are_both_kept(cid):
    live("alpha.example.com")
    live("alpha.example.org")
    result = co.add_companies(
        cid,
        [
            {"name": "Alpha Freight", "domain": "alpha.example.com"},
            {"name": "Alpha Freight", "domain": "alpha.example.org"},
        ],
    )
    assert result["added"] == 2


def test_dedupe_is_per_campaign(offer_id):
    a = make_campaign(offer_id)
    b = make_campaign(offer_id, name="Another run")
    assert co.add_companies(a, [{"name": "Northwind"}])["added"] == 1
    assert co.add_companies(b, [{"name": "Northwind"}])["added"] == 1


# --- exclusions -----------------------------------------------------------------------------


def test_domain_like_company_and_competitor_names_also_match_by_name(offer_id):
    cid = make_campaign(
        offer_id,
        exclusions={"companies": ["Fabrikam.io"], "competitors": ["Rival.io"]},
    )
    result = co.add_companies(
        cid,
        [
            {"name": "Rival.io"},
            {"name": "Alias Only", "aliases": ["Fabrikam.io Pte Ltd"]},
            {"name": "Fabrikam.io"},
            {"name": "Other", "domain": "https://www.rival.io/"},
            {"name": "Other2", "domain": "app.fabrikam.io"},
            {"name": "Unrelated", "domain": "example.com"},
        ],
    )
    reasons = {e["name"]: e["reason"] for e in result["excluded"]}
    assert reasons == {
        "Rival.io": "competitor 'Rival.io'",
        "Alias Only": "excluded company 'Fabrikam.io'",
        "Fabrikam.io": "excluded company 'Fabrikam.io'",
        "Other": "competitor 'Rival.io'",
        "Other2": "excluded company 'Fabrikam.io'",
    }


def test_exclusions_by_name_domain_and_competitor(offer_id):
    cid = make_campaign(
        offer_id,
        exclusions={
            "companies": ["Contoso Freight"],
            "domains": ["https://www.fabrikam.example.com/"],
            "competitors": ["Trainer Rivals", "rival.example.com"],
        },
    )
    live("ok.example.com")
    result = co.add_companies(
        cid,
        [
            {"name": "Contoso Freight Pte Ltd"},
            {"name": "Fabrikam Cargo", "domain": "cargo.fabrikam.example.com"},
            {"name": "Trainer Rivals"},
            {"name": "Some Rival", "domain": "rival.example.com"},
            {"name": "Alias Match", "aliases": ["Contoso Freight"]},
            {"name": "Fine Co", "domain": "ok.example.com"},
        ],
    )
    assert result["added"] == 1
    assert len(result["excluded"]) == 5
    reasons = [e["reason"] for e in result["excluded"]]
    assert reasons[0] == "excluded company 'Contoso Freight'"
    assert "excluded domain" in reasons[1]
    assert reasons[2] == "competitor 'Trainer Rivals'"
    assert reasons[3] == "competitor 'rival.example.com'"
    stored = co.list_companies(cid, status="excluded")
    assert stored["total"] == 5
    assert co.list_companies(cid, status="candidate")["total"] == 1
    # excluded companies are not domain-checked
    assert all(c["domain_ok"] is None for c in stored["companies"])


# --- domain check ---------------------------------------------------------------------------


def test_domain_ok_with_head(cid):
    live("northwind.example.com")
    route = respx.head("https://northwind.example.com/").mock(return_value=httpx.Response(200))
    result = co.add_companies(cid, [{"name": "Northwind", "domain": "northwind.example.com"}])
    assert route.called
    assert result["domain_failures"] == []
    assert result["companies"][0]["domain_ok"] is True
    row = co.list_companies(cid)["companies"][0]
    assert row["domain_ok"] is True
    assert "Domain check: ok" in row["notes"]


def test_domain_mx_only_counts_as_dns_then_homepage_must_answer(cid):
    live("mail.example.com", ("MX",))
    respx.route(host="mail.example.com").mock(side_effect=httpx.ConnectError("boom"))
    result = co.add_companies(cid, [{"name": "Mail Only", "domain": "mail.example.com"}])
    assert result["added"] == 1  # a failed check never blocks the insert
    failure = result["domain_failures"][0]
    assert "DNS exists but the website did not answer" in failure["note"]
    assert co.list_companies(cid)["companies"][0]["domain_ok"] is False


def test_head_refused_falls_back_to_get(cid):
    live("northwind.example.com")
    respx.head("https://northwind.example.com/").mock(return_value=httpx.Response(405))
    get = respx.get("https://northwind.example.com/").mock(return_value=httpx.Response(200))
    result = co.add_companies(cid, [{"name": "Northwind", "domain": "northwind.example.com"}])
    assert get.called
    assert result["companies"][0]["domain_ok"] is True


def test_https_fails_then_http_works(cid):
    live("old.example.com")
    respx.route(scheme="https", host="old.example.com").mock(side_effect=httpx.ConnectError("x"))
    respx.head("http://old.example.com/").mock(return_value=httpx.Response(200))
    result = co.add_companies(cid, [{"name": "Old Site", "domain": "old.example.com"}])
    assert result["companies"][0]["domain_ok"] is True


def test_blocked_homepage_still_counts_as_existing(cid):
    live("guarded.example.com")
    respx.head("https://guarded.example.com/").mock(return_value=httpx.Response(403))
    respx.get("https://guarded.example.com/").mock(return_value=httpx.Response(403))
    result = co.add_companies(cid, [{"name": "Guarded", "domain": "guarded.example.com"}])
    assert result["companies"][0]["domain_ok"] is True
    assert "HTTP 403" in co.list_companies(cid)["companies"][0]["notes"]


def test_server_error_is_a_failure(cid):
    live("down.example.com")
    respx.route(host="down.example.com").mock(return_value=httpx.Response(503))
    result = co.add_companies(cid, [{"name": "Down", "domain": "down.example.com"}])
    assert "HTTP 503" in result["domain_failures"][0]["note"]


def test_redirect_to_other_site_is_recorded_and_warned(cid):
    live("old.example.com")
    respx.head("https://old.example.com/").mock(
        return_value=httpx.Response(301, headers={"Location": "https://new.example.org/"})
    )
    respx.head("https://new.example.org/").mock(return_value=httpx.Response(200))
    result = co.add_companies(cid, [{"name": "Old Name", "domain": "old.example.com"}])
    assert result["companies"][0]["domain_ok"] is True
    row = co.list_companies(cid)["companies"][0]
    assert "redirects to new.example.org" in row["notes"]
    assert row["warnings"] and "different site" in row["warnings"][0]


def test_idn_domain_is_checked_without_false_redirect_warning(cid):
    live("xn--bcher-kva.de")
    respx.route(url__regex=r"https?://(xn--bcher-kva\.de|bücher\.de)/").mock(
        return_value=httpx.Response(200)
    )
    result = co.add_companies(cid, [{"name": "Buchhandlung Nord", "domain": "bücher.de"}])
    assert result["domain_failures"] == []
    row = co.list_companies(cid)["companies"][0]
    assert row["domain"] == "xn--bcher-kva.de"
    assert row["domain_ok"] is True
    assert row["warnings"] == []
    assert "redirects" not in row["notes"]
    assert co.check_domain("xn--bcher-kva.de").redirect_warning == ""
    assert co.approve_all(cid) == {"ok": True, "approved": 1, "skipped": []}


def test_redirect_to_www_or_subdomain_is_not_a_warning(cid):
    live("example.net")
    respx.head("https://example.net/").mock(
        return_value=httpx.Response(301, headers={"Location": "https://www.example.net/"})
    )
    respx.head("https://www.example.net/").mock(return_value=httpx.Response(200))
    co.add_companies(cid, [{"name": "Plain", "domain": "example.net"}])
    assert co.list_companies(cid)["companies"][0]["warnings"] == []


def test_redirect_to_private_address_is_refused(cid):
    live("sneaky.example.com")
    respx.head("https://sneaky.example.com/").mock(
        return_value=httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"})
    )
    admin = respx.route(host="127.0.0.1").mock(return_value=httpx.Response(200))
    result = co.add_companies(cid, [{"name": "Sneaky", "domain": "sneaky.example.com"}])
    assert not admin.called
    assert "local or private" in result["domain_failures"][0]["note"]


def test_dns_failures(cid):
    live("nomx.example.com", ())  # exists in DNS but has no records of any type
    result = co.add_companies(
        cid,
        [
            {"name": "Ghost", "domain": "ghost.example.com"},
            {"name": "Empty", "domain": "nomx.example.com"},
            {"name": "Slow", "domain": "slow.timeout.test"},
        ],
    )
    assert result["added"] == 3
    notes = {f["name"]: f["note"] for f in result["domain_failures"]}
    assert "does not exist" in notes["Ghost"]
    assert "no A, AAAA or MX" in notes["Empty"]
    assert "timed out" in notes["Slow"]
    assert all(c["domain_ok"] is False for c in co.list_companies(cid)["companies"])


def test_dns_other_error_is_reported(monkeypatch, cid):
    def broken(qname, rdtype="A", **kwargs):
        raise dns.resolver.NoNameservers()

    monkeypatch.setattr(dns.resolver, "resolve", broken)
    result = co.add_companies(cid, [{"name": "Northwind", "domain": "northwind.example.com"}])
    assert "DNS lookup failed" in result["domain_failures"][0]["note"]


def test_dns_checks_a_then_aaaa_then_mx(monkeypatch):
    asked = []

    def resolve(qname, rdtype="A", **kwargs):
        asked.append(rdtype)
        if rdtype == "MX":
            return object()
        raise dns.resolver.NoAnswer()

    monkeypatch.setattr(dns.resolver, "resolve", resolve)
    assert co._dns_exists("example.com") == (True, "")
    assert asked == ["A", "AAAA", "MX"]


def test_check_error_is_contained(monkeypatch, cid):
    live("boom.example.com")

    def explode(*args, **kwargs):
        raise RuntimeError("kaput")

    monkeypatch.setattr(co, "_homepage", explode)
    result = co.add_companies(cid, [{"name": "Boom", "domain": "boom.example.com"}])
    assert "check error (RuntimeError)" in result["domain_failures"][0]["note"]


def test_checks_run_in_parallel_threads(cid):
    import threading

    names = set()
    # every check waits for CHECK_WORKERS checks to be running at once: serial runs time out
    barrier = threading.Barrier(co.CHECK_WORKERS, timeout=10)

    def fake_check(host):
        names.add(threading.current_thread().name)
        barrier.wait()
        return co.DomainCheck(True, f"{co.CHECK_PREFIX}ok")

    rows = [
        {"id": i, "name": f"C{i}", "domain": f"c{i}.example.com", "notes": ""} for i in range(1, 9)
    ]
    # the rows must exist for the update step
    with db.session() as conn:
        for r in rows:
            r["id"] = db.insert(
                conn, "companies", campaign_id=cid, name=r["name"], domain=r["domain"]
            )
    outcome = co._run_checks(rows, check=fake_check)
    assert outcome["checked"] == 8 and outcome["unchecked"] == []
    assert len(names) == co.CHECK_WORKERS


def test_in_flight_checks_are_not_waited_for_past_the_deadline(cid):
    import threading
    import time

    release = threading.Event()

    def fake_check(host):
        if host == "fast.example.com":
            return co.DomainCheck(True, f"{co.CHECK_PREFIX}ok")
        release.wait(30)  # a very slow site
        return co.DomainCheck(True, f"{co.CHECK_PREFIX}ok")

    domains = ["fast.example.com"] + [f"slow{i}.example.com" for i in range(6)]
    rows = []
    with db.session() as conn:
        for i, d in enumerate(domains):
            rows.append(
                {"id": db.insert(conn, "companies", campaign_id=cid, name=f"C{i}", domain=d),
                 "name": f"C{i}", "domain": d, "notes": ""}
            )  # fmt: skip
    started = time.monotonic()
    try:
        outcome = co._run_checks(rows, check=fake_check, deadline_seconds=0.3)
        elapsed = time.monotonic() - started
    finally:
        release.set()
    assert elapsed < 5  # returned at the deadline, not after the slow checks
    assert outcome["checked"] == 1
    assert [u["name"] for u in outcome["unchecked"]] == [f"C{i}" for i in range(1, 7)]
    assert outcome["domain_failures"] == []
    with db.session() as conn:
        assert db.get(conn, "companies", rows[0]["id"])["domain_ok"] == 1
        assert db.get(conn, "companies", rows[1]["id"])["domain_ok"] is None


def test_not_checked_then_check_domains(cid, monkeypatch):
    live("a.example.com")
    respx.route(host="a.example.com").mock(return_value=httpx.Response(200))
    original = co._run_checks
    monkeypatch.setattr(
        co, "_run_checks", lambda rows, check=None, deadline_seconds=0: original(rows, check, -1.0)
    )
    result = co.add_companies(cid, [{"name": "Alpha", "domain": "a.example.com"}])
    assert result["added"] == 1
    assert result["domains_not_checked"][0]["name"] == "Alpha"
    assert "check_domains" in result["next_step"]
    assert co.list_companies(cid)["companies"][0]["domain_ok"] is None
    monkeypatch.setattr(co, "_run_checks", original)
    done = co.check_domains(cid)
    assert done["checked"] == 1 and done["unchecked"] == []
    assert co.list_companies(cid)["companies"][0]["domain_ok"] is True
    again = co.check_domains(cid)  # nothing left that is unchecked
    assert again["checked"] == 0


def test_check_domains_recheck_replaces_old_lines(cid):
    co.add_companies(cid, [{"name": "Alpha", "domain": "a.example.com", "notes": "my note"}])
    row = co.list_companies(cid)["companies"][0]
    assert row["domain_ok"] is False
    live("a.example.com")
    respx.route(host="a.example.com").mock(return_value=httpx.Response(200))
    co.check_domains(cid, [row["id"]])
    row = co.list_companies(cid)["companies"][0]
    assert row["domain_ok"] is True
    assert row["notes"].splitlines() == ["my note", "Domain check: ok"]
    with pytest.raises(CompanyError):
        co.check_domains(cid, [])


# --- list / status / approve ----------------------------------------------------------------


def seed(cid, n):
    co.add_companies(cid, [{"name": f"Company {i:02d}"} for i in range(n)])


def test_list_pages_and_filters(cid):
    seed(cid, 7)
    page1 = co.list_companies(cid, page=1, page_size=3)
    assert [c["name"] for c in page1["companies"]] == ["Company 00", "Company 01", "Company 02"]
    assert page1["total"] == 7 and page1["has_more"] is True
    assert co.list_companies(cid, page=3, page_size=3)["has_more"] is False
    assert co.list_companies(cid, page_size=500)["page_size"] == co.PAGE_SIZE_MAX
    assert co.list_companies(cid, status="approved")["total"] == 0
    assert page1["counts"] == {"candidate": 7, "approved": 0, "excluded": 0}
    with pytest.raises(CompanyError):
        co.list_companies(cid, status="bogus")


def test_set_status_with_reason_and_unknown_ids(offer_id, cid):
    other = make_campaign(offer_id, name="Other campaign")
    co.add_companies(other, [{"name": "Elsewhere"}])
    other_id = co.list_companies(other)["companies"][0]["id"]
    seed(cid, 3)
    ids = [c["id"] for c in co.list_companies(cid)["companies"]]
    result = co.set_company_status(cid, [ids[0], ids[1], other_id, 9999], "excluded", "competitor")
    assert result["updated"] == 2
    assert result["not_found"] == [other_id, 9999]
    rows = {c["id"]: c for c in co.list_companies(cid)["companies"]}
    assert rows[ids[0]]["status"] == "excluded"
    assert "Status note (excluded): competitor" in rows[ids[0]]["notes"]
    assert rows[ids[2]]["status"] == "candidate"
    assert co.list_companies(other)["companies"][0]["status"] == "candidate"


def test_set_status_validation_and_unverified(cid):
    co.add_companies(cid, [{"name": "Northwind", "domain": "northwind.example.com"}])
    row_id = co.list_companies(cid)["companies"][0]["id"]
    approved = co.set_company_status(cid, [row_id], "approved")
    assert approved["unverified"] == [{"id": row_id, "name": "Northwind"}]
    for bad in ("done", None):
        with pytest.raises(CompanyError):
            co.set_company_status(cid, [row_id], bad)
    for bad_ids in ([], "1", [True], [1.5]):
        with pytest.raises(CompanyError):
            co.set_company_status(cid, bad_ids, "approved")
    with pytest.raises(CampaignError):
        co.set_company_status(999, [1], "approved")


def test_approve_all_skips_failures_warnings_and_unchecked(cid):
    live("good.example.com")
    respx.route(host="good.example.com").mock(return_value=httpx.Response(200))
    co.add_companies(
        cid,
        [
            {"name": "Good Co", "domain": "good.example.com"},
            {"name": "No Domain Co"},
            {"name": "Dead Co", "domain": "dead.example.com"},
            {"name": "Salford Industrial Trading", "aliases": ["SAL"]},
            {"name": "Typo Co", "domain": "not a domain"},
        ],
    )
    with db.session() as conn:  # a company whose domain was never checked
        db.insert(conn, "companies", campaign_id=cid, name="Later Co", domain="later.example.com")
    result = co.approve_all(cid)
    assert result["approved"] == 2
    skipped = {s["name"]: s["reasons"] for s in result["skipped"]}
    assert set(skipped) == {"Dead Co", "Salford Industrial Trading", "Typo Co", "Later Co"}
    assert skipped["Dead Co"] == ["domain check failed"]
    assert skipped["Later Co"] == ["domain not checked yet"]
    assert "ambiguous" in skipped["Salford Industrial Trading"][0]
    approved = {c["name"] for c in co.list_companies(cid, status="approved")["companies"]}
    assert approved == {"Good Co", "No Domain Co"}
    assert co.approve_all(cid)["approved"] == 0  # idempotent: skipped ones stay candidates


# --- CSV import -----------------------------------------------------------------------------

CSV_TEXT = (
    "company,aliases,domain,industry,notes\n"
    "Northwind Logistics,Northwind;NW Logistics,https://www.northwind.example.com/,Logistics,"
    '"Big, in Asia"\n'
    "Contoso Freight,,contoso.example.com,Freight,\n"
    ",,,,\n"
    "Café Fabrikam,Fabrikam,,Food,\n"
    "Northwind Logistics,,,,duplicate row\n"
)


def test_import_csv(cid, tmp_path):
    live("northwind.example.com")
    respx.route(host="northwind.example.com").mock(return_value=httpx.Response(200))
    path = tmp_path / "nxt.csv"
    path.write_text(CSV_TEXT, encoding="utf-8-sig")
    result = co.import_companies_csv(cid, str(path))
    assert result["rows_read"] == 5 and result["rows_skipped"] == 1
    assert result["added"] == 3
    assert [d["name"] for d in result["duplicates"]] == ["Northwind Logistics"]
    rows = {c["name"]: c for c in co.list_companies(cid)["companies"]}
    nw = rows["Northwind Logistics"]
    assert nw["aliases"] == ["Northwind Logistics", "Northwind", "NW Logistics"]
    assert nw["sector"] == "Logistics" and nw["source"] == "user"
    assert nw["notes"].splitlines()[0] == "Big, in Asia"
    assert nw["domain"] == "northwind.example.com" and nw["domain_ok"] is True
    assert rows["Contoso Freight"]["domain_ok"] is False  # not in fake DNS
    assert [f["name"] for f in result["domain_failures"]] == ["Contoso Freight"]
    assert "Café Fabrikam" in rows
    assert result["domains_checked"] == 2


def test_import_csv_cp1252_and_header_case(cid, tmp_path):
    path = tmp_path / "excel.csv"
    path.write_bytes("Company,Domain\nCafé Fabrikam,\n".encode("cp1252"))
    result = co.import_companies_csv(cid, str(path))
    assert result["added"] == 1
    assert co.list_companies(cid)["companies"][0]["name"] == "Café Fabrikam"


def test_import_csv_errors(cid, tmp_path):
    with pytest.raises(CompanyError) as exc:
        co.import_companies_csv(cid, str(tmp_path / "missing.csv"))
    assert exc.value.code == "file_not_found"
    bad = tmp_path / "bad.csv"
    bad.write_text("name_of_org,site\nNorthwind,x.example.com\n", encoding="utf-8")
    with pytest.raises(CompanyError) as exc:
        co.import_companies_csv(cid, str(bad))
    assert exc.value.code == "bad_csv"
    empty = tmp_path / "empty.csv"
    empty.write_text("company,domain\n,\n", encoding="utf-8")
    with pytest.raises(CompanyError) as exc:
        co.import_companies_csv(cid, str(empty))
    assert exc.value.code == "bad_csv"
    binary = tmp_path / "bin.csv"
    binary.write_bytes(b"company\n\x81\x8d\x8f\x90\n")
    with pytest.raises(CompanyError) as exc:
        co.import_companies_csv(cid, str(binary))
    assert exc.value.code == "unreadable_file"
    with pytest.raises(CampaignError):
        co.import_companies_csv(999, str(bad))


def test_import_csv_size_limits(cid, tmp_path, monkeypatch):
    path = tmp_path / "big.csv"
    path.write_text("company\n" + "\n".join(f"Co {i}" for i in range(5)), encoding="utf-8")
    monkeypatch.setattr(co, "MAX_CSV_ROWS", 3)
    with pytest.raises(CompanyError) as exc:
        co.import_companies_csv(cid, str(path))
    assert exc.value.code == "too_many"
    monkeypatch.setattr(co, "MAX_CSV_BYTES", 10)
    with pytest.raises(CompanyError) as exc:
        co.import_companies_csv(cid, str(path))
    assert exc.value.code == "file_too_big"


# --- research queries -----------------------------------------------------------------------


def test_research_queries(offer_id):
    cid = make_campaign(offer_id, countries=["SG", "MY"], sectors=["logistics"])
    queries = co.research_queries(cid)
    assert "largest logistics companies in Singapore" in queries
    assert "top logistics employers Malaysia list" in queries
    assert "logistics companies Singapore 1000 employees" in queries
    assert "site:wikipedia.org list of logistics companies Malaysia" in queries
    assert len(queries) == len(set(queries)) == 8


def test_research_queries_without_sector(offer_id):
    cid = make_campaign(offer_id, sectors=[])
    assert co.research_queries(cid)[0] == "largest companies in Singapore"


# --- search_web -----------------------------------------------------------------------------

R = SearchResult("Northwind Logistics", "https://northwind.example.com/", "Freight.", 1)
Q = "largest logistics companies in Singapore"


def test_search_web_returns_results_and_uses_country_gl(ready_db):
    fake = FakeSearchProvider({Q: [R]})
    out = co.search_web(Q, "Singapore", 5, provider=fake)
    assert out == [
        {
            "title": "Northwind Logistics",
            "link": "https://northwind.example.com/",
            "snippet": "Freight.",
            "position": 1,
        }
    ]
    assert fake.calls == [(Q, "sg", 5)]


def test_search_web_uses_cache(ready_db, cid):
    fake = FakeSearchProvider({Q: [R]})
    co.search_web(Q, "SG", 10, campaign_id=cid, provider=fake)
    co.search_web(Q, "SG", 10, campaign_id=cid, provider=fake)
    assert len(fake.calls) == 1
    assert budget.used(cid, "search") == 1  # the cache hit cost nothing


def test_search_web_marks_low_confidence(ready_db):
    low = SearchResult("T", "https://example.com/", "S", 1, low_confidence=True)
    out = co.search_web("q", provider=FakeSearchProvider({"q": [low]}))
    assert out[0]["low_confidence"] is True


def test_search_web_budget_counts_and_blocks(offer_id):
    cid = make_campaign(offer_id, budget={"search": 2})
    fake = FakeSearchProvider(lambda q, gl, n: [R])
    co.search_web("one", campaign_id=cid, provider=fake)
    co.search_web("two", campaign_id=cid, provider=fake)
    assert budget.used(cid, "search") == 2
    with pytest.raises(CompanyError) as exc:
        co.search_web("three", campaign_id=cid, provider=fake)
    reply = exc.value.as_response()
    assert reply["ok"] is False
    assert reply["budget_reached"] == {
        "kind": "search",
        "used": 2.0,
        "limit": 2.0,
        "how_to_continue": "Ask the user, then repeat the call with confirm=true.",
    }
    assert reply["fallback_available"] == "ai_web_search"
    assert len(fake.calls) == 2  # blocked before any call
    # a cached query is still free once the budget is used up
    assert co.search_web("one", campaign_id=cid, provider=fake)[0]["title"] == R.title
    # confirm=true goes over the limit and is still counted
    co.search_web("three", campaign_id=cid, provider=fake, confirm=True)
    assert budget.used(cid, "search") == 3


def test_search_web_default_budget_is_300(offer_id):
    cid = make_campaign(offer_id)  # no budget given: PLAN default
    budget.charge(cid, "search", 300)
    with pytest.raises(CompanyError) as exc:
        co.search_web("x", campaign_id=cid, provider=FakeSearchProvider())
    assert exc.value.as_response()["budget_reached"]["limit"] == 300.0


def test_search_web_large_num_costs_more(offer_id):
    cid = make_campaign(offer_id, budget={"search": 1})
    with pytest.raises(CompanyError):
        co.search_web("x", num=20, campaign_id=cid, provider=FakeSearchProvider())


def test_search_web_without_campaign_has_no_budget(ready_db):
    fake = FakeSearchProvider()
    for i in range(3):
        co.search_web(f"q{i}", provider=fake)
    assert len(fake.calls) == 3


def test_search_web_needs_key(ready_db):
    with pytest.raises(CompanyError) as exc:
        co.search_web("anything")  # no key set in the temp workspace
    reply = exc.value.as_response()
    assert reply["needs_key"]["provider"] == "serper"
    assert reply["fallback_available"] == "ai_web_search"


def test_search_web_quota_and_other_errors(ready_db):
    quota = FakeSearchProvider(fail_with=ProviderError("quota", "none left"))
    with pytest.raises(CompanyError) as exc:
        co.search_web("x", provider=quota)
    assert "quota_exhausted" in exc.value.as_response()
    auth = FakeSearchProvider(fail_with=ProviderError("auth", "bad key"))
    with pytest.raises(CompanyError) as exc:
        co.search_web("x", provider=auth)
    reply = exc.value.as_response()
    assert reply["error"]["code"] == "auth"
    assert exc.value.code == "auth"


def test_search_web_failed_search_costs_nothing(offer_id):
    cid = make_campaign(offer_id)
    fake = FakeSearchProvider(fail_with=ProviderError("rate_limited", "slow down"))
    with pytest.raises(CompanyError):
        co.search_web("x", campaign_id=cid, provider=fake)
    assert budget.used(cid, "search") == 0


@pytest.mark.parametrize("query", ["", "   "])
def test_search_web_validation(ready_db, query):
    with pytest.raises(CompanyError):
        co.search_web(query, provider=FakeSearchProvider())


@pytest.mark.parametrize("num", [0, 21, True, "10", 2.5])
def test_search_web_bad_num(ready_db, num):
    with pytest.raises(CompanyError) as exc:
        co.search_web("x", num=num, provider=FakeSearchProvider())
    assert exc.value.code == "invalid_value"


def test_search_web_bad_country_and_campaign(ready_db):
    with pytest.raises(CompanyError) as exc:
        co.search_web("x", "Atlantis", provider=FakeSearchProvider())
    assert exc.value.code == "invalid_country"
    with pytest.raises(CampaignError):
        co.search_web("x", campaign_id=999, provider=FakeSearchProvider())


def test_search_web_picks_configured_provider(monkeypatch, ready_db):
    fake = FakeSearchProvider({"q": [R]})
    monkeypatch.setattr(co.registry, "get_search_provider", lambda: fake)
    assert co.search_web("q")[0]["title"] == R.title


# --- budget module --------------------------------------------------------------------------


def test_budget_helpers(offer_id):
    cid = make_campaign(offer_id)
    assert budget.limit({"search": 5}, "search") == 5
    assert budget.limit({}, "hunter") == 25 and budget.limit(None, "verify") == 200
    assert budget.limit(None, "unknown") == 0
    assert budget.used(cid, "search") == 0
    assert budget.charge(cid, "search", 2) == 2
    assert budget.charge(cid, "search", 0.5) == 2.5
    assert budget.charge(cid, "search", 0) == 2.5
    assert budget.check(cid, {"search": 3}, "search", 0.5) is None
    assert budget.check(cid, {"search": 3}, "search", 1)["budget_reached"]["used"] == 2.5
    assert budget.check(cid, {"search": 3}, "search", 99, confirm=True) is None
    with db.session() as conn:
        conn.execute("UPDATE settings SET value = 'oops'")
    assert budget.used(cid, "search") == 0
