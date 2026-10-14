# T06: Campaigns and designations

Depends on: T02, T03, T05. PLAN sections: 6 (campaigns, designations, seniority), 8 step 2.

## Goal
Create and manage campaigns, and provide the designation (job-role group) and seniority logic used by the people finder, QA and exports.

## Deliverables
### `services/designations.py`
Port and generalise `classify_designation` from `reference/harvey/export.py`:
- `DesignationGroup(label, keywords: list[str], search_terms: list[str])`.
- Word-based matching exactly as Harvey's `_title_words`/`_rule_matches` (whole words, multi-word runs, trailing `*` prefix match, `L&D`/`L & D`/`L and D` → `l&d`), including:
  - false friends removed first: machine/deep/reinforcement/statistical learning, model training, quality of life, IT/network/cloud/security/cyber/data operations;
  - `vice president` → `vp` (so it never hits "president");
  - classify the **role part** of the title only (text before ` at ` or `@`).
- `classify(title, groups) -> str` (first matching group label, else "Other", "" for empty).
- `TEMPLATES: dict[str, DesignationGroup]` for: `ld` (L&D / Training), `hr` (HR leaders), `safety_ops` (Safety / Operations), `owners` (Business owners / MDs), `marketing`, `sales`, `it` (IT / Technology leaders), `finance`, `procurement`, `facilities`, `customer_service`. Each with sensible keywords and 4–7 `search_terms`. The first four must reproduce Harvey's rules (copy keywords; add `hrbp`, `employee development`, `people development`, `staff development`).
- `list_templates()`.

### `services/seniority.py`
- Levels `individual < manager < director < vp < c_suite`.
- `seniority(title) -> str` using whole-word rules (PLAN section 6), including: "Assistant Manager"/"Deputy Manager"/"Associate Manager" = manager; "Assistant Director"/"Deputy Director" = director; "AVP"/"SVP"/"EVP"/"Vice President"/"Head of"/"Head," = vp; CEO/COO/CFO/CTO/CHRO/Chief/Founder/Owner/President(not vice)/Managing Partner = c_suite; "Managing Director" = vp (rank) unless with "Group" + "CEO"; "Partner" only counts in "HR Business Partner" (manager) and "Managing Partner"/"Senior Partner"/"Equity Partner" (c_suite); "Talent Acquisition Partner", "People Partner" = individual unless also "Lead"/"Head"/"Manager"; "Junior", "Intern", "Trainee", "Associate" (alone), "Executive" (not Executive Director/Vice President), "Specialist", "Coordinator", "Officer" (not Chief … Officer) = individual.
- `meets(title, minimum) -> bool`.
- `is_top_leader(title) -> bool` (for the owners group rule, PLAN section 11.7).

### `services/campaign.py`
- `create_campaign(name, offer_id, countries: list[str], sectors: list[str], company_size: str, channels: list[str], designations: list[dict] | list[str], seniority_min="manager", exclusions=None, locale=None, budget=None, options=None) -> dict`:
  - options default `{"invite_note": false, "allow_risky_emails": false, "allow_guess_emails": false, "search_mode": "auto"}`; `search_mode` in `api`/`ai`/`auto` (PLAN 7a);
  - countries validated via `core.countries.get` (store iso2);
  - channels subset of `email`, `linkedin`;
  - `designations`: either template keys (strings) or full group dicts; 1–8 groups;
  - exclusions: `{companies: [], domains: [], people: [], competitors: []}`;
  - locale default from offer profile, then settings.
- `update_campaign(id, **changes)`, `get_campaign(id)` (with counts: companies by status, people by status, emails by status, messages by status), `list_campaigns()`, `archive_campaign(id)`.

## Worker prep (Haiku)
1. Write `src/leadgen/services/designation_templates.py`: a module docstring and one constant `TEMPLATE_DATA: dict[str, dict]` keyed `ld, hr, safety_ops, owners, marketing, sales, it, finance, procurement, facilities, customer_service`, each `{"label": str, "keywords": list[str], "search_terms": list[str]}`. For `ld`, `hr`, `safety_ops`, `owners` copy the keyword lists **exactly** from `DESIGNATION_RULES` in `reference/harvey/export.py`, then add `hrbp` to hr and `employee development`, `people development`, `staff development` to ld. For the other seven write 8-20 lower-case whole-word keywords (multi-word allowed, trailing `*` = prefix match) and 4-7 quoted-ready `search_terms` each. Data only.
2. Write `tests/fixtures/titles.py`: `TITLE_CASES: list[tuple[str, str, str]]` of at least 60 synthetic job titles as `(title, expected_group_key_or_"other", expected_seniority)`, covering every rule bullet in this card's `designations.py` and `seniority.py` sections (false friends, "vice president", role-part-before-"at", "Partner" rules, junior words, Managing Director as a rank). Use invented company names after "at"/"@". Add a short comment on each tricky case naming the rule it tests. The builder will check every expected value against the rules.

## Tests
- Classification table test with ≥40 synthetic titles covering every rule above (port the cases from the NXT pilot as *titles only*, no names: e.g. "Head of IT Operations" → Other for safety_ops; "CEO at Example Facility Services" → owners; "Senior Vice President, Human" → Other; "Training Manager@Example Utility" → ld; "Machine Learning Engineering Lead" → Other).
- Seniority table test ≥30 titles.
- Campaign create/update validation errors (bad country, bad channel, 0 or 9 groups).

## Acceptance
Tests pass; coverage ≥ 90% for designations.py and seniority.py.
