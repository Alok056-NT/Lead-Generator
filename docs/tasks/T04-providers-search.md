# T04: Provider framework and search providers

Depends on: T01, T02, T03. PLAN sections: 7.

## Goal
A uniform way to call third-party APIs with usage tracking, budgets, caching and test fakes; plus the search providers (Serper default, Brave optional, DuckDuckGo last-resort).

## Verify first (record in DECISIONS.md with URL + date)
- Serper: endpoint (`POST https://google.serper.dev/search`), auth header (`X-API-KEY`), body fields (`q`, `gl`, `num`, `page`), response shape (`organic[].title/link/snippet/position`), credit cost per call vs `num`, and the free allowance. Use the official docs at serper.dev.
- Brave Search API: endpoint, auth header, params (`q`, `country`, `count`), response shape, current free/credit terms.
- The `gl` codes used in `core/countries.py` (e.g. GB → `uk` or `gb`?). Fix T03 data if needed.

## Deliverables
### `providers/base.py`
```python
class ProviderError(Exception): code: str  # "not_configured" | "auth" | "quota" | "rate_limited" | "http" | "parse"
@dataclass class ProviderInfo: key: str; name: str; purpose: str; env_keys: list[str]; free_tier: str; signup_url: str; paid: bool; default: bool
class Provider(Protocol):
    info: ProviderInfo
    def configured(self) -> bool: ...
    def test(self) -> tuple[bool, str]: ...      # cheapest possible call; never spends >1 credit
```
- `record_usage(provider_key, calls=1, credits=1)` writes `api_usage` (day = UTC date).
- `usage(provider_key, period="month") -> dict(calls, credits)`.
- Simple rate limiter: `RateLimiter(per_second)` thread-safe token bucket.
- Retry helper: 2 retries with backoff on 429/5xx; never retry 401/403.

### `providers/search.py` (interface + result type)
```python
@dataclass class SearchResult: title: str; link: str; snippet: str; position: int
class SearchProvider(Provider):
    def search(self, query: str, country_gl: str = "", num: int = 10) -> list[SearchResult]: ...
```
- `cached_search(provider, query, country_gl, num, ttl_days=30)`: key = sha256 of `provider|normalized query|gl|num`; reads/writes `search_cache`; cache hits do **not** record usage.

### `providers/serper.py`, `providers/brave.py`, `providers/ddg.py`
- Serper: as verified. Map errors to `ProviderError` codes.
- Brave: as verified.
- DuckDuckGo HTML (`https://html.duckduckgo.com/html/`): parse results with BeautifulSoup; unwrap redirect links (`//duckduckgo.com/l/?uddg=<urlencoded>`); disabled unless `settings.allow_ddg_fallback` is true (add the setting, default false). Mark results as low-confidence.
- Port nothing from Harvey's Bing scraper (unreliable; see DECISIONS).

### `providers/fake.py`
`FakeSearchProvider(responses: dict[str, list[SearchResult]] | Callable)` for tests and the e2e run; records calls; supports simulating errors.

### `providers/registry.py`
- `ALL_PROVIDERS`: info for serper, brave, ddg, hunter, reoon, zerobounce, lemlist, smtp (later tasks implement the last five; register their `ProviderInfo` now so `list_providers` is complete).
- `get_search_provider() -> SearchProvider`: first configured of [serper, brave], else ddg if allowed, else raise `ProviderError("not_configured")`.
- `needs_key_response(provider_key, why) -> dict` (shape in PLAN section 7): `{"ok": false, "needs_key": {provider, name, why, free_tier, signup_url, how_to_add: ["Dashboard → Keys (recommended)", "Tell the AI to call save_api_key"]}}`.
- `save_api_key(provider_key, key) -> dict`: store via `core.secrets.set`, run `test()`; if test fails, delete the key again and return the error; return masked key on success.

### `services/status.py` (partial)
`list_providers() -> list[dict]`: info + configured + masked key + usage this month + remaining free allowance where computable.

### CLI
`leadgen keys list`, `leadgen keys set <provider>` (prompts with `getpass`, never echo), `leadgen keys test <provider>`.

## Tests (respx for HTTP)
- Serper request shape (headers, body), parse, error mapping (401 → auth, 429 → rate_limited with retry, 500 retry then http).
- Cache: second identical query hits cache, no HTTP call, no usage recorded; expired entry refetched.
- Registry fallback order; `needs_key_response` shape; `save_api_key` deletes key on failed test.
- DDG parser on a saved synthetic HTML fixture; redirect unwrapping.
- Usage aggregation by month.

## Acceptance
Tests pass. `leadgen keys set serper` + `leadgen keys test serper` works against the real API (manual, 1 credit).

## Out of scope
Email providers (T09), lemlist (T12), SMTP (T14).
