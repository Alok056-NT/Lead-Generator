"""The same mini flow over the real MCP stdio transport (T19).

`python -m leadgen mcp` is started as a child process with a temporary `LEADGEN_HOME`. Search
answers come from canned data through the fake-provider switch, which is honoured only when
`LEADGEN_FAKE_PROVIDERS=1` and `LEADGEN_ALLOW_FAKES=1` are both set. The flow is LinkedIn-only
and the three companies have no website address, so the child needs no DNS, HTTP or API key.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import anyio
import anyio.from_thread
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from universe import COMPANIES, SEARCH_RESULTS
from world import PROFILE, draft_channel, read_csv, review_all, run_chunked

COMPANY_NAMES = ["Aster Harbour Logistics", "Bayline Insurance", "Crescent Marine Services"]
MARKERS = {"L&D / Training heads": '"training manager"', "HR leaders": '"HR director"'}
EXPECTED_CONFIRMED = 17  # 6 + 5 + 6 people; the rest of the 30 results go to review


def fake_data(path: Path) -> Path:
    """Canned search rules for the three companies, without the fabricated results."""
    rules = []
    for key, rows in SEARCH_RESULTS.items():
        company, label = key.split("|")
        if company not in COMPANY_NAMES:
            continue
        rules.append(
            {
                "contains": [f'"{company}"', MARKERS[label]],
                "results": [
                    {"title": r["title"], "link": r["link"], "snippet": r["snippet"]}
                    for r in rows
                    if not r.get("fabricated")
                ],
            }
        )
    path.write_text(json.dumps({"rules": rules}), encoding="utf-8")
    return path


def params(workspace: Path, **extra: str) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "leadgen", "mcp"],
        env={"LEADGEN_HOME": str(workspace), **extra},
    )


class Session:
    """A blocking `call(tool, **args)` over a real stdio connection to a child server."""

    def __init__(self, server: StdioServerParameters) -> None:
        self.server = server

    def __enter__(self):
        self.portal_cm = anyio.from_thread.start_blocking_portal()
        self.portal = self.portal_cm.__enter__()
        self.client_cm = self.portal.wrap_async_context_manager(Client(self.server))
        self.client = self.client_cm.__enter__()
        return self._call

    def __exit__(self, *exc):
        self.client_cm.__exit__(*exc)
        self.portal_cm.__exit__(*exc)

    def _call(self, tool, /, **args):
        result = self.portal.call(self.client.call_tool, tool, args)
        assert not result.is_error, result.content
        return json.loads(result.content[0].text)


def test_mini_flow_over_stdio_with_fake_providers(workspace, tmp_path):
    data = fake_data(tmp_path / "fake-search.json")
    server = params(
        workspace, LEADGEN_FAKE_PROVIDERS="1", LEADGEN_ALLOW_FAKES="1", LEADGEN_FAKE_DATA=str(data)
    )
    with Session(server) as mcp:
        # the server answers over stdout only with protocol messages (a stray print would break it)
        assert mcp("get_status")["next_step"]["tool"] == "create_project"

        ingested = mcp("ingest_offer_source", text="Lanternfish VR builds short VR training.")
        saved = mcp("save_offer_profile", offer_id=ingested["offer_id"], profile=PROFILE)
        assert saved["profile"]["name"] == "Lanternfish VR"
        project = mcp("create_project", name="Transport check", offer_id=ingested["offer_id"])
        created = mcp(
            "create_campaign", name="Transport check", project_id=project["id"],
            countries=["SG"], sectors=["logistics"], company_size="200+ employees",
            channels=["linkedin"], designations=["ld", "hr"], locale="en-GB",
        )  # fmt: skip
        cid = created["id"]
        added = mcp(
            "add_companies", campaign_id=cid,
            companies=[
                {"name": c["name"], "aliases": c["aliases"], "country": "SG", "source": "user"}
                for c in COMPANIES
                if c["name"] in COMPANY_NAMES
            ],
        )  # fmt: skip
        assert added["added"] == 3
        assert mcp("approve_companies", campaign_id=cid)["approved"] == 3

        estimate = mcp("estimate_people_search", campaign_id=cid)
        assert estimate["searches"] == 6
        found = run_chunked(mcp, "find_people", cid, confirm=True)
        counts = found[-1]["campaign_people"]
        assert counts["confirmed"] == EXPECTED_CONFIRMED and counts["review"] > 0

        drafted = draft_channel(mcp, cid, "linkedin_message", {0: "forbidden", 1: "hype"})
        assert drafted["saved"] == EXPECTED_CONFIRMED and len(drafted["failed"]) == 1
        qa = mcp("run_qa", campaign_id=cid)
        assert qa["people"]["with_blocks"] == 0
        verdicts = review_all(mcp, cid)
        assert verdicts["fix"] == 1 and verdicts["pass"] >= EXPECTED_CONFIRMED

        exported = mcp("export_campaign", campaign_id=cid, format="lemlist")
        _, rows = read_csv(exported["path"])
        assert len(rows) == exported["exported"] == EXPECTED_CONFIRMED
        assert all(r["linkedinMessage"] for r in rows)
        assert {r["evidenceOrigin"] for r in rows} == {"api"}

    assert Path(exported["path"]).is_file()  # the child wrote it into the temporary workspace


@pytest.mark.parametrize(
    "env",
    [
        {"LEADGEN_FAKE_PROVIDERS": "1"},  # asked for fakes, not allowed
        {"LEADGEN_ALLOW_FAKES": "1"},  # allowed, not asked for
    ],
)
def test_fake_providers_need_both_switches(workspace, tmp_path, env):
    data = fake_data(tmp_path / "fake-search.json")
    with Session(params(workspace, LEADGEN_FAKE_DATA=str(data), **env)) as mcp:
        reply = mcp("search_web", query='"Aster Harbour Logistics" training manager')
    assert reply["ok"] is False and reply["error"]["code"] == "needs_key"
