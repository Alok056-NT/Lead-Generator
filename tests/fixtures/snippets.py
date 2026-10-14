"""Synthetic Google results for the people finder's confirmation rules (T08, PLAN section 11).

Each entry is one invented search result (title, link, snippet) for an invented company, with
the outcome `confirm.parse_result` should give. tests/test_confirm.py runs every entry.

Assumed campaign for every case, unless the case has a `campaign` key:
- country: Singapore (country_code "SG", country_name "Singapore");
- designation_groups: ["ld", "hr"] (template keys in services/designation_templates.py);
- seniority_min: "manager";
- exclusions: none; suppressed_urls: none.

Overrides (a `campaign` key changes only the keys it lists; the test maps them onto
`confirm.CampaignCtx`: country_code -> countries, designation_groups -> groups, exclusions (a
list of names) -> exclusions["competitors"], suppressed_urls -> suppressed):
- "country_code" and "country_name": case 9-c runs as Canada (CA).
- "designation_groups": cases 12-a and 12-b use ["safety_ops"]; 16-a, 17-a, 18-b and 18-c use
  ["owners"].
- "exclusions": case 24-a (the company "Brightpath Learning" is an excluded competitor).
- "suppressed_urls": case 24-b (the canonical profile URL is on the suppression list).

Expectations:
- "confirmed" is True exactly when "reasons" is empty.
- "reasons" lists the PLAN section 14 codes that the engine should report, in any order. Where
  the task card gives two codes ("L14/L07", "L13/L06") both are expected. Case 12-a: "Head of IT
  Operations" is not a safety_ops title (L06) and is a weak-fit function (L13, PLAN 11.6).
  Case 18-c: a former "Managing Director" in the owners group is a former role (L08) and a
  rank-only title (L10).
- L11 (name is not a first and last name) and L12 (garbled headline) are reasons only when the
  name or headline is really unusable; none of these cases triggers them (tests/test_confirm.py
  has their own cases). Name cases stay confirmed when the greeting name is set. L20 (profile
  language/location hint) is a QA check, not a confirmation rule.
- Case 20 is a company page, so parse_result returns None: "confirmed" False, no reasons and
  "greeting_name" None.

Snippets copy Google's shapes: "Headline at Company · Experience: ...", "Experience · Company",
"Headline @ Company", "Name. Headline. Company School. City.", "Company. Jul 2011 - Present",
"Location: ..." fields, and the separators "·", "•", "�" (U+FFFD) and " - ". Names,
companies and schools are invented. Singapore, Toronto and Ontario/Canada are real places.
The shared company and campaign dicts below are read-only.
"""

NORTHWIND: dict[str, object] = {
    "name": "Northwind Health",
    "aliases": ["Northwind Health", "Northwind"],
    "domain": "northwindhealth.example.com",
}
LAKESIDE: dict[str, object] = {
    "name": "Lakeside Bank",
    "aliases": ["Lakeside Bank"],
    "domain": "lakeside.example.com",
}
KESTREL: dict[str, object] = {
    "name": "Kestrel Holdings",
    "aliases": ["Kestrel Holdings", "KH"],
    "domain": "kestrel.example.org",
}
HARBOURVIEW: dict[str, object] = {
    "name": "Harbourview Housing & Development",
    "aliases": ["Harbourview Housing & Development"],
    "domain": "harbourview.example.org",
}
ONEIL: dict[str, object] = {
    "name": "O'Neil & Partners",
    "aliases": ["O'Neil & Partners"],
    "domain": "oneil.example.net",
}
BRIGHTPATH: dict[str, object] = {
    "name": "Brightpath Learning",
    "aliases": ["Brightpath Learning"],
    "domain": "brightpath.example.net",
}

CA_CAMPAIGN: dict[str, object] = {"country_code": "CA", "country_name": "Canada"}
SAFETY_OPS: dict[str, object] = {"designation_groups": ["safety_ops"]}
OWNERS: dict[str, object] = {"designation_groups": ["owners"]}
EXCLUDE_BRIGHTPATH: dict[str, object] = {"exclusions": ["Brightpath Learning"]}
SUPPRESS_HUGO: dict[str, object] = {
    "suppressed_urls": ["https://www.linkedin.com/in/hugo-laurent-7c1e04"],
}

