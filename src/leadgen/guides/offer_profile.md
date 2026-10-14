# How to write the offer profile

You have just read the text of the user's offer (a website, pasted text or a document). Turn it
into an `OfferProfile` and save it with `save_offer_profile`. Every message written later is
checked against this profile, so accuracy matters more than style.

## Rules

1. **Only claims stated in the source.** Do not add facts from your own knowledge, from the
   web, or from what similar companies usually say. If the source does not say it, it is not a claim.
2. **Anything marked "to confirm" is forbidden, not confirmed.** The ingest result lists
   `unconfirmed_snippets`: lines containing `[CONFIRM`, `[PROVIDE`, `TBC`, `TBD`, `TODO`,
   "to be confirmed", `XX` placeholders, or `[HIGHLIGHT: ...]` (text the author highlighted as
   a placeholder). Put each such claim in `forbidden_claims`. Never put it in `confirmed_claims`
   or `proof_points`, even if it sounds reasonable.
3. **Numbers only with a source.** A figure, percentage, year, headcount, price or "X clients"
   goes in `proof_points` as `{claim, source}` where `source` says where in the source it came
   from (a page URL or section name). If you cannot name the source, it goes in `forbidden_claims`.
4. **Client and brand names** are forbidden unless the source clearly presents them as the
   offer's clients or partners. List the allowed ones in `confirmed_claims`.
5. **Keep claims short and close to the source's own words** (one fact per entry). The checks
   compare messages against these lines.
6. **Ask, do not guess.** If the sender (name, company, role), tone or language is not in the
   source, ask the user. Do not invent a person.
7. **A claim must not appear in both lists.** The tool rejects a profile where
   `confirmed_claims` and `forbidden_claims` overlap.

## Fields

```
name, one_liner (one sentence), description (2-5 sentences)
services:        [{name, what, best_for: [..]}]
audiences:       who buys (roles and company types)
confirmed_claims facts the messages MAY state
forbidden_claims things the messages must NOT state
proof_points:    [{claim, source}]
ctas:            e.g. "15-minute call", "short example"
sender:          {name, company, role, email?, website?}
tone:            e.g. "professional, plain, no hype"
language:        e.g. "en-GB" (spelling used in messages)
```

All of `name`, `one_liner`, `description`, `sender` (name, company, role), `tone` and
`language` are required. Lists may be empty but should not be, except `proof_points` when the
source has none.

## Before saving: confirm with the user

Show the user the two lists in plain words and wait for their answer before calling
`save_offer_profile`:

- "I will let the messages say: ..." (`confirmed_claims`, and the `proof_points`)
- "I will NOT let the messages say: ..." (`forbidden_claims`, with the reason, e.g. "marked
  to confirm in your document")

Ask: "Is anything in the first list wrong, or anything in the second list actually confirmed?"
Apply their corrections, then save. If the tool replies `invalid_profile`, fix the fields it
names and save again.
