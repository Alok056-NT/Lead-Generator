# T03: Countries and compliance tables

Depends on: T01. PLAN sections: 11 (country check), 16.

## Goal
Two data modules other code relies on: a country table (for searching and confirming location) and a cold-email compliance table (for the sending gate). The compliance table must be **researched with sources**, not written from memory.

## Deliverables
### `src/leadgen/core/countries.py`
A dataclass `Country(name, iso2, gl, linkedin_subdomains: tuple[str,...], cities: tuple[str,...], aliases: tuple[str,...], locale: str, timezone: str)` (`timezone` = IANA zone of the main business city, used for quiet hours in T14; validate with `zoneinfo.ZoneInfo`; add `tzdata` as a Windows dependency marker `tzdata; sys_platform == "win32"`) and a dict `COUNTRIES` keyed by iso2 covering at least:
SG, MY, ID, PH, TH, VN, IN, AE, SA, QA, GB, IE, US, CA, AU, NZ, DE, FR, NL, BE, ES, IT, SE, DK, NO, FI, CH, AT, PL, PT, BR, MX, ZA, NG, KE, JP, KR, HK, TW, CN.
- `gl`: Google country code (`gl` parameter, lower-case iso2 except `uk` for GB, verify against Serper docs in T04 and note it).
- `linkedin_subdomains`: the country subdomain LinkedIn uses in public profile URLs (e.g. `sg`, `uk`, `in`, `au`, `ca`, `de`, `fr`, `nl`, `ae`, `my`; the US uses `www`). Where unsure, leave empty and rely on city/country names.
- `cities`: 5–15 largest business cities (used to recognise "Location: Bengaluru" etc.).
- `aliases`: e.g. GB: "United Kingdom", "UK", "England", "Scotland", "Wales"; AE: "UAE", "Dubai"; US: "United States", "USA".
- Functions: `get(code_or_name) -> Country` (case-insensitive, accepts iso2, name, alias), `matches_location(country, url, text) -> bool`, `all_countries()`.
- `matches_location` rules, in order:
  1. If the text has an explicit location field (`Location: <value>` up to the next `·`, `|`, `.` or end) **and** that value names any country/alias/city in the table, decide **only** from that value (True if it names this country, else False). This stops "Experience: Singapore Airlines" from counting as Singapore when the person is in Mumbai. If the value names nothing recognisable (e.g. a postcode), ignore the field and continue.
  2. Else, if the URL subdomain is in the country's `linkedin_subdomains` (not `www`) → True.
  3. Else, country name/alias/city as whole words anywhere in the text → True.
  4. Else False.

### `src/leadgen/core/compliance.py`
`EmailRule(country_iso2, regime, consent_model, required_elements: tuple[str,...], subject_label: str | None, default_policy, summary, sources: tuple[str,...], checked_on: str)` where:
- `consent_model`: `opt_out` | `opt_in` | `opt_in_with_exceptions`
- `default_policy`: `allow_with_optout` | `warn` | `block_without_consent`
- `required_elements`: subset of `sender_identity`, `postal_address`, `unsubscribe`, `truthful_subject`, `subject_label`
Cover every country in COUNTRIES; group the EU/EEA under a shared base rule plus country overrides where national law is stricter (Germany is the known strict case for B2B).
Research and cite, at minimum: US (CAN-SPAM), Canada (CASL), UK (PECR: corporate vs sole-trader subscribers), EU GDPR + ePrivacy with DE override, Australia (Spam Act 2003, inferred consent), New Zealand, Singapore (Spam Control Act, including whether the `<ADV>` subject label applies to unsolicited commercial email), India, UAE, Malaysia, Japan.
- `rule_for(iso2) -> EmailRule` (unknown → conservative `warn` rule).
- `send_gate(countries) -> list[EmailRule]` used by T14.
- Every rule's `summary` is two to four plain-English sentences a non-lawyer can follow, ending with "This is not legal advice."

### `docs/DECISIONS.md`
Add a "Compliance sources" table: country, regime, source URL (official legislation or regulator page preferred), date checked, key point.

## Worker prep (Haiku)
1. Write `src/leadgen/core/countries_data.py`: a module docstring and one constant `COUNTRY_ROWS: list[dict]`, one dict per country listed under "Deliverables" (all 40), each with keys `name`, `iso2`, `gl`, `linkedin_subdomains` (list), `cities` (list, 5-15 largest business cities), `aliases` (list), `locale` (e.g. "en-SG", "de-DE"), `timezone` (IANA zone of the main business city). Use correct ISO 3166-1 codes. `gl` = lower-case iso2 except "uk" for GB (the builder will verify against Serper). Only list a LinkedIn subdomain you are confident appears in public profile URLs for that country (the US uses "www"); otherwise use an empty list. Check every timezone loads with `zoneinfo` (install `tzdata` into `.venv` if Windows needs it). Data only: no functions, no tests.

## Tests
- `get("Singapore")`, `get("sg")`, `get("uk")`/`get("United Kingdom")` resolve; unknown raises a clear error.
- `matches_location`: `https://sg.linkedin.com/in/x` + no text → True for SG; `www` URL + "Location: Singapore" → True; `https://sg.linkedin.com/in/x` + "Experience: Singapore Airlines · Location: Mumbai" → False for SG, True for IN (rule 1 wins over the subdomain); city match "Bengaluru" → IN; "Location: 228211" (postcode only) with an `sg` subdomain → True via rule 2 only if rule 1 found no country/city (treat unrecognised location values as "no location field").
- Every COUNTRIES entry has a compliance rule; every rule has ≥1 source and a `checked_on` date; summaries end with the disclaimer.

## Acceptance
Tests pass; DECISIONS.md has the sources table; the reviewer can spot-check 5 rules against their sources.

## Out of scope
Using the gate (T14).
