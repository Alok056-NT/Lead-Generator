# T09: Email finder and verification

Depends on: T04, T08. PLAN sections: 7, 12, tools 21–22.
Port from: `reference/harvey/integrations/email_finder.py` (MIT).

## Goal
For confirmed people in email-channel campaigns, find one best-guess work email and verify it honestly: `verified`, `risky` (catch-all), `guess` (unverified), `invalid`, `unknown`.

## Verify first (DECISIONS.md)
- Hunter: domain-search endpoint + `pattern` field format, email-finder endpoint, email-verifier endpoint, auth (`api_key` query param), credit costs, free allowance, error codes for quota.
- Reoon: single verification endpoint, modes (quick/power), response statuses, free allowance.
- ZeroBounce: validate endpoint, statuses, free allowance.

## Deliverables
### Providers: `providers/hunter.py`, `providers/reoon.py`, `providers/zerobounce.py`
Implement `Provider` (T04) + methods: Hunter `domain_pattern(domain) -> (pattern, confidence) | None`, `find(first, last, domain) -> (email, score) | None`, `verify(email) -> Verdict`; Reoon `verify(email) -> Verdict`; ZeroBounce `verify(email) -> Verdict`. `Verdict(status: verified|risky|invalid|unknown, raw_status: str, provider: str)`. Record usage + budgets (`budgets.hunter`, `budgets.verify`).

### `services/emails.py` (port + adapt Harvey's pipeline; synchronous)
- `mx_type(domain) -> "google"|"microsoft"|"gateway"|"other"|None` (dnspython; cache per process).
- `derive_pattern(domain) -> (pattern, source)`: DB cache (`settings` key `pattern:<domain>`) → Hunter domain pattern → known addresses found on the company website (scrape `/contact`, `/about` for `mailto:` on the same domain; infer pattern) → default `{first}.{last}`.
- `build_email(pattern, first, last, domain)`: transliterate accents (NFKD), drop non-letters, handle multi-part last names (use the last token for `{last}` and also try the joined form only when the pattern came from a real example). Patterns: `{first}.{last}`, `{first}{last}`, `{f}{last}`, `{first}`, `{first}_{last}`, `{last}.{first}`, `{f}.{last}`, `{first}{l}`.
- `verify(email, mx)`: Reoon → ZeroBounce → Hunter verifier → SMTP RCPT probe **only** for `other` MX (never for google/microsoft) → `guess`. Catch-all detection (probe a random address on the domain via the same verifier) → `risky`.
- Role-address guard: never store `info@`, `hr@`, `careers@`, `admin@`, `contact@`, `hello@`, `sales@` as a person's email.
- `estimate_email_search(campaign_id) -> {"people", "domains", "hunter_credits", "verify_credits", "budget_left"}`.
- `find_emails(campaign_id, person_ids=None, confirm=False, cursor=None, max_people_per_call=25) -> dict`: chunked like T08; stores into `emails` table; returns counts by status.
- Suppression check before storing.

## Tests
- Pattern building table (accents, hyphens, apostrophes, multi-part surnames, single-letter first names).
- Pattern inference from a known address.
- Verifier fallback order with respx; catch-all → risky; SMTP never used for google/microsoft MX (assert no socket attempts: monkeypatch).
- Role-address guard.
- Budget/confirm behaviour and chunking.

## Acceptance
Tests pass. Manual: 3 real people with free keys; statuses look honest.
