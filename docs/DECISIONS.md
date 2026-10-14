# Decisions and verified facts

Every external fact the code depends on must be recorded here with a source and the date it was checked. Re-verify anything older than 6 months before relying on it.

## Design decisions (2026-10-14, planner)
| # | Decision | Why |
|---|---|---|
| D1 | Local-first MCP server + CLI + local dashboard; no hosted service in v1 | Lead data and keys stay on the user's machine; simplest to run |
| D2 | Host AI writes messages and proposes companies; tool code never calls an LLM | Works with any AI host, no extra AI API key, no extra cost |
| D3 | LinkedIn people found via Google search results (Serper), never via LinkedIn login | Avoids LinkedIn ToS automation risk; proven in the NXT pilot |
| D4 | Synchronous services + SQLite (stdlib) | Easier to maintain for a small team; volumes are small |
| D5 | Strict QA with blocking issues and mandatory AI review before export/send | The owner asked for rigorous checks on leads and messages |
| D6 | ChatGPT via local Streamable HTTP + Cloudflare quick tunnel + secret URL path | ChatGPT requires a public HTTPS MCP server |
| D7 | Email sending is opt-in, capped, with compliance acknowledgement per country | Protects the user's domain and reputation and keeps sending lawful |
| D8 | Open/click tracking off in v1 | Needs a public server; unreliable due to mail privacy features |
| D9 | Reuse Harvey (MIT) code by porting, with attribution | Saves time; the confirmation rules were tuned on a real 75-company run |
| D10 | Free fallback: host AI's own web search runs the queries, tool confirms (AI search mode) with verbatim-copy rules, slug–name checks and a user spot check | Owner wants a no-cost path when API credits run out; AI-copied results can be paraphrased or fabricated, so extra guards are needed |

## External facts (secondary sources, 2026-10-14, re-verify in the named task)
| Fact | Source(s) | Verify in |
|---|---|---|
| Serper: 2,500 free searches one-time, no card; 1 credit per search (≤10 results) | apiserpent.com/blog/serper-pricing-credits-explained; coldiq.com/blog/serper-pricing | T04 |
| Brave Search API: free tier replaced by ~$5 monthly credit, card required | implicator.ai (Brave drops free tier); agentdeals.dev/vendor/brave-search-api | T04 |
| Hunter.io free plan: 50 credits/month; verification costs 0.5 credit | casrai.org/guides/hunter-io-pricing; getle.ad/blog/hunter-pricing | T09 |
| Reoon: 20 daily credits (~600/month) + 100 on signup, no card | reoon.com/email-verifier | T09 |
| lemlist API "Create Lead in Campaign" exists; email may be required (unconfirmed) | developer.lemlist.com/api-reference/endpoints/leads/create-lead-in-campaign | T12 |
| lemlist "Accepted invite" condition needs Multichannel Expert or Enterprise plan | lemlist.com/multi-channel-advanced-conditions; help.lemlist.com/en/articles/8290177 | T18 |
| ChatGPT custom MCP: paid plan, Developer Mode, public HTTPS, Streamable HTTP; auth OAuth or none; write access may vary by plan | peliqan.io/blog/chatgpt-mcp; hatchable.com/articles/how-to-add-an-mcp-server-to-chatgpt; OpenAI help center (check) | T17 |
| Official MCP Python SDK is v2.x (`pip install mcp`); bundled FastMCP class reportedly renamed (MCPServer) in v2 | pypi.org/project/mcp; github.com/modelcontextprotocol/python-sdk/releases | T13 |
| Claude Desktop: local servers via config file or `.mcpb` extensions; remote via Custom Connectors; ~240 s per tool call limit | claude.com/docs/connectors/building | T13, T17, T20 |
| Google LinkedIn snippet shapes (Experience ·, at, @, "Name. Headline. Company School.") | NXT pilot run, 2026-10-14 (planner) | T08 |

## Verified by builders
| Date | Task | Fact / decision | Source |
|---|---|---|---|
| 2026-10-14 | T01 | Design: `secrets.masked()` shows only "…" for values shorter than 8 characters (card says "…" + last 4) so short secrets are never fully revealed; `paths.exports_dir()` creates the folder and slugifies its argument to prevent path traversal; `secrets.set` accepts any UPPER_CASE name (not just known keys) so later providers need no change | Builder decision |
| 2026-10-14 | T01 | pydantic 2.14.1: `ConfigDict(extra="allow")` keeps unknown keys in `model_extra` and `model_dump()` returns them, so unknown settings survive a load/save cycle; `ValidationError.errors(include_input=False, include_url=False)` omits the offending value (used so settings errors never echo values) | Ran against installed package (.venv), `pydantic.BaseModel`/`ConfigDict` |
| 2026-10-14 | T01 | httpx 0.28.1: `httpx.Timeout(20.0, connect=10.0)` sets read/write/pool=20 and connect=10; `Client(follow_redirects=..., headers=..., timeout=...)`; `Client.stream("GET", url)` is a context manager and `Response.iter_bytes()` yields decoded chunks; `Response.encoding` honours the Content-Type charset, default utf-8 | Inspected `httpx.Client.__init__`/`stream`/`Timeout` signatures and ran a `httpx.Response` in the installed package |
| 2026-10-14 | T01 | PyYAML 6.0.3: `yaml.safe_load` / `yaml.safe_dump(data, sort_keys=False, allow_unicode=True)`; broken input raises `yaml.YAMLError` | Installed package signature (`safe_dump(data, stream=None, **kwds)`) and used in tests |
| 2026-10-14 | T01 | beautifulsoup4 4.15.0: `BeautifulSoup(s, "html.parser")`, `soup(["script","style"])` + `.decompose()`, `.get_text(" ")` (stdlib parser, no lxml needed) | Ran against the installed package |
| 2026-10-14 | T01 | respx 0.23.1 (test-only): `@respx.mock` decorator uses the global `MockRouter(assert_all_called=False)` with `assert_all_mocked=True` default, so an unmocked request raises `AllMockedAssertionError("RESPX: ... not mocked!")` (no real network); `respx.get(url).mock(return_value=httpx.Response(...))` returns a `Route`; `route.calls[0].request` is the recorded `httpx.Request` (`Call` NamedTuple has `.request`) | Read `.venv/Lib/site-packages/respx/api.py`, `router.py` (`resolver`, lines ~240-250) and `models.py` (`Call`, `Route.calls`); used in tests/test_http.py |
