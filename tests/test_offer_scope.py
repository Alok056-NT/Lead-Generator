"""One page or the whole site, and one source per project (T25, part B).

Websites are invented (`example.com`) and every request is mocked with respx.
"""

from __future__ import annotations

import json
import re

import anyio
import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from mcp.client import Client

from leadgen import mcp_server
from leadgen.dashboard import app as dash
from leadgen.services import campaign, offer, offer_web, projects

pytestmark = pytest.mark.usefixtures("ready_db")

BASE = "https://www.northwind.example.com"


@pytest.fixture(autouse=True)
def fast_crawl(monkeypatch):
    monkeypatch.setattr(offer_web, "REQUESTS_PER_SECOND", 10_000.0)


def page(body: str) -> str:
    return f"<html><head><title>Northwind</title></head><body>{body}</body></html>"


def mock_site(count: int) -> dict[str, respx.Route]:
    """A home page linking to `count` more pages (/p0 ...), every other path is a 404."""
    links = "".join(f'<a href="/p{i}">Page {i}</a>' for i in range(count))
    routes = {"/robots.txt": respx.get(f"{BASE}/robots.txt").mock(
        return_value=httpx.Response(404))}  # fmt: skip
    routes["/"] = respx.get(f"{BASE}/").mock(
        return_value=httpx.Response(200, html=page(f"<p>We train warehouse teams.</p>{links}"))
    )
    for i in range(count):
        routes[f"/p{i}"] = respx.get(f"{BASE}/p{i}").mock(
            return_value=httpx.Response(200, html=page(f"<p>Course number {i} for teams.</p>"))
        )
    respx.get(url__regex=rf"^{re.escape(BASE)}/.*").mock(return_value=httpx.Response(404))
    return routes


def pages_fetched(routes) -> list[str]:
    return sorted(path for path, route in routes.items() if route.called and path != "/robots.txt")


# --- scope -------------------------------------------------------------------------------------


@respx.mock
def test_scope_page_fetches_exactly_one_page():
    routes = mock_site(20)
    result = offer.ingest_url(BASE + "/", scope="page")
    assert result["ok"] and len(result["pages"]) == 1
    assert pages_fetched(routes) == ["/"]
    assert result["summary"] == "Read 1 page from northwind.example.com: /"


@respx.mock
def test_scope_page_ignores_max_pages():
    routes = mock_site(20)
    result = offer.ingest_url(BASE + "/", max_pages=30, scope="page")
    assert len(result["pages"]) == 1 and pages_fetched(routes) == ["/"]


@respx.mock
def test_scope_page_reads_the_given_address_not_the_home_page():
    routes = mock_site(3)
    result = offer.ingest_url(BASE + "/p2", scope="page")
    assert pages_fetched(routes) == ["/p2"]
    assert [p["url"] for p in result["pages"]] == [BASE + "/p2"]
    assert result["summary"].endswith(": /p2")


@respx.mock
def test_scope_site_reads_up_to_the_default_of_15_pages():
    routes = mock_site(30)
    result = offer.ingest_url(BASE + "/", scope="site")
    assert len(result["pages"]) == 15 and len(pages_fetched(routes)) == 15
    assert offer.ingest_url(BASE + "/")["pages"].__len__() == 15  # site is the default


@respx.mock
def test_scope_site_respects_max_pages():
    routes = mock_site(30)
    result = offer.ingest_url(BASE + "/", max_pages=4, scope="site")
    assert len(result["pages"]) == 4 and len(pages_fetched(routes)) == 4
    wide = offer.ingest_url(BASE + "/", max_pages=50, scope="site")
    assert len(wide["pages"]) == 31  # the home page and all 30 linked pages (fewer than 50)


@respx.mock
def test_scope_site_stops_at_50_pages():
    mock_site(70)
    result = offer.ingest_url(BASE + "/", max_pages=50, scope="site")
    assert len(result["pages"]) == 50
    assert result["summary"].startswith("Read 50 pages from northwind.example.com: /")
    assert result["summary"].endswith(", ...")


