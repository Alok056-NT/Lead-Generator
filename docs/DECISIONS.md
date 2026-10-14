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
(Add rows: date, task, fact, source.)
