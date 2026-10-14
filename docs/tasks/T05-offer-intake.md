# T05: Offer intake (URL, text, file) and OfferProfile

Depends on: T02. PLAN sections: 6 (OfferProfile), 8 step 1.

## Goal
Turn whatever the user has (website URL, pasted text, or a PDF/DOCX/TXT/MD file) into clean text the AI can read, then store the structured `OfferProfile` the AI writes back.

## Deliverables
Add dependencies `pypdf` and `python-docx` to pyproject.

### `services/offer.py`
- `ingest_url(url, max_pages=15) -> offer_id`:
  - Port Harvey's `FallbackCrawler` from `reference/harvey/trainer.py` (same-domain crawl starting at the URL, prioritised paths like /about, /services, /solutions, /industries, /case-studies, /faq, /contact; respect robots.txt via `urllib.robotparser`; max 15 pages; 1 req/s).
  - Extract readable text per page (drop nav/footer/script/style; keep headings as `## `); store `raw_text` as `# <page url>\n<text>` blocks.
  - If the URL returns 4xx/5xx or no text (page "not active"), return a structured error suggesting pasted text or a file.
- `ingest_text(text, name=None) -> offer_id`.
- `ingest_file(path) -> offer_id`: `.pdf` (pypdf), `.docx` (python-docx, include tables), `.txt`, `.md`. Reject other types with a clear message. Max 20 MB.
- `read_offer_text(offer_id, cursor=0, max_chars=40_000) -> {"text", "next_cursor" | None, "total_chars"}`: chunking for MCP size limits.
- `save_profile(offer_id, profile: dict) -> OfferProfile`: validate with the pydantic model; normalise whitespace; dedupe lists; reject if `confirmed_claims` and `forbidden_claims` overlap.
- `get_offer(offer_id)`, `list_offers()`.
- Detect "to confirm" markers in source text: lines containing `[CONFIRM`, `[PROVIDE`, `TBC`, `TODO`, `to be confirmed`, or highlighted-as-placeholder patterns, and return them in `ingest_*` results as `unconfirmed_snippets` so the AI puts them in `forbidden_claims` (this is how the NXT doc worked).
- `models.py` (or inside offer.py): `OfferProfile` exactly as PLAN section 6.

### Google Docs
Public Google Doc links (`docs.google.com/document/d/<id>/...`): convert to the export URL `https://docs.google.com/document/d/<id>/export?format=txt` and ingest as text. If it isn't public, return an error telling the user to download it as .docx or paste the text.

### Guide text
`src/leadgen/guides/offer_profile.md`: instructions for the AI on writing the profile: only claims stated in the source; anything marked to-confirm goes to forbidden; numbers only with a source; ask the user to confirm the two claim lists before saving.

## Tests
- Crawler with respx: follows same-domain links only, respects robots.txt disallow, stops at max_pages, handles a 404 start URL with the structured error.
- Text extraction drops nav/script.
- PDF/DOCX ingestion with small generated fixtures (create them in the test with pypdf/python-docx, don't commit binaries).
- Google Docs URL rewriting; private doc (HTTP 401/403 or HTML login page) → helpful error.
- `unconfirmed_snippets` detection.
- Profile validation: missing required fields, overlap rejection, round-trip.
- Chunking: cursor walks the full text with no loss.

## Acceptance
Tests pass. Manual: ingest a real public website and a local DOCX.