def test_a_bad_scope_or_page_count_is_refused_with_a_plain_error():
    for kwargs in ({"scope": "everything"}, {"scope": "site", "max_pages": 51},
                   {"scope": "site", "max_pages": 0}, {"max_pages": "ten"},
                   {"max_pages": True}):  # fmt: skip
        reply = offer.ingest_source(url=BASE + "/", **kwargs)
        assert reply["ok"] is False
        assert reply["error"]["code"] in ("invalid_scope", "invalid_max_pages")
    assert offer.list_offers() == []  # nothing was read or stored


@respx.mock
def test_the_summary_names_the_pages_that_were_read():
    mock_site(11)
    result = offer.ingest_url(BASE + "/", max_pages=12)
    assert result["summary"].startswith("Read 12 pages from northwind.example.com: /, ")
    assert result["summary"].endswith(", ...") and result["summary"].count("/p") <= 7
    short = offer.ingest_source(url=BASE + "/", max_pages=3)
    assert re.fullmatch(r"Read 3 pages from northwind\.example\.com: /(, /p\d+){2}",
                        short["summary"])  # fmt: skip
    assert offer.ingest_text("Northwind sells training.")["summary"].startswith("Read the pasted")


# --- one source per project --------------------------------------------------------------------


@pytest.fixture
def project():
    return projects.create_project("Northwind Training")["id"]


@respx.mock
def test_a_source_for_a_project_becomes_its_offer(project):
    mock_site(2)
    result = offer.ingest_source(url=BASE + "/", scope="page", project_id=project)
    assert result["ok"] and result["project_id"] == project
    assert result["replaced_offer_id"] is None and result["old_offer_deleted"] is False
    assert projects.get_project(project)["offer_id"] == result["offer_id"]
    assert result["text"] and result["summary"].startswith("Read 1 page")


def test_a_new_source_replaces_the_old_one(project):
    first = offer.ingest_source(text="Northwind sells safety training.", project_id=project)
    second = offer.ingest_source(text="Northwind now sells forklift courses.", project_id=project)
    assert second["replaced_offer_id"] == first["offer_id"]
    assert second["old_offer_deleted"] is True
    assert projects.get_project(project)["offer_id"] == second["offer_id"]
    assert [o["id"] for o in offer.list_offers()] == [second["offer_id"]]  # one source only


def test_the_old_source_stays_when_a_campaign_was_made_with_it(project):
    first = offer.ingest_source(text="Northwind sells safety training.", project_id=project)
    old = campaign.create_campaign(
        name="First run", project_id=project, countries=["SG"], channels=["email"],
        designations=["ld"],
    )
    second = offer.ingest_source(text="Northwind now sells forklift courses.", project_id=project)
    assert second["replaced_offer_id"] == first["offer_id"]
    assert second["old_offer_deleted"] is False
    assert campaign.get_campaign(old["id"])["offer_id"] == first["offer_id"]  # keeps its offer
    assert projects.get_project(project)["offer_id"] == second["offer_id"]
    new = campaign.create_campaign(
        name="Second run", project_id=project, countries=["SG"], channels=["email"],
        designations=["ld"],
    )
    assert new["offer_id"] == second["offer_id"]


@respx.mock
def test_a_failed_read_leaves_the_old_source_in_place(project):
    first = offer.ingest_source(text="Northwind sells safety training.", project_id=project)
    respx.get(f"{BASE}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE}/").mock(return_value=httpx.Response(500))
    reply = offer.ingest_source(url=BASE + "/", project_id=project)
    assert reply["ok"] is False
    assert projects.get_project(project)["offer_id"] == first["offer_id"]
    assert [o["id"] for o in offer.list_offers()] == [first["offer_id"]]


@respx.mock
def test_an_unknown_project_is_refused_before_anything_is_fetched():
    routes = mock_site(1)
    with pytest.raises(projects.ProjectError) as info:
        offer.ingest_source(url=BASE + "/", project_id=999)
    assert info.value.code == "not_found"
    assert pages_fetched(routes) == [] and offer.list_offers() == []


def test_without_a_project_nothing_is_attached():
    result = offer.ingest_source(text="Northwind sells safety training.")
    assert "project_id" not in result and result["offer_id"]


def test_the_plain_setup_sentence_is_in_the_guide():
    from leadgen.services import guide

    text = " ".join(guide.get_guide("workflow")["text"].split())
    assert offer.SOURCE_HELP in text
    assert "scope" in text and "project_id" in text