SNIPPET_CASES: list[dict] = [
    # 1. Headline names the company -> confirmed.
    {
        "case": "1-a",
        "company": NORTHWIND,
        "result": {
            "title": "Mira Lee - Training Manager at Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/mira-lee-3f9a2c1b",
            "snippet": "Training Manager at Northwind Health · Singapore · 500+ connections",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Mira"},
    },
    {
        "case": "1-b",
        "company": NORTHWIND,
        "result": {
            "title": (
                "Priya Raman - Learning and Development Manager at Northwind Health | LinkedIn"
            ),
            "link": "https://sg.linkedin.com/in/priya-raman-8d04e7a1",
            "snippet": (
                "Learning and Development Manager at Northwind Health - Singapore - "
                "Experience: Northwind Health"
            ),
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Priya"},
    },
    # 1 (cont.). Title "Name - Company | LinkedIn" has no role: the snippet's first segment
    # supplies the headline. Without a role in the snippet the person goes to review (L06, L07).
    {
        "case": "1-c",
        "company": NORTHWIND,
        "result": {
            "title": "Aria Tan - Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/aria-tan-4",
            "snippet": (
                "Training Manager at Northwind Health · Experience: Northwind Health · "
                "Location: Singapore"
            ),
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Aria"},
    },
    {
        "case": "1-d",
        "company": NORTHWIND,
        "result": {
            "title": "Aria Tan - Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/aria-tan-5",
            "snippet": "Experience: Northwind Health · Location: Singapore",
        },
        "expect": {"confirmed": False, "reasons": ["L06", "L07"], "greeting_name": "Aria"},
    },
    # 2. "Experience · Company" form -> confirmed.
    {
        "case": "2-a",
        "company": NORTHWIND,
        "result": {
            "title": "Jonah Reyes - HR Manager | LinkedIn",
            "link": "https://www.linkedin.com/in/jonah-reyes",
            "snippet": (
                "Experience · Northwind Health · Education · Lakeshore University · Singapore"
            ),
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Jonah"},
    },
    {
        "case": "2-b",
        "company": NORTHWIND,
        "result": {
            "title": "Lucia Ferrer - Training Manager | LinkedIn",
            "link": "https://www.linkedin.com/in/lucia-ferrer",
            "snippet": "Experience � Northwind Health � Singapore � 500+ connections",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Lucia"},
    },
    # 3. "Headline @ Company" form -> confirmed.
    {
        "case": "3-a",
        "company": NORTHWIND,
        "result": {
            "title": "Aidan Blake - HR Manager @ Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/aidan-blake",
            "snippet": "HR Manager @ Northwind Health · Singapore · Recruitment and culture",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Aidan"},
    },
    {
        "case": "3-b",
        "company": NORTHWIND,
        "result": {
            "title": "Kenji Sato - Talent Development Manager @ Northwind Health | LinkedIn",
            "link": "https://sg.linkedin.com/in/kenji-sato",
            "snippet": "Talent Development Manager @ Northwind Health · Singapore",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Kenji"},
    },
    # 4. "Name. Headline. Company School. City." (company only after the headline) -> confirmed.
    {
        "case": "4-a",
        "company": NORTHWIND,
        "result": {
            "title": "Mira Lee - Learning and Development Manager | LinkedIn",
            "link": "https://www.linkedin.com/in/mira-lee",
            "snippet": (
                "Mira Lee. Learning and Development Manager. "
                "Northwind Health Lakeshore University. Singapore."
            ),
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Mira"},
    },
    {
        "case": "4-b",
        "company": NORTHWIND,
        "result": {
            "title": "Sofia Ng - Training Manager | LinkedIn",
            "link": "https://www.linkedin.com/in/sofia-ng",
            "snippet": (
                "Sofia Ng. Training Manager. Northwind Health Ridgeview Institute. Singapore."
            ),
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Sofia"},
    },
    # 5. "Company. Jul 2011 - Present" -> confirmed.
    {
        "case": "5-a",
        "company": NORTHWIND,
        "result": {
            "title": "Dara Quek - Training Manager | LinkedIn",
            "link": "https://www.linkedin.com/in/dara-quek",
            "snippet": "Northwind Health. Jul 2011 - Present · Training Manager · Singapore",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Dara"},
    },
    {
        "case": "5-b",
        "company": NORTHWIND,
        "result": {
            "title": "Marcus Wong - Training Manager | LinkedIn",
            "link": "https://sg.linkedin.com/in/marcus-wong-a1b2c3d",
            "snippet": "Northwind Health. Mar 2018 - Present. Singapore. Training Manager",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Marcus"},
    },
    # 6. Former employer only: the company appears only as an earlier role -> L04.
    {
        "case": "6-a",
        "company": NORTHWIND,
        "result": {
            "title": "Ivy Tan - Chief People Officer | LinkedIn",
            "link": "https://www.linkedin.com/in/ivy-tan",
            "snippet": (
                "Experience · Chief People Officer. Blue Harbor Logistics · "
                "Jan 2016 - Jun 2019 · Northwind Health · Jul 2011 - Dec 2015 · Singapore"
            ),
        },
        "expect": {"confirmed": False, "reasons": ["L04"], "greeting_name": "Ivy"},
    },
    # 6 (cont.). Past-job wording before "at <company>" and an Experience field naming another
    # company are no proof of a current job -> L04 (PLAN 11.5: when unsure, review).
    {
        "case": "6-b",
        "company": NORTHWIND,
        "result": {
            "title": "Aria Tan - Training Manager | LinkedIn",
            "link": "https://www.linkedin.com/in/aria-tan",
            "snippet": (
                "Training Manager · Singapore · Previously Training Manager at Northwind Health"
            ),
        },
        "expect": {"confirmed": False, "reasons": ["L04"], "greeting_name": "Aria"},
    },
    {
        "case": "6-c",
        "company": NORTHWIND,
        "result": {
            "title": "Aria Tan - HR Manager | LinkedIn",
            "link": "https://www.linkedin.com/in/aria-tan-2",
            "snippet": "Singapore · Formerly HR lead at Northwind Health.",
        },
        "expect": {"confirmed": False, "reasons": ["L04"], "greeting_name": "Aria"},
    },
    {
        "case": "6-d",
        "company": NORTHWIND,
        "result": {
            "title": "Aria Tan - HR Manager | LinkedIn",
            "link": "https://www.linkedin.com/in/aria-tan-3",
            "snippet": (
                "Experience: Acme Corp · Singapore · "
                "I was HR Manager at Northwind Health from 2015 to 2019."
            ),
        },
        "expect": {"confirmed": False, "reasons": ["L04"], "greeting_name": "Aria"},
    },
    {
        "case": "6-e",
        "company": NORTHWIND,
        "result": {
            "title": "Aria Tan - Senior HR Manager | LinkedIn",
            "link": "https://www.linkedin.com/in/aria-tan-4",
            "snippet": "Singapore · Previously Sr. HR Manager at Northwind Health",
        },
        "expect": {"confirmed": False, "reasons": ["L04"], "greeting_name": "Aria"},
    },
    # 7. Headline names another employer while the snippet mentions the company -> L09.
    {
        "case": "7-a",
        "company": NORTHWIND,
        "result": {
            "title": "Tobias Lehmann - Chief People Officer at Blue Harbor Logistics | LinkedIn",
            "link": "https://www.linkedin.com/in/tobias-lehmann",
            "snippet": "Experience · Northwind Health · Singapore",
        },
        "expect": {"confirmed": False, "reasons": ["L09"], "greeting_name": "Tobias"},
    },
    {
        "case": "7-b",
        "company": NORTHWIND,
        "result": {
            "title": "Nina Vogel - Training Manager @ Blue Harbor Logistics | LinkedIn",
            "link": "https://www.linkedin.com/in/nina-vogel",
            "snippet": "Experience · Northwind Health · Singapore",
        },
        "expect": {"confirmed": False, "reasons": ["L09"], "greeting_name": "Nina"},
    },
    {
        "case": "7-c",
        "company": NORTHWIND,
        "result": {
            "title": "Aria Tan - Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/aria-tan-7",
            "snippet": (
                "Aria Tan. HR Manager at Acme Pte. Ltd. Northwind Health "
                "National University of Singapore. Singapore."
            ),
        },
        "expect": {"confirmed": False, "reasons": ["L09"], "greeting_name": "Aria"},
    },
    # 8. Truncated employer in the headline matches the alias prefix -> confirmed.
    {
        "case": "8-a",
        "company": NORTHWIND,
        "result": {
            "title": "Ben Adler - Training Manager at North… | LinkedIn",
            "link": "https://www.linkedin.com/in/ben-adler",
            "snippet": "Training Manager at North… · Singapore",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Ben"},
    },
    {
        "case": "8-b",
        "company": NORTHWIND,
        "result": {
            "title": "Dana Okoro - Training Manager at Northwin... | LinkedIn",
            "link": "https://sg.linkedin.com/in/dana-okoro",
            "snippet": "Training Manager at Northwin... · Singapore",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Dana"},
    },
    # 9. Country: subdomain and Location field. 9-c runs as a Canada campaign.
    {
        "case": "9-a",
        "company": NORTHWIND,
        "result": {
            "title": "Ethan Brooks - Training Manager at Northwind Health | LinkedIn",
            "link": "https://ca.linkedin.com/in/ethan-brooks-5e1d3a88",
            "snippet": (
                "Training Manager at Northwind Health · Toronto, Ontario, Canada · "
                "500+ connections"
            ),
        },
        "expect": {"confirmed": False, "reasons": ["L05"], "greeting_name": "Ethan"},
    },
    {
        "case": "9-b",
        "company": NORTHWIND,
        "result": {
            "title": "Chloe Martin - Training Manager at Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/chloe-martin",
            "snippet": (
                "Training Manager at Northwind Health · Location: Singapore · "
                "Experience · Northwind Health"
            ),
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Chloe"},
    },
    {
        "case": "9-c",
        "campaign": CA_CAMPAIGN,
        "company": NORTHWIND,
        "result": {
            "title": "Noah Fraser - HR Manager at Northwind Health | LinkedIn",
            "link": "https://ca.linkedin.com/in/noah-fraser-b72c90fe",
            "snippet": (
                "HR Manager at Northwind Health · Toronto, Ontario · "
                "Experience · Northwind Health"
            ),
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Noah"},
    },
    # 10. An explicit Location field overrides the subdomain.
    {
        "case": "10-a",
        "company": NORTHWIND,
        "result": {
            "title": "Olivia Chen - Training Manager at Northwind Health | LinkedIn",
            "link": "https://ca.linkedin.com/in/olivia-chen-47bc1e6a",
            "snippet": (
                "Training Manager at Northwind Health · Location: Singapore · 500+ connections"
            ),
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Olivia"},
    },
    {
        "case": "10-b",
        "company": NORTHWIND,
        "result": {
            "title": "Kwame Asante - Training Manager at Northwind Health | LinkedIn",
            "link": "https://sg.linkedin.com/in/kwame-asante",
            "snippet": (
                "Training Manager at Northwind Health · Location: Toronto, Ontario, Canada"
            ),
        },
        "expect": {"confirmed": False, "reasons": ["L05"], "greeting_name": "Kwame"},
    },
    # 11. Machine Learning is a false friend, so it is not L&D -> L06 (no override needed).
    {
        "case": "11-a",
        "company": NORTHWIND,
        "result": {
            "title": "Samuel Ortiz - Machine Learning Lead | LinkedIn",
            "link": "https://www.linkedin.com/in/samuel-ortiz",
            "snippet": "Machine Learning Lead at Northwind Health · Singapore",
        },
        "expect": {"confirmed": False, "reasons": ["L06"], "greeting_name": "Samuel"},
    },
    # 12. Weak-fit function for a safety/operations group (safety_ops campaign).
    {
        "case": "12-a",
        "campaign": SAFETY_OPS,
        "company": NORTHWIND,
        "result": {
            "title": "Kofi Mensah - Head of IT Operations at Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/kofi-mensah-0c6f4b2d",
            "snippet": "Head of IT Operations at Northwind Health · Singapore",
        },
        "expect": {"confirmed": False, "reasons": ["L06", "L13"], "greeting_name": "Kofi"},
    },
    {
        "case": "12-b",
        "campaign": SAFETY_OPS,
        "company": NORTHWIND,
        "result": {
            "title": "Tara Vance - Head of Facilities Operations at Northwind Health | LinkedIn",
            "link": "https://sg.linkedin.com/in/tara-vance",
            "snippet": "Head of Facilities Operations at Northwind Health · Singapore",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Tara"},
    },
    # 13. "Executive" is individual rank and a junior keyword -> L07 and L14.
    {
        "case": "13-a",
        "company": NORTHWIND,
        "result": {
            "title": "Rafael Quintero - Senior Executive, Training at Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/rafael-quintero-e91a7d03",
            "snippet": "Senior Executive, Training at Northwind Health · Singapore",
        },
        "expect": {
            "confirmed": False,
            "reasons": ["L07", "L14"],
            "greeting_name": "Rafael",
        },
    },
    # 14. "Partner" alone is individual rank -> L07.
    {
        "case": "14-a",
        "company": NORTHWIND,
        "result": {
            "title": "Cara Lim - Talent Acquisition Partner at Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/cara-lim",
            "snippet": "Talent Acquisition Partner at Northwind Health · Singapore",
        },
        "expect": {"confirmed": False, "reasons": ["L07"], "greeting_name": "Cara"},
    },
    # 15. "HR Business Partner" counts as manager -> confirmed.
    {
        "case": "15-a",
        "company": NORTHWIND,
        "result": {
            "title": "Nadia Rahman - HR Business Partner at Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/nadia-rahman-9a3f0d52",
            "snippet": "HR Business Partner at Northwind Health · Singapore",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Nadia"},
    },
    # 16. Owners group, rank-only "Managing Director" (not a head-of-company term) -> L10.
    {
        "case": "16-a",
        "campaign": OWNERS,
        "company": LAKESIDE,
        "result": {
            "title": (
                "Owen Harte - Managing Director, Group Head of Retail Banking at Lakeside Bank "
                "| LinkedIn"
            ),
            "link": "https://www.linkedin.com/in/owen-harte",
            "snippet": (
                "Managing Director, Group Head of Retail Banking at Lakeside Bank · Singapore"
            ),
        },
        "expect": {"confirmed": False, "reasons": ["L10"], "greeting_name": "Owen"},
    },
    # 17. Owners group, "Deputy CEO" is a head-of-company term -> confirmed.
    {
        "case": "17-a",
        "campaign": OWNERS,
        "company": NORTHWIND,
        "result": {
            "title": "Grace Lau - Deputy CEO at Northwind Health | LinkedIn",
            "link": "https://sg.linkedin.com/in/grace-lau-1d8e6c7b",
            "snippet": "Deputy CEO at Northwind Health · Singapore",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Grace"},
    },
    # 18. Former role -> L08.
    {
        "case": "18-a",
        "company": NORTHWIND,
        "result": {
            "title": "Hana Mori - Former Training Manager at Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/hana-mori",
            "snippet": "Former Training Manager at Northwind Health · Singapore",
        },
        "expect": {"confirmed": False, "reasons": ["L08"], "greeting_name": "Hana"},
    },
    {
        "case": "18-b",
        "campaign": OWNERS,
        "company": NORTHWIND,
        "result": {
            "title": "Victor Ng - Former Founder and CEO at Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/victor-ng-c05b92e4",
            "snippet": "Former Founder and CEO at Northwind Health · Singapore",
        },
        "expect": {"confirmed": False, "reasons": ["L08"], "greeting_name": "Victor"},
    },
    {
        "case": "18-c",
        "campaign": OWNERS,
        "company": NORTHWIND,
        "result": {
            "title": "Simon Lok - Former Managing Director at Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/simon-lok",
            "snippet": "Former Managing Director at Northwind Health · Singapore",
        },
        "expect": {
            "confirmed": False,
            "reasons": ["L08", "L10"],
            "greeting_name": "Simon",
        },
    },
    # 19. Job-seeker hints are a warning that still leaves the person in review -> L15.
    {
        "case": "19-a",
        "company": NORTHWIND,
        "result": {
            "title": "Rhea Nair - Training Manager | LinkedIn",
            "link": "https://www.linkedin.com/in/rhea-nair-2b7e9c40",
            "snippet": (
                "Looking for my next role in training · Singapore · Experience · Northwind Health"
            ),
        },
        "expect": {"confirmed": False, "reasons": ["L15"], "greeting_name": "Rhea"},
    },
    {
        "case": "19-b",
        "company": NORTHWIND,
        "result": {
            "title": "Arjun Patel - Training Manager at Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/arjun-patel",
            "snippet": "Open to work · Training Manager at Northwind Health · Singapore",
        },
        "expect": {"confirmed": False, "reasons": ["L15"], "greeting_name": "Arjun"},
    },
    # 20. Company pages are not personal profiles -> None.
    {
        "case": "20-a",
        "company": NORTHWIND,
        "result": {
            "title": "Northwind Health: Overview | LinkedIn",
            "link": "https://www.linkedin.com/company/northwind-health/",
            "snippet": "Northwind Health | Singapore · 500+ employees",
        },
        "expect": {"confirmed": False, "reasons": [], "greeting_name": None},
    },
    {
        "case": "20-b",
        "company": NORTHWIND,
        "result": {
            "title": "Northwind Health | LinkedIn",
            "link": "https://sg.linkedin.com/company/northwind-health-pte",
            "snippet": "Health services company · Singapore",
        },
        "expect": {"confirmed": False, "reasons": [], "greeting_name": None},
    },
    # 21. Short all-caps alias "KH": whole word and case-sensitive only.
    {
        "case": "21-a",
        "company": KESTREL,
        "result": {
            "title": "Ruth Ong - Training Manager | LinkedIn",
            "link": "https://www.linkedin.com/in/ruth-ong-6f2a8d1e",
            "snippet": "Training Manager · Singapore · Experience · Khoury Hardware",
        },
        "expect": {"confirmed": False, "reasons": ["L04"], "greeting_name": "Ruth"},
    },
    {
        "case": "21-b",
        "company": KESTREL,
        "result": {
            "title": "Lena Ho - Training Manager at KH | LinkedIn",
            "link": "https://sg.linkedin.com/in/lena-ho",
            "snippet": "Training Manager at KH · Singapore",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Lena"},
    },
    # 22. Name quality: credentials, brackets, initials, ALL CAPS and East Asian order.
    {
        "case": "22-a",
        "company": NORTHWIND,
        "result": {
            "title": "Jerome Tan, MBA - Training Manager at Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/jerome-tan-a41e07d",
            "snippet": "Training Manager at Northwind Health · Singapore",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Jerome"},
    },
    {
        "case": "22-b",
        "company": NORTHWIND,
        "result": {
            "title": "Sarah Lopez (IHRP-CP) - HR Manager at Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/sarah-lopez",
            "snippet": "HR Manager at Northwind Health · Singapore · IHRP-CP",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Sarah"},
    },
    {
        "case": "22-c",
        "company": NORTHWIND,
        "result": {
            "title": "Lim Jun Hao (Max) - Training Manager at Northwind Health | LinkedIn",
            "link": "https://sg.linkedin.com/in/lim-jun-hao",
            "snippet": "Training Manager at Northwind Health · Singapore",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Max"},
    },
    {
        "case": "22-d",
        "company": NORTHWIND,
        "result": {
            "title": "R KUMARASAMY - Training Manager at Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/r-kumarasamy-3b1f9d2",
            "snippet": "Training Manager at Northwind Health · Singapore",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Kumarasamy"},
    },
    {
        "case": "22-e",
        "company": NORTHWIND,
        "result": {
            "title": "ANITA PRASAD - HR Manager at Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/anita-prasad",
            "snippet": "HR Manager at Northwind Health · Singapore",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Anita"},
    },
    {
        "case": "22-f",
        "company": NORTHWIND,
        "result": {
            "title": "Mei Xin Tan - Training Manager at Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/mei-xin-tan-0a9d4e7",
            "snippet": "Training Manager at Northwind Health · Singapore",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Mei Xin"},
    },
    {
        "case": "22-g",
        "company": NORTHWIND,
        "result": {
            "title": "Lim Wei Jie - HR Manager at Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/lim-wei-jie",
            "snippet": "HR Manager at Northwind Health · Singapore",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Wei Jie"},
    },
    {
        "case": "22-h",
        "company": NORTHWIND,
        "result": {
            "title": "Chen Rachel - Training Manager at Northwind Health | LinkedIn",
            "link": "https://sg.linkedin.com/in/chen-rachel",
            "snippet": "Training Manager at Northwind Health · Singapore",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Rachel"},
    },
    {
        "case": "22-i",
        "company": NORTHWIND,
        "result": {
            "title": "WEI LING TAN - Training Manager at Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/wei-ling-tan-8e3c5a1",
            "snippet": "Training Manager at Northwind Health · Singapore",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Wei Ling"},
    },
    {
        "case": "22-j",
        "company": NORTHWIND,
        "result": {
            "title": "Aditi Rao, PhD - HR Manager at Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/aditi-rao-d4f2b6",
            "snippet": "HR Manager at Northwind Health · Singapore",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Aditi"},
    },
    # 23. HTML entities are decoded before matching.
    {
        "case": "23-a",
        "company": HARBOURVIEW,
        "result": {
            "title": (
                "Eli Mensah - Training Manager at Harbourview Housing &amp; Development | LinkedIn"
            ),
            "link": "https://sg.linkedin.com/in/eli-mensah",
            "snippet": "Training Manager at Harbourview Housing &amp; Development · Singapore",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Eli"},
    },
    {
        "case": "23-b",
        "company": ONEIL,
        "result": {
            "title": "Maya O&#39;Neil - HR Manager at O&#39;Neil &amp; Partners | LinkedIn",
            "link": "https://www.linkedin.com/in/maya-oneil-5d0a3c7",
            "snippet": "Experience · O&#39;Neil &amp; Partners · Singapore",
        },
        "expect": {"confirmed": True, "reasons": [], "greeting_name": "Maya"},
    },
    # 24. Excluded competitor (campaign exclusions) and suppressed profile URL.
    {
        "case": "24-a",
        "campaign": EXCLUDE_BRIGHTPATH,
        "company": BRIGHTPATH,
        "result": {
            "title": "Ana Lima - Training Manager at Brightpath Learning | LinkedIn",
            "link": "https://www.linkedin.com/in/ana-lima",
            "snippet": "Training Manager at Brightpath Learning · Singapore",
        },
        "expect": {"confirmed": False, "reasons": ["L03"], "greeting_name": "Ana"},
    },
    {
        "case": "24-b",
        "campaign": SUPPRESS_HUGO,
        "company": NORTHWIND,
        "result": {
            "title": "Hugo Laurent - Training Manager at Northwind Health | LinkedIn",
            "link": "https://www.linkedin.com/in/hugo-laurent-7c1e04",
            "snippet": "Training Manager at Northwind Health · Singapore",
        },
        "expect": {"confirmed": False, "reasons": ["L16"], "greeting_name": "Hugo"},
    },
]