# --- MCP and dashboard -------------------------------------------------------------------------


@pytest.fixture
def mcp():
    with anyio.from_thread.start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(Client(mcp_server.build_server())) as client:

            def call(tool, /, **args):
                result = portal.call(client.call_tool, tool, args)
                assert not result.is_error, result.content
                return json.loads(result.content[0].text)

            yield call


@respx.mock
def test_the_mcp_tool_takes_scope_max_pages_and_project(mcp, project):
    routes = mock_site(10)
    reply = mcp("ingest_offer_source", url=BASE + "/", scope="page", project_id=project)
    assert reply["project_id"] == project and reply["summary"].startswith("Read 1 page")
    assert pages_fetched(routes) == ["/"]
    wide = mcp("ingest_offer_source", url=BASE + "/", scope="site", max_pages=3, project_id=project)
    assert wide["summary"].startswith("Read 3 pages") and wide["replaced_offer_id"]
    bad = mcp("ingest_offer_source", url=BASE + "/", max_pages=99)
    assert bad["ok"] is False and bad["error"]["code"] == "invalid_max_pages"


def test_the_mcp_project_tools_take_the_email_policy(mcp):
    made = mcp("create_project", name="Harbour Freight", email_policy="allow_risky")
    assert made["email_policy"] == "allow_risky"
    changed = mcp("update_project", project_id=made["id"], email_policy="verified_only")
    assert changed["email_policy"] == "verified_only"
    assert mcp("get_project", project_id=made["id"])["email_policy"] == "verified_only"


@pytest.fixture
def client():
    return TestClient(dash.create_app(), base_url="http://127.0.0.1:8765")


CSRF = {"X-LeadGen": "1"}


@respx.mock
def test_dashboard_attaches_a_website_as_one_page_or_the_whole_site(client, project):
    routes = mock_site(6)
    one = client.post(f"/api/projects/{project}/source", headers=CSRF,
                      json={"url": BASE + "/", "scope": "page"})  # fmt: skip
    assert one.status_code == 200
    body = one.json()
    assert body["summary"].startswith("Read 1 page") and "text" not in body
    assert pages_fetched(routes) == ["/"]
    wide = {"url": BASE + "/", "scope": "site", "max_pages": 4}
    site = client.post(f"/api/projects/{project}/source", headers=CSRF, json=wide).json()
    assert site["summary"].startswith("Read 4 pages") and site["replaced_offer_id"]
    assert projects.get_project(project)["offer_id"] == site["offer_id"]


def test_dashboard_attaches_pasted_text_and_refuses_bad_requests(client, project):
    reply = client.post(f"/api/projects/{project}/source", headers=CSRF,
                        json={"text": "Northwind sells safety training."})  # fmt: skip
    assert reply.status_code == 200 and reply.json()["summary"].startswith("Read the pasted")
    unknown = client.post("/api/projects/999/source", headers=CSRF, json={"text": "Some text."})
    assert unknown.status_code == 404
    assert client.post(f"/api/projects/{project}/source", json={"text": "x"}).status_code == 403
    for bad in ({"url": BASE, "scope": "everything"}, {"url": BASE, "max_pages": 51},
                {"url": BASE, "file_path": "C:/secret.docx"}):  # fmt: skip
        assert client.post(f"/api/projects/{project}/source", headers=CSRF,
                           json=bad).status_code == 422  # fmt: skip
    nothing = client.post(f"/api/projects/{project}/source", headers=CSRF, json={})
    assert nothing.status_code == 400 and nothing.json()["error"]["code"] == "one_source_needed"


def test_dashboard_shows_the_plain_setup_sentence(client):
    reply = client.get("/api/offer-source-help").json()
    assert reply["text"] == offer.SOURCE_HELP
    assert "one page, or the whole site up to 15 pages" in reply["text"]
    assert "it replaces the old one" in reply["text"]


def test_dashboard_text_scan_has_no_offer_file_route():
    # a file path from the browser would read any file on the PC; uploads come with T26
    assert not any("file_path" in line for line in dash.SourceBody.__doc__.splitlines())
    assert "file_path" not in dash.SourceBody.model_fields

