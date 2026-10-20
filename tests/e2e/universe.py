"""Synthetic world for the T19 end-to-end tests.

Everything here is invented: companies, people, LinkedIn slugs and addresses. Domains are
example.com or example.org only. Real places (Singapore and UK cities) appear only as
locations. Data only, no logic.

- OFFER_TEXT: the offer of a fictional VR training studio (about 400 words). The two
  [CONFIRM ...] lines must be reported as unconfirmed.
- COMPANIES: 12 companies, 7 in Singapore (SG) and 5 in the UK (GB). `country_code` is
  the company's country; the flows use one campaign for both countries.
- SEARCH_RESULTS: key "<company name>|<designation group label>" (labels from
  services/designation_templates.py); 5 results per key, 120 in all. The comment after
  each result is the outcome of the confirmation engine (`confirm`, or `review` with the
  PLAN 14 codes). A result with "fabricated": True has a link slug that belongs to
  another name. Google never returns those, so the API flow skips them, and the
  AI-search flow must block them.
- HUNTER_RESPONSES and REOON_RESPONSES: replies in the shapes recorded in docs/DECISIONS.md
  (T09). Hunter: `domain_search` by domain (one example address when the domain has a
  pattern), `email_finder` by "<domain>|<first> <last>" (a missing key is HTTP 404),
  `email_verifier` by address, `account` (free). HTTP status codes 401, 403, 429 and 202
  are status codes, not bodies. Reoon: `verify` by address (power mode), `balance`, and
  `errors` (the JSON body the code reads as quota or auth). An address that is not a key
  has no answer in this world.
"""

from typing import Any

OFFER_TEXT = """Lanternfish VR is a virtual reality training studio. We build short immersive
courses for frontline teams, safety crews and sales floors, so that people can
practise a hard moment in a headset before they meet it for real.

What we make

Our studio builds short VR scenarios of 10 to 20 minutes that run on standard
headsets. A learner might calm a customer who is upset about a late delivery,
walk a warehouse floor to spot hazards, or hold a difficult performance review.
Each scenario ends with a short debrief and a score that the training team can
read on a simple dashboard.

Who it helps

Learning and development teams use us when classroom courses do not stick. HR
leaders use us for onboarding, manager training and conduct topics. Operations
and safety leads use us for drills that would be too costly or risky to run on
site.

How we work

1. We interview your subject experts for one hour and collect your current
   policies and incident notes.

2. We write a storyboard within two weeks and share it for comments.

3. We build a prototype scenario within six weeks and test it with five of your
   learners.

4. We refine the scenario and hand it over with a short guide for your trainers.

What you get

- Scenarios tailored to your processes, not generic demos

- A dashboard with completion, scores and retry counts

- Content updates for two years after launch

- Training for your trainers, in person or online

Proof points

We have delivered projects for logistics, healthcare and retail teams. Clients
report fewer escalated complaints after customer handling courses, and shorter
time to competence for new warehouse staff.

[CONFIRM: which client names we may publish and the exact complaint reduction figure]

Pricing

A single scenario starts from a fixed project fee, with a monthly licence for
hosting and the dashboard. Volume discounts apply to five or more scenarios a
year.

[CONFIRM: current list prices in SGD and GBP and the minimum project size]

Accessibility and privacy

Scenarios include captions, a seated mode and a clear exit at any time. We store
only the scores that the client's training team needs, and we never record
learners' voices or images without written consent.

Next step

Reply with the team you have in mind and the skill you want to improve. We will
send back a short plan within three working days, with a rough timeline and a
price range. There is no obligation to continue.

Thank you for reading. We look forward to talking about your learners and what
they need to practise.
"""

COMPANIES: list[dict[str, Any]] = [
    {
        "name": "Aster Harbour Logistics",
        "aliases": [
            "Aster Harbour Logistics",
            "Aster Harbour",
        ],
        "domain": "asterharbour.example.com",
        "sector": "Logistics",
        "country_code": "SG",
    },
    {
        "name": "Bayline Insurance",
        "aliases": [
            "Bayline Insurance",
            "Bayline",
        ],
        "domain": "bayline.example.com",
        "sector": "Insurance",
        "country_code": "SG",
    },
    {
        "name": "Crescent Marine Services",
        "aliases": [
            "Crescent Marine Services",
            "Crescent Marine",
        ],
        "domain": "crescentmarine.example.com",
        "sector": "Marine services",
        "country_code": "SG",
    },
    {
        "name": "Driftwood Foods",
        "aliases": [
            "Driftwood Foods",
            "Driftwood",
        ],
        "domain": "driftwoodfoods.example.com",
        "sector": "Food manufacturing",
        "country_code": "SG",
    },
    {
        "name": "Evergreen Clinics",
        "aliases": [
            "Evergreen Clinics",
            "Evergreen",
        ],
        "domain": "evergreenclinics.example.com",
        "sector": "Healthcare",
        "country_code": "SG",
    },
    {
        "name": "Fernhill Retail Group",
        "aliases": [
            "Fernhill Retail Group",
            "Fernhill Retail",
        ],
        "domain": "fernhillretail.example.com",
        "sector": "Retail",
        "country_code": "SG",
    },
    {
        "name": "Quillon Telecom",
        "aliases": [
            "Quillon Telecom",
            "Quillon",
        ],
        "domain": "quillon.example.org",
        "sector": "Telecommunications",
        "country_code": "SG",
    },
    {
        "name": "Thornbury Cold Storage",
        "aliases": [
            "Thornbury Cold Storage",
            "Thornbury",
        ],
        "domain": "thornbury.example.org",
        "sector": "Cold-chain logistics",
        "country_code": "GB",
    },
    {
        "name": "Westbrook Dental Group",
        "aliases": [
            "Westbrook Dental Group",
            "Westbrook Dental",
        ],
        "domain": "westbrookdental.example.org",
        "sector": "Dental care",
        "country_code": "GB",
    },
    {
        "name": "Marlow Pines Care Homes",
        "aliases": [
            "Marlow Pines Care Homes",
            "Marlow Pines",
        ],
        "domain": "marlowpines.example.org",
        "sector": "Residential care",
        "country_code": "GB",
    },
    {
        "name": "Sedgemoor Textiles",
        "aliases": [
            "Sedgemoor Textiles",
            "Sedgemoor",
        ],
        "domain": "sedgemoor.example.org",
        "sector": "Textiles",
        "country_code": "GB",
    },
    {
        "name": "Tidewell Energy Supply",
        "aliases": [
            "Tidewell Energy Supply",
            "Tidewell Energy",
            "Tidewell",
        ],
        "domain": "tidewell.example.org",
        "sector": "Energy supply",
        "country_code": "GB",
    },
]

SEARCH_RESULTS: dict[str, list[dict]] = {
    "Aster Harbour Logistics|L&D / Training heads": [
        {
            "title": "Mira Tan - Training Manager at Aster Harbour Logistics | LinkedIn",
            "link": "https://www.linkedin.com/in/mira-tan",
            "snippet": "Training Manager at Aster Harbour Logistics · Singapore · 500+ connections",
        },  # confirm
        {
            "title": (
                "Dev Iyer - Learning and Development Manager at Aster Harbour Logistics | LinkedIn"
            ),
            "link": "https://sg.linkedin.com/in/dev-iyer",
            "snippet": (
                "Learning and Development Manager at Aster Harbour Logistics "
                "- Singapore - Experience: Aster Harbour Logistics"
            ),
        },  # confirm
        {
            "title": "Lena Ong - Training Manager | LinkedIn",
            "link": "https://www.linkedin.com/in/lena-ong",
            "snippet": (
                "Experience · Aster Harbour Logistics · Education · Redhill Institute · Singapore"
            ),
        },  # confirm
        {
            "title": "Ravi Menon - Training Specialist at Aster Harbour Logistics | LinkedIn",
            "link": "https://www.linkedin.com/in/ravi-menon",
            "snippet": (
                "Training Specialist at Aster Harbour Logistics · Singapore · Corporate learning"
            ),
        },  # review L07 L14
        {
            "title": "Joy Lim - Former Training Manager at Aster Harbour Logistics | LinkedIn",
            "link": "https://www.linkedin.com/in/joy-lim",
            "snippet": "Former Training Manager at Aster Harbour Logistics · Singapore",
        },  # review L08
    ],
    "Aster Harbour Logistics|HR leaders": [
        {
            "title": "Aidan Blake - HR Manager @ Aster Harbour Logistics | LinkedIn",
            "link": "https://www.linkedin.com/in/aidan-blake",
            "snippet": "HR Manager @ Aster Harbour Logistics · Singapore · Recruitment and culture",
        },  # confirm
        {
            "title": "Sofia Ng - HR Business Partner at Aster Harbour Logistics | LinkedIn",
            "link": "https://sg.linkedin.com/in/sofia-ng",
            "snippet": "HR Business Partner at Aster Harbour Logistics · Singapore",
        },  # confirm
        {
            "title": "Tomas Berg - Chief People Officer at Blue Harbor Logistics | LinkedIn",
            "link": "https://www.linkedin.com/in/tomas-berg",
            "snippet": "Experience · Aster Harbour Logistics · Singapore",
        },  # review L09
        {
            "title": "Ana Ruiz - HR Manager | LinkedIn",
            "link": "https://www.linkedin.com/in/ana-ruiz",
            "snippet": "Previously HR Manager at Aster Harbour Logistics · Singapore",
        },  # review L04 L08
        {
            "title": "Owen Park - Head of Human Resources at Aster Harbour Logistics | LinkedIn",
            "link": "https://www.linkedin.com/in/owen-park",
            "snippet": (
                "Head of Human Resources at Aster Harbour Logistics · Singapore · 500+ connections"
            ),
        },  # confirm
    ],
    "Bayline Insurance|L&D / Training heads": [
        {
            "title": (
                "Priya Raman - Learning and Development Manager at Bayline Insurance | LinkedIn"
            ),
            "link": "https://sg.linkedin.com/in/priya-raman",
            "snippet": (
                "Learning and Development Manager at Bayline Insurance - "
                "Singapore - Experience: Bayline Insurance"
            ),
        },  # confirm
        {
            "title": "Kenji Sato - Talent Development Manager @ Bayline Insurance | LinkedIn",
            "link": "https://sg.linkedin.com/in/kenji-sato",
            "snippet": "Talent Development Manager @ Bayline Insurance · Singapore",
        },  # confirm
        {
            "title": "Grace Lau - Training Manager at Bayline | LinkedIn",
            "link": "https://www.linkedin.com/in/grace-lau",
            "snippet": "Training Manager at Bayline · Singapore · 500+ connections",
        },  # confirm
        {
            "title": "Nadia Rahman - Trainer at Bayline Insurance | LinkedIn",
            "link": "https://www.linkedin.com/in/nadia-rahman",
            "snippet": "Trainer at Bayline Insurance · Singapore",
        },  # review L07
        {
            "title": "Kwame Asante - Training Manager at Bayline Insurance | LinkedIn",
            "link": "https://www.linkedin.com/in/kwame-asante",
            "snippet": "Training Manager at Bayline Insurance · Location: Toronto, Ontario, Canada",
        },  # review L05
    ],
    "Bayline Insurance|HR leaders": [
        {
            "title": "Eli Mensah - HR Manager at Bayline Insurance | LinkedIn",
            "link": "https://www.linkedin.com/in/eli-mensah",
            "snippet": "HR Manager at Bayline Insurance · Singapore · Recruitment and culture",
        },  # confirm
        {
            "title": "Maya Chen - Human Resources Director at Bayline Insurance | LinkedIn",
            "link": "https://www.linkedin.com/in/maya-chen",
            "snippet": (
                "Human Resources Director at Bayline Insurance · Singapore · "
                "Experience · Bayline Insurance"
            ),
        },  # confirm
        {
            "title": "Victor Ng - Former HR Director at Bayline Insurance | LinkedIn",
            "link": "https://www.linkedin.com/in/victor-ng",
            "snippet": "Former HR Director at Bayline Insurance · Singapore",
        },  # review L08
        {
            "title": "Samuel Ortiz - Machine Learning Lead at Bayline Insurance | LinkedIn",
            "link": "https://www.linkedin.com/in/samuel-ortiz",
            "snippet": "Machine Learning Lead at Bayline Insurance · Singapore",
        },  # review L06
        {
            "title": "Cara Lim - Talent Acquisition Partner at Bayline Insurance | LinkedIn",
            "link": "https://www.linkedin.com/in/cara-lim",
            "snippet": "Talent Acquisition Partner at Bayline Insurance · Singapore",
        },  # review L07
    ],
    "Crescent Marine Services|L&D / Training heads": [
        {
            "title": "Hana Mori - Learning Manager at Crescent Marine Services | LinkedIn",
            "link": "https://www.linkedin.com/in/hana-mori",
            "snippet": (
                "Learning Manager at Crescent Marine Services · Singapore · 500+ connections"
            ),
        },  # confirm
        {
            "title": "Felix Brandt - Training Manager at Crescent Marine | LinkedIn",
            "link": "https://sg.linkedin.com/in/alan-yap",
            "snippet": (
                "Training Manager at Crescent Marine · Singapore · Experience "
                "· Crescent Marine Services"
            ),
            "fabricated": True,
        },  # fabricated: link slug belongs to another name
        {
            "title": "Ruth Ong - Training Manager | LinkedIn",
            "link": "https://www.linkedin.com/in/ruth-ong",
            "snippet": "Experience · Pelham Hardware · Singapore · Training Manager",
        },  # review L04
        {
            "title": "Ben Adler - Training Manager at Crescent… | LinkedIn",
            "link": "https://www.linkedin.com/in/ben-adler",
            "snippet": "Training Manager at Crescent… · Singapore",
        },  # confirm
        {
            "title": "Alan Teo - Head of Learning at Crescent Marine Services | LinkedIn",
            "link": "https://sg.linkedin.com/in/alan-teo",
            "snippet": (
                "Head of Learning at Crescent Marine Services · Singapore · "
                "Learning and development"
            ),
        },  # confirm
    ],
    "Crescent Marine Services|HR leaders": [
        {
            "title": "Dara Quek - HR Manager | LinkedIn",
            "link": "https://www.linkedin.com/in/dara-quek",
            "snippet": "Crescent Marine Services. Jul 2011 - Present · HR Manager · Singapore",
        },  # confirm
        {
            "title": "Marcus Wong - HR Business Partner at Crescent Marine Services | LinkedIn",
            "link": "https://sg.linkedin.com/in/marcus-wong",
            "snippet": "HR Business Partner at Crescent Marine Services · Singapore · Recruitment",
        },  # confirm
        {
            "title": "Noor Hassan - HR Manager at Crescent Marine Services | LinkedIn",
            "link": "https://www.linkedin.com/in/noor-hassan",
            "snippet": "HR Manager at Crescent Marine Services · Singapore",
        },  # confirm
        {
            "title": "Greta Holm - HR Manager at Crescent Marine Services | LinkedIn",
            "link": "https://www.linkedin.com/in/greta-holm",
            "snippet": "HR Manager at Crescent Marine Services · Location: Hamburg, Germany",
        },  # review L05
        {
            "title": "Simon Lok - Director of Operations at Crescent Marine Services | LinkedIn",
            "link": "https://www.linkedin.com/in/simon-lok",
            "snippet": "Director of Operations at Crescent Marine Services · Singapore",
        },  # review L06
    ],
    "Driftwood Foods|L&D / Training heads": [
        {
            "title": "Ines Duarte - Learning and Development Manager at Driftwood Foods | LinkedIn",
            "link": "https://sg.linkedin.com/in/ines-duarte",
            "snippet": (
                "Learning and Development Manager at Driftwood Foods - "
                "Singapore - Experience: Driftwood Foods"
            ),
        },  # confirm
        {
            "title": "Tara Vance - Training Manager at Driftwood Foods | LinkedIn",
            "link": "https://www.linkedin.com/in/tara-vance",
            "snippet": "Training Manager at Driftwood Foods · Singapore · 500+ connections",
        },  # confirm
        {
            "title": "Olu Bakare - Training Coordinator at Driftwood Foods | LinkedIn",
            "link": "https://www.linkedin.com/in/olu-bakare",
            "snippet": "Training Coordinator at Driftwood Foods · Singapore · Food safety courses",
        },  # review L07 L14
        {
            "title": "Chloe Martin - Training Manager at Driftwood Foods | LinkedIn",
            "link": "https://www.linkedin.com/in/chloe-martin",
            "snippet": (
                "Training Manager at Driftwood Foods · Location: Singapore · "
                "Experience · Driftwood Foods"
            ),
        },  # confirm
        {
            "title": "Hugo Laurent - Training Manager at Driftwood Foods | LinkedIn",
            "link": "https://www.linkedin.com/in/hugo-laurent",
            "snippet": "Training Manager at Driftwood Foods · Singapore · Open to work",
        },  # review L15
    ],
    "Driftwood Foods|HR leaders": [
        {
            "title": "Ellie Marsh - HR Manager at Driftwood Foods | LinkedIn",
            "link": "https://www.linkedin.com/in/ellie-marsh",
            "snippet": "HR Manager at Driftwood Foods · Singapore · Recruitment and culture",
        },  # confirm
        {
            "title": "Rohan Kapoor - HR Director at Driftwood Foods | LinkedIn",
            "link": "https://sg.linkedin.com/in/rohan-kapoor",
            "snippet": "HR Director at Driftwood Foods · Singapore · Experience · Driftwood Foods",
        },  # confirm
        {
            "title": "Iris Teo - People and Culture Manager at Driftwood Foods | LinkedIn",
            "link": "https://www.linkedin.com/in/iris-teo",
            "snippet": "People and Culture Manager at Driftwood Foods · Singapore",
        },  # confirm
        {
            "title": "Jun Park - Former HR Manager at Driftwood Foods | LinkedIn",
            "link": "https://www.linkedin.com/in/jun-park",
            "snippet": "Former HR Manager at Driftwood Foods · Singapore",
        },  # review L08
        {
            "title": "Leon Tay - Chief Executive Officer at Driftwood Foods | LinkedIn",
            "link": "https://www.linkedin.com/in/leon-tay",
            "snippet": "Chief Executive Officer at Driftwood Foods · Singapore",
        },  # review L06
    ],
    "Evergreen Clinics|L&D / Training heads": [
        {
            "title": "Amara Osei - Training Manager at Evergreen Clinics | LinkedIn",
            "link": "https://www.linkedin.com/in/amara-osei",
            "snippet": "Training Manager at Evergreen Clinics · Singapore · 500+ connections",
        },  # confirm
        {
            "title": "Ivan Petrov - Clinical Training Lead at Evergreen Clinics | LinkedIn",
            "link": "https://sg.linkedin.com/in/ivan-petrov",
            "snippet": "Clinical Training Lead at Evergreen Clinics · Singapore",
        },  # confirm
        {
            "title": "Zoe Tan - Learning and Development Manager at Evergreen | LinkedIn",
            "link": "https://www.linkedin.com/in/zoe-tan",
            "snippet": "Learning and Development Manager at Evergreen · Singapore",
        },  # confirm
        {
            "title": "Ming Li - Training Manager at Evergreen Clinics | LinkedIn",
            "link": "https://www.linkedin.com/in/ming-li",
            "snippet": (
                "Training Manager · Singapore · Previously Training Manager at Evergreen Clinics"
            ),
        },  # confirm
        {
            "title": "Sanjay Mehta - Training Officer at Evergreen Clinics | LinkedIn",
            "link": "https://www.linkedin.com/in/sanjay-mehta",
            "snippet": "Training Officer at Evergreen Clinics · Singapore",
        },  # review L07
    ],
    "Evergreen Clinics|HR leaders": [
        {
            "title": "Lucia Ferrer - HR Manager at Evergreen Clinics | LinkedIn",
            "link": "https://www.linkedin.com/in/lucia-ferrer",
            "snippet": "Experience � Evergreen Clinics � Singapore � 500+ connections",
        },  # confirm
        {
            "title": "Sarah Lopez - HR Manager at Evergreen Clinics | LinkedIn",
            "link": "https://www.linkedin.com/in/sarah-lopez",
            "snippet": "HR Manager at Evergreen Clinics · Singapore · IHRP-CP",
        },  # confirm
        {
            "title": "Peter Yeo - Talent Acquisition Partner at Evergreen Clinics | LinkedIn",
            "link": "https://www.linkedin.com/in/peter-yeo",
            "snippet": "Talent Acquisition Partner at Evergreen Clinics · Singapore",
        },  # review L07
        {
            "title": "Rosa Vidal - HR Business Partner at Evergreen Clinics | LinkedIn",
            "link": "https://sg.linkedin.com/in/rosa-vidal",
            "snippet": "HR Business Partner at Evergreen Clinics · Singapore",
        },  # confirm
        {
            "title": "Dana Okoro - HR Manager at Bayline Insurance | LinkedIn",
            "link": "https://www.linkedin.com/in/dana-okoro",
            "snippet": "Experience · Evergreen Clinics · Singapore",
        },  # review L09
    ],
    "Fernhill Retail Group|L&D / Training heads": [
        {
            "title": "Mina Kato - Training Manager at Fernhill Retail Group | LinkedIn",
            "link": "https://www.linkedin.com/in/mina-kato",
            "snippet": "Training Manager at Fernhill Retail Group · Singapore · 500+ connections",
        },  # confirm
        {
            "title": "Oscar Reid - Learning and Development Manager at Fernhill Retail | LinkedIn",
            "link": "https://sg.linkedin.com/in/oscar-reid",
            "snippet": "Learning and Development Manager at Fernhill Retail · Singapore",
        },  # confirm
        {
            "title": "Hannah Brooks - Training Manager | LinkedIn",
            "link": "https://www.linkedin.com/in/hannah-brooks",
            "snippet": (
                "Experience · Fernhill Retail Group · Education · Redhill Institute · Singapore"
            ),
        },  # confirm
        {
            "title": "Yusuf Ali - Training Manager at Fernhill Retail Group | LinkedIn",
            "link": "https://www.linkedin.com/in/yusuf-ali",
            "snippet": (
                "Training Manager at Fernhill Retail Group · Location: Kuala Lumpur, Malaysia"
            ),
        },  # review L05
        {
            "title": "Eva Lindqvist - Training Manager at Fernhill Retail Group | LinkedIn",
            "link": "https://www.linkedin.com/in/eva-lindqvist",
            "snippet": (
                "Training Manager at Fernhill Retail Group · Singapore · "
                "Looking for my next role in training"
            ),
        },  # review L15
    ],
    "Fernhill Retail Group|HR leaders": [
        {
            "title": "Gabriel Soto - HR Manager at Fernhill Retail Group | LinkedIn",
            "link": "https://www.linkedin.com/in/gabriel-soto",
            "snippet": "HR Manager at Fernhill Retail Group · Singapore · Recruitment and culture",
        },  # confirm
        {
            "title": "Ava Lim - HR Director at Fernhill Retail Group | LinkedIn",
            "link": "https://www.linkedin.com/in/ava-lim",
            "snippet": "HR Director at Fernhill Retail Group · Singapore",
        },  # confirm
        {
            "title": "Nikhil Rao - HR Manager at Fernhill Retail Group | LinkedIn",
            "link": "https://sg.linkedin.com/in/nikhil-rao",
            "snippet": (
                "HR Manager at Fernhill Retail Group · Singapore · Experience "
                "· Fernhill Retail Group"
            ),
        },  # confirm
        {
            "title": "Paul Gerber - HR Manager at Fernhill Retail Group | LinkedIn",
            "link": "https://www.linkedin.com/in/paul-gerber",
            "snippet": (
                "Paul Gerber. HR Manager. Fernhill Retail Group Redhill Institute. Singapore."
            ),
        },  # confirm
        {
            "title": "Lily Wan - HR Assistant at Fernhill Retail Group | LinkedIn",
            "link": "https://www.linkedin.com/in/lily-wan",
            "snippet": "HR Assistant at Fernhill Retail Group · Singapore",
        },  # review L07
    ],
    "Quillon Telecom|L&D / Training heads": [
        {
            "title": "Ethan Koh - Training Manager at Quillon Telecom | LinkedIn",
            "link": "https://www.linkedin.com/in/ethan-koh",
            "snippet": "Training Manager at Quillon Telecom · Singapore · 500+ connections",
        },  # confirm
        {
            "title": "Gita Nair - Training Manager | LinkedIn",
            "link": "https://www.linkedin.com/in/gita-nair",
            "snippet": (
                "Training Manager · Singapore · Previously Training Manager at Quillon Telecom"
            ),
        },  # review L04
        {
            "title": "Felix Wong - Training Lead at Quillon Telecom | LinkedIn",
            "link": "https://sg.linkedin.com/in/felix-wong",
            "snippet": "Training Lead at Quillon Telecom · Singapore · Corporate learning",
        },  # confirm
        {
            "title": "Bella Sim - Talent Development Manager at Quillon Telecom | LinkedIn",
            "link": "https://www.linkedin.com/in/bella-sim",
            "snippet": "Talent Development Manager at Quillon Telecom · Singapore",
        },  # confirm
        {
            "title": "Ryo Kadota - Training Manager at Quillon Telecom | LinkedIn",
            "link": "https://www.linkedin.com/in/ryo-kadota",
            "snippet": "Training Manager at Quillon Telecom · Singapore · Location: Tokyo, Japan",
        },  # review L05
    ],
    "Quillon Telecom|HR leaders": [
        {
            "title": "Helen Tay - HR Manager at Quillon Telecom | LinkedIn",
            "link": "https://www.linkedin.com/in/helen-tay",
            "snippet": "HR Manager at Quillon Telecom · Singapore · Recruitment and culture",
        },  # confirm
        {
            "title": "Mark Dewi - Human Resources Manager at Quillon Telecom | LinkedIn",
            "link": "https://www.linkedin.com/in/mark-dewi",
            "snippet": "Human Resources Manager at Quillon Telecom · Singapore",
        },  # confirm
        {
            "title": "Aisha Noor - Chief HR Officer at Quillon Telecom | LinkedIn",
            "link": "https://www.linkedin.com/in/aisha-noor",
            "snippet": "Chief HR Officer at Quillon Telecom · Singapore",
        },  # confirm
        {
            "title": "Nathan Yeo - HR Manager at Quillon Telecom | LinkedIn",
            "link": "https://www.linkedin.com/in/rachel-ong",
            "snippet": "HR Manager at Quillon Telecom · Singapore · Recruitment and culture",
            "fabricated": True,
        },  # fabricated: link slug belongs to another name
        {
            "title": "Trevor Gill - IT Operations Manager at Quillon Telecom | LinkedIn",
            "link": "https://www.linkedin.com/in/trevor-gill",
            "snippet": "IT Operations Manager at Quillon Telecom · Singapore",
        },  # review L06
    ],
    "Thornbury Cold Storage|L&D / Training heads": [
        {
            "title": "Emily Hart - Training Manager at Thornbury Cold Storage | LinkedIn",
            "link": "https://uk.linkedin.com/in/emily-hart",
            "snippet": (
                "Training Manager at Thornbury Cold Storage · Manchester, England, United Kingdom"
            ),
        },  # confirm
        {
            "title": "Callum Reed - Learning and Development Manager at Thornbury | LinkedIn",
            "link": "https://uk.linkedin.com/in/callum-reed",
            "snippet": "Learning and Development Manager at Thornbury · Manchester, United Kingdom",
        },  # confirm
        {
            "title": "Ruby Shaw - Training Manager at Thornbury Cold Storage | LinkedIn",
            "link": "https://www.linkedin.com/in/ruby-shaw",
            "snippet": (
                "Training Manager at Thornbury Cold Storage · Location: "
                "Leeds, England, United Kingdom"
            ),
        },  # confirm
        {
            "title": "Jasper Lacey - Training Coordinator at Thornbury Cold Storage | LinkedIn",
            "link": "https://uk.linkedin.com/in/jasper-lacey",
            "snippet": (
                "Training Coordinator at Thornbury Cold Storage · Manchester, United Kingdom"
            ),
        },  # review L07 L14
        {
            "title": "Isla Morgan - Training Manager at Thornbury Cold Storage | LinkedIn",
            "link": "https://uk.linkedin.com/in/isla-morgan",
            "snippet": "Training Manager at Thornbury Cold Storage · Location: Dublin, Ireland",
        },  # review L05
    ],
    "Thornbury Cold Storage|HR leaders": [
        {
            "title": "George Pryce - HR Manager at Thornbury Cold Storage | LinkedIn",
            "link": "https://uk.linkedin.com/in/george-pryce",
            "snippet": "HR Manager at Thornbury Cold Storage · Manchester, United Kingdom",
        },  # confirm
        {
            "title": "Lauren Hughes - HR Director at Thornbury Cold Storage | LinkedIn",
            "link": "https://www.linkedin.com/in/lauren-hughes",
            "snippet": "HR Director at Thornbury Cold Storage · Leeds, England, United Kingdom",
        },  # confirm
        {
            "title": "Ahmed Farah - HR Business Partner at Thornbury Cold Storage | LinkedIn",
            "link": "https://uk.linkedin.com/in/ahmed-farah",
            "snippet": "HR Business Partner at Thornbury Cold Storage · Manchester, United Kingdom",
        },  # confirm
        {
            "title": "Olivia Price - HR Manager at Thornbury Cold Storage | LinkedIn",
            "link": "https://www.linkedin.com/in/olivia-price",
            "snippet": "HR Manager at Thornbury Cold Storage · Glasgow, Scotland, United Kingdom",
        },  # confirm
        {
            "title": "Megan Ford - HR Manager at Thornbury Cold Storage | LinkedIn",
            "link": "https://uk.linkedin.com/in/tara-quinn",
            "snippet": "HR Manager at Thornbury Cold Storage · Manchester, United Kingdom",
            "fabricated": True,
        },  # fabricated: link slug belongs to another name
    ],
    "Westbrook Dental Group|L&D / Training heads": [
        {
            "title": "Freya Clarke - Training Manager at Westbrook Dental Group | LinkedIn",
            "link": "https://uk.linkedin.com/in/freya-clarke",
            "snippet": (
                "Training Manager at Westbrook Dental Group · Bristol, England, United Kingdom"
            ),
        },  # confirm
        {
            "title": "Tom Ashby - Clinical Training Manager at Westbrook Dental | LinkedIn",
            "link": "https://uk.linkedin.com/in/tom-ashby",
            "snippet": "Clinical Training Manager at Westbrook Dental · London, United Kingdom",
        },  # confirm
        {
            "title": (
                "Jess Okafor - Learning and Development Manager at Westbrook "
                "Dental Group | LinkedIn"
            ),
            "link": "https://www.linkedin.com/in/jess-okafor",
            "snippet": "Experience · Westbrook Dental Group · London, United Kingdom · Education",
        },  # confirm
        {
            "title": "Ollie Grant - Training Manager at Westbrook Dental Group | LinkedIn",
            "link": "https://uk.linkedin.com/in/ollie-grant",
            "snippet": "Training Manager at Westbrook Dental Group · Location: Paris, France",
        },  # review L05
        {
            "title": "Leah Simms - Training Manager at Westbrook Dental Group | LinkedIn",
            "link": "https://uk.linkedin.com/in/leah-simms",
            "snippet": (
                "Training Manager · Bristol, England, United Kingdom · "
                "Previously Training Manager at Westbrook Dental Group"
            ),
        },  # confirm
    ],
    "Westbrook Dental Group|HR leaders": [
        {
            "title": "Rhys Powell - HR Manager at Westbrook Dental Group | LinkedIn",
            "link": "https://uk.linkedin.com/in/rhys-powell",
            "snippet": "HR Manager at Westbrook Dental Group · London, United Kingdom",
        },  # confirm
        {
            "title": "Sienna Cole - HR Manager at Westbrook Dental Group | LinkedIn",
            "link": "https://www.linkedin.com/in/sienna-cole",
            "snippet": (
                "HR Manager at Westbrook Dental Group · Bristol, England, "
                "United Kingdom · Recruitment and culture"
            ),
        },  # confirm
        {
            "title": "Dylan Fox - Head of People at Westbrook Dental Group | LinkedIn",
            "link": "https://uk.linkedin.com/in/dylan-fox",
            "snippet": "Head of People at Westbrook Dental Group · London, United Kingdom",
        },  # confirm
        {
            "title": "Amelia Rhodes - HR Officer at Westbrook Dental Group | LinkedIn",
            "link": "https://www.linkedin.com/in/amelia-rhodes",
            "snippet": "HR Officer at Westbrook Dental Group · Bristol, England, United Kingdom",
        },  # review L07
        {
            "title": "Cole Barker - Business Partner at Westbrook Dental Group | LinkedIn",
            "link": "https://uk.linkedin.com/in/cole-barker",
            "snippet": "Business Partner at Westbrook Dental Group · London, United Kingdom",
        },  # review L06 L07
    ],
    "Marlow Pines Care Homes|L&D / Training heads": [
        {
            "title": "Poppy Hale - Training Manager at Marlow Pines Care Homes | LinkedIn",
            "link": "https://uk.linkedin.com/in/poppy-hale",
            "snippet": (
                "Training Manager at Marlow Pines Care Homes · Leeds, England, United Kingdom"
            ),
        },  # confirm
        {
            "title": "Benji Cross - Learning and Development Manager at Marlow Pines | LinkedIn",
            "link": "https://uk.linkedin.com/in/benji-cross",
            "snippet": "Learning and Development Manager at Marlow Pines · Leeds, United Kingdom",
        },  # confirm
        {
            "title": "Nora Bell - Training Manager | LinkedIn",
            "link": "https://www.linkedin.com/in/nora-bell",
            "snippet": (
                "Experience · Marlow Pines Care Homes · Education · Redhill "
                "Institute · United Kingdom"
            ),
        },  # confirm
        {
            "title": "Cedric Moss - Training Manager at Marlow Pines Care Homes | LinkedIn",
            "link": "https://uk.linkedin.com/in/cedric-moss",
            "snippet": "Training Manager at Marlow Pines Care Homes · Location: Accra, Ghana",
        },  # confirm
        {
            "title": "Joan Pike - Former Training Manager at Marlow Pines Care Homes | LinkedIn",
            "link": "https://www.linkedin.com/in/joan-pike",
            "snippet": "Former Training Manager at Marlow Pines Care Homes · Leeds, United Kingdom",
        },  # review L08
    ],
    "Marlow Pines Care Homes|HR leaders": [
        {
            "title": "Dominic Shaw - HR Manager at Marlow Pines Care Homes | LinkedIn",
            "link": "https://uk.linkedin.com/in/dominic-shaw",
            "snippet": "HR Manager at Marlow Pines Care Homes · Leeds, England, United Kingdom",
        },  # confirm
        {
            "title": "Freya Marsh - HR Director at Marlow Pines Care Homes | LinkedIn",
            "link": "https://www.linkedin.com/in/freya-marsh",
            "snippet": (
                "HR Director at Marlow Pines Care Homes · United Kingdom · "
                "Experience · Marlow Pines Care Homes"
            ),
        },  # confirm
        {
            "title": "Kieran Doyle - HR Manager at Marlow Pines Care Homes | LinkedIn",
            "link": "https://uk.linkedin.com/in/kieran-doyle",
            "snippet": (
                "HR Manager at Marlow Pines Care Homes · Leeds, England, "
                "United Kingdom · Looking for my next role"
            ),
        },  # review L15
        {
            "title": "Tessa Lowe - Recruitment Manager at Marlow Pines Care Homes | LinkedIn",
            "link": "https://uk.linkedin.com/in/tessa-lowe",
            "snippet": "Recruitment Manager at Marlow Pines Care Homes · Leeds, United Kingdom",
        },  # confirm
        {
            "title": "Ryan Beck - Facilities Manager at Marlow Pines Care Homes | LinkedIn",
            "link": "https://uk.linkedin.com/in/ryan-beck",
            "snippet": "Facilities Manager at Marlow Pines Care Homes · Leeds, United Kingdom",
        },  # review L06
    ],
    "Sedgemoor Textiles|L&D / Training heads": [
        {
            "title": "Holly Brennan - Training Manager at Sedgemoor Textiles | LinkedIn",
            "link": "https://uk.linkedin.com/in/holly-brennan",
            "snippet": "Training Manager at Sedgemoor Textiles · Bristol, England, United Kingdom",
        },  # confirm
        {
            "title": "Max Ferrier - Trainer at Sedgemoor Textiles | LinkedIn",
            "link": "https://uk.linkedin.com/in/max-ferrier",
            "snippet": "Trainer at Sedgemoor Textiles · Bristol, United Kingdom",
        },  # review L07
        {
            "title": "Ivy Crowther - Learning and Development Manager at Sedgemoor | LinkedIn",
            "link": "https://www.linkedin.com/in/ivy-crowther",
            "snippet": (
                "Learning and Development Manager at Sedgemoor · Bristol, England, United Kingdom"
            ),
        },  # confirm
        {
            "title": "Gus Harper - Training Manager at Sedgemoor Textiles | LinkedIn",
            "link": "https://uk.linkedin.com/in/gus-harper",
            "snippet": "Training Manager at Sedgemoor Textiles · Location: Mumbai, India",
        },  # review L05
        {
            "title": "Ella Burrows - Training Manager at Sedgemoor Textiles | LinkedIn",
            "link": "https://uk.linkedin.com/in/ella-burrows",
            "snippet": (
                "Training Manager at Sedgemoor Textiles · Bristol, England, "
                "United Kingdom · Previously Training Manager at Sedgemoor "
                "Textiles"
            ),
        },  # confirm
    ],
    "Sedgemoor Textiles|HR leaders": [
        {
            "title": "Neil Crofts - HR Manager at Sedgemoor Textiles | LinkedIn",
            "link": "https://uk.linkedin.com/in/neil-crofts",
            "snippet": "HR Manager at Sedgemoor Textiles · Bristol, England, United Kingdom",
        },  # confirm
        {
            "title": "Leanne Hobbs - HR Business Partner at Sedgemoor Textiles | LinkedIn",
            "link": "https://www.linkedin.com/in/leanne-hobbs",
            "snippet": (
                "HR Business Partner at Sedgemoor Textiles · Bristol, United Kingdom · Recruitment"
            ),
        },  # confirm
        {
            "title": "Joel Parr - Chief People Officer at Sedgemoor Textiles | LinkedIn",
            "link": "https://uk.linkedin.com/in/joel-parr",
            "snippet": (
                "Chief People Officer at Sedgemoor Textiles · Bristol, England, United Kingdom"
            ),
        },  # confirm
        {
            "title": "Sanna Lund - HR Manager at Sedgemoor Textiles | LinkedIn",
            "link": "https://www.linkedin.com/in/sanna-lund",
            "snippet": (
                "Experience · Sedgemoor Textiles · Bristol, England, United Kingdom · HR Manager"
            ),
        },  # confirm
        {
            "title": "Oscar Tan - HR Manager at Thornbury Cold Storage | LinkedIn",
            "link": "https://uk.linkedin.com/in/oscar-tan",
            "snippet": "Experience · Sedgemoor Textiles · Bristol, England, United Kingdom",
        },  # review L09
    ],
    "Tidewell Energy Supply|L&D / Training heads": [
        {
            "title": "Ruby Alder - Training Manager at Tidewell Energy Supply | LinkedIn",
            "link": "https://uk.linkedin.com/in/ruby-alder",
            "snippet": "Training Manager at Tidewell Energy Supply · London, United Kingdom",
        },  # confirm
        {
            "title": "Finn Murray - Learning and Development Manager at Tidewell | LinkedIn",
            "link": "https://uk.linkedin.com/in/finn-murray",
            "snippet": (
                "Learning and Development Manager at Tidewell · London, England, United Kingdom"
            ),
        },  # confirm
        {
            "title": "Chloe Bright - Head of Learning at Tidewell Energy Supply | LinkedIn",
            "link": "https://www.linkedin.com/in/chloe-bright",
            "snippet": "Head of Learning at Tidewell Energy Supply · Manchester, United Kingdom",
        },  # confirm
        {
            "title": "Adam Hewitt - Training Manager at Tidewell Energy Supply | LinkedIn",
            "link": "https://uk.linkedin.com/in/adam-hewitt",
            "snippet": "Training Manager at Tidewell Energy Supply · Location: Berlin, Germany",
        },  # review L05
        {
            "title": "Jade Coker - Training Specialist at Tidewell Energy Supply | LinkedIn",
            "link": "https://uk.linkedin.com/in/jade-coker",
            "snippet": "Training Specialist at Tidewell Energy Supply · London, United Kingdom",
        },  # review L07 L14
    ],
    "Tidewell Energy Supply|HR leaders": [
        {
            "title": "Robin Walsh - HR Manager at Tidewell Energy Supply | LinkedIn",
            "link": "https://uk.linkedin.com/in/robin-walsh",
            "snippet": "HR Manager at Tidewell Energy Supply · London, England, United Kingdom",
        },  # confirm
        {
            "title": "Mia Chapman - HR Director at Tidewell Energy Supply | LinkedIn",
            "link": "https://www.linkedin.com/in/mia-chapman",
            "snippet": (
                "HR Director at Tidewell Energy Supply · London, United "
                "Kingdom · Recruitment and culture"
            ),
        },  # confirm
        {
            "title": "Gareth Owen - Former HR Manager at Tidewell Energy Supply | LinkedIn",
            "link": "https://uk.linkedin.com/in/gareth-owen",
            "snippet": "Former HR Manager at Tidewell Energy Supply · Manchester, United Kingdom",
        },  # review L08
        {
            "title": "Sophie Naylor - HR Advisor at Tidewell Energy Supply | LinkedIn",
            "link": "https://uk.linkedin.com/in/sophie-naylor",
            "snippet": "HR Advisor at Tidewell Energy Supply · London, United Kingdom",
        },  # review L07
        {
            "title": "Jamie Ross - HR Manager at Tidewell Energy Supply | LinkedIn",
            "link": "https://uk.linkedin.com/in/jamie-ross",
            "snippet": (
                "HR Manager at Tidewell Energy Supply · London, United "
                "Kingdom · Recruitment and culture"
            ),
        },  # confirm
    ],
}

HUNTER_RESPONSES: dict[str, Any] = {
    "domain_search": {
        "asterharbour.example.com": {
            "data": {
                "domain": "asterharbour.example.com",
                "organization": "Aster Harbour Logistics",
                "pattern": "{first}.{last}",
                "accept_all": False,
                "emails": [
                    {
                        "value": "mira.tan@asterharbour.example.com",
                        "type": "personal",
                        "confidence": 90,
                        "first_name": "Mira",
                        "last_name": "Tan",
                    },
                ],
            },
            "meta": {
                "results": 14,
                "limit": 1,
                "offset": 0,
            },
        },
        "bayline.example.com": {
            "data": {
                "domain": "bayline.example.com",
                "organization": "Bayline Insurance",
                "pattern": "{f}{last}",
                "accept_all": False,
                "emails": [
                    {
                        "value": "praman@bayline.example.com",
                        "type": "personal",
                        "confidence": 90,
                        "first_name": "Priya",
                        "last_name": "Raman",
                    },
                ],
            },
            "meta": {
                "results": 9,
                "limit": 1,
                "offset": 0,
            },
        },
        "crescentmarine.example.com": {
            "data": {
                "domain": "crescentmarine.example.com",
                "organization": "Crescent Marine Services",
                "pattern": "{first}.{last}",
                "accept_all": False,
                "emails": [
                    {
                        "value": "hana.mori@crescentmarine.example.com",
                        "type": "personal",
                        "confidence": 90,
                        "first_name": "Hana",
                        "last_name": "Mori",
                    },
                ],
            },
            "meta": {
                "results": 21,
                "limit": 1,
                "offset": 0,
            },
        },
        "driftwoodfoods.example.com": {
            "data": {
                "domain": "driftwoodfoods.example.com",
                "organization": "Driftwood Foods",
                "pattern": "{first}_{last}",
                "accept_all": False,
                "emails": [
                    {
                        "value": "ines_duarte@driftwoodfoods.example.com",
                        "type": "personal",
                        "confidence": 90,
                        "first_name": "Ines",
                        "last_name": "Duarte",
                    },
                ],
            },
            "meta": {
                "results": 4,
                "limit": 1,
                "offset": 0,
            },
        },
        "evergreenclinics.example.com": {
            "data": {
                "domain": "evergreenclinics.example.com",
                "organization": "Evergreen Clinics",
                "pattern": None,
                "accept_all": False,
                "emails": [],
            },
            "meta": {
                "results": 0,
                "limit": 1,
                "offset": 0,
            },
        },
        "fernhillretail.example.com": {
            "data": {
                "domain": "fernhillretail.example.com",
                "organization": "Fernhill Retail Group",
                "pattern": "{f}.{last}",
                "accept_all": False,
                "emails": [
                    {
                        "value": "m.kato@fernhillretail.example.com",
                        "type": "personal",
                        "confidence": 90,
                        "first_name": "Mina",
                        "last_name": "Kato",
                    },
                ],
            },
            "meta": {
                "results": 6,
                "limit": 1,
                "offset": 0,
            },
        },
        "quillon.example.org": {
            "data": {
                "domain": "quillon.example.org",
                "organization": "Quillon Telecom",
                "pattern": "{first}.{last}",
                "accept_all": False,
                "emails": [
                    {
                        "value": "ethan.koh@quillon.example.org",
                        "type": "personal",
                        "confidence": 90,
                        "first_name": "Ethan",
                        "last_name": "Koh",
                    },
                ],
            },
            "meta": {
                "results": 5,
                "limit": 1,
                "offset": 0,
            },
        },
        "thornbury.example.org": {
            "data": {
                "domain": "thornbury.example.org",
                "organization": "Thornbury Cold Storage",
                "pattern": "{first}",
                "accept_all": True,
                "emails": [
                    {
                        "value": "emily@thornbury.example.org",
                        "type": "personal",
                        "confidence": 90,
                        "first_name": "Emily",
                        "last_name": "Hart",
                    },
                ],
            },
            "meta": {
                "results": 12,
                "limit": 1,
                "offset": 0,
            },
        },
        "westbrookdental.example.org": {
            "data": {
                "domain": "westbrookdental.example.org",
                "organization": "Westbrook Dental Group",
                "pattern": "{first}.{last}",
                "accept_all": False,
                "emails": [
                    {
                        "value": "freya.clarke@westbrookdental.example.org",
                        "type": "personal",
                        "confidence": 90,
                        "first_name": "Freya",
                        "last_name": "Clarke",
                    },
                ],
            },
            "meta": {
                "results": 3,
                "limit": 1,
                "offset": 0,
            },
        },
        "marlowpines.example.org": {
            "data": {
                "domain": "marlowpines.example.org",
                "organization": "Marlow Pines Care Homes",
                "pattern": None,
                "accept_all": False,
                "emails": [],
            },
            "meta": {
                "results": 0,
                "limit": 1,
                "offset": 0,
            },
        },
        "sedgemoor.example.org": {
            "data": {
                "domain": "sedgemoor.example.org",
                "organization": "Sedgemoor Textiles",
                "pattern": "{last}.{first}",
                "accept_all": False,
                "emails": [
                    {
                        "value": "brennan.holly@sedgemoor.example.org",
                        "type": "personal",
                        "confidence": 90,
                        "first_name": "Holly",
                        "last_name": "Brennan",
                    },
                ],
            },
            "meta": {
                "results": 7,
                "limit": 1,
                "offset": 0,
            },
        },
        "tidewell.example.org": {
            "data": {
                "domain": "tidewell.example.org",
                "organization": "Tidewell Energy Supply",
                "pattern": "{first}.{last}",
                "accept_all": False,
                "emails": [
                    {
                        "value": "ruby.alder@tidewell.example.org",
                        "type": "personal",
                        "confidence": 90,
                        "first_name": "Ruby",
                        "last_name": "Alder",
                    },
                ],
            },
            "meta": {
                "results": 4,
                "limit": 1,
                "offset": 0,
            },
        },
    },
    "email_finder": {
        "asterharbour.example.com|Mira Tan": {
            "data": {
                "email": "mira.tan@asterharbour.example.com",
                "score": 88,
                "first_name": "Mira",
                "last_name": "Tan",
                "domain": "asterharbour.example.com",
            },
        },
        "bayline.example.com|Grace Lau": {
            "data": {
                "email": "glau@bayline.example.com",
                "score": 88,
                "first_name": "Grace",
                "last_name": "Lau",
                "domain": "bayline.example.com",
            },
        },
    },
    "email_verifier": {
        "mira.tan@asterharbour.example.com": {"data": {"status": "valid"}},
        "dev.iyer@asterharbour.example.com": {"data": {"status": "valid"}},
        "lena.ong@asterharbour.example.com": {"data": {"status": "valid"}},
        "ravi.menon@asterharbour.example.com": {"data": {"status": "unknown"}},
        "joy.lim@asterharbour.example.com": {"data": {"status": "invalid"}},
        "aidan.blake@asterharbour.example.com": {"data": {"status": "valid"}},
        "sofia.ng@asterharbour.example.com": {"data": {"status": "valid"}},
        "tomas.berg@asterharbour.example.com": {"data": {"status": "valid"}},
        "ana.ruiz@asterharbour.example.com": {"data": {"status": "valid"}},
        "owen.park@asterharbour.example.com": {"data": {"status": "valid"}},
        "praman@bayline.example.com": {"data": {"status": "valid"}},
        "ksato@bayline.example.com": {"data": {"status": "valid"}},
        "glau@bayline.example.com": {"data": {"status": "valid"}},
        "nrahman@bayline.example.com": {"data": {"status": "invalid"}},
        "kasante@bayline.example.com": {"data": {"status": "valid"}},
        "emensah@bayline.example.com": {"data": {"status": "valid"}},
        "mchen@bayline.example.com": {"data": {"status": "valid"}},
        "vng@bayline.example.com": {"data": {"status": "valid"}},
        "sortiz@bayline.example.com": {"data": {"status": "valid"}},
        "clim@bayline.example.com": {"data": {"status": "valid"}},
        "hana.mori@crescentmarine.example.com": {"data": {"status": "unknown"}},
        "ruth.ong@crescentmarine.example.com": {"data": {"status": "valid"}},
        "ben.adler@crescentmarine.example.com": {"data": {"status": "invalid"}},
        "alan.teo@crescentmarine.example.com": {"data": {"status": "valid"}},
        "dara.quek@crescentmarine.example.com": {"data": {"status": "valid"}},
        "marcus.wong@crescentmarine.example.com": {"data": {"status": "valid"}},
        "noor.hassan@crescentmarine.example.com": {"data": {"status": "valid"}},
        "greta.holm@crescentmarine.example.com": {"data": {"status": "valid"}},
        "simon.lok@crescentmarine.example.com": {"data": {"status": "valid"}},
        "ines_duarte@driftwoodfoods.example.com": {"data": {"status": "valid"}},
        "tara_vance@driftwoodfoods.example.com": {"data": {"status": "valid"}},
        "olu_bakare@driftwoodfoods.example.com": {"data": {"status": "invalid"}},
        "chloe_martin@driftwoodfoods.example.com": {"data": {"status": "valid"}},
        "hugo_laurent@driftwoodfoods.example.com": {"data": {"status": "valid"}},
        "ellie_marsh@driftwoodfoods.example.com": {"data": {"status": "valid"}},
        "rohan_kapoor@driftwoodfoods.example.com": {"data": {"status": "valid"}},
        "iris_teo@driftwoodfoods.example.com": {"data": {"status": "valid"}},
        "jun_park@driftwoodfoods.example.com": {"data": {"status": "unknown"}},
        "leon_tay@driftwoodfoods.example.com": {"data": {"status": "valid"}},
        "amara.osei@evergreenclinics.example.com": {"data": {"status": "valid"}},
        "ivan.petrov@evergreenclinics.example.com": {"data": {"status": "invalid"}},
        "zoe.tan@evergreenclinics.example.com": {"data": {"status": "valid"}},
        "ming.li@evergreenclinics.example.com": {"data": {"status": "valid"}},
        "sanjay.mehta@evergreenclinics.example.com": {"data": {"status": "valid"}},
        "lucia.ferrer@evergreenclinics.example.com": {"data": {"status": "valid"}},
        "sarah.lopez@evergreenclinics.example.com": {"data": {"status": "valid"}},
        "peter.yeo@evergreenclinics.example.com": {"data": {"status": "valid"}},
        "rosa.vidal@evergreenclinics.example.com": {"data": {"status": "valid"}},
        "dana.okoro@evergreenclinics.example.com": {"data": {"status": "valid"}},
        "m.kato@fernhillretail.example.com": {"data": {"status": "invalid"}},
        "o.reid@fernhillretail.example.com": {"data": {"status": "valid"}},
        "h.brooks@fernhillretail.example.com": {"data": {"status": "valid"}},
        "y.ali@fernhillretail.example.com": {"data": {"status": "valid"}},
        "e.lindqvist@fernhillretail.example.com": {"data": {"status": "valid"}},
        "g.soto@fernhillretail.example.com": {"data": {"status": "unknown"}},
        "a.lim@fernhillretail.example.com": {"data": {"status": "valid"}},
        "n.rao@fernhillretail.example.com": {"data": {"status": "valid"}},
        "p.gerber@fernhillretail.example.com": {"data": {"status": "valid"}},
        "l.wan@fernhillretail.example.com": {"data": {"status": "invalid"}},
        "ethan.koh@quillon.example.org": {"data": {"status": "valid"}},
        "gita.nair@quillon.example.org": {"data": {"status": "valid"}},
        "felix.wong@quillon.example.org": {"data": {"status": "valid"}},
        "bella.sim@quillon.example.org": {"data": {"status": "valid"}},
        "ryo.kadota@quillon.example.org": {"data": {"status": "valid"}},
        "helen.tay@quillon.example.org": {"data": {"status": "valid"}},
        "mark.dewi@quillon.example.org": {"data": {"status": "valid"}},
        "aisha.noor@quillon.example.org": {"data": {"status": "valid"}},
        "trevor.gill@quillon.example.org": {"data": {"status": "invalid"}},
        "emily@thornbury.example.org": {"data": {"status": "accept_all"}},
        "callum@thornbury.example.org": {"data": {"status": "accept_all"}},
        "ruby@thornbury.example.org": {"data": {"status": "accept_all"}},
        "jasper@thornbury.example.org": {"data": {"status": "accept_all"}},
        "isla@thornbury.example.org": {"data": {"status": "accept_all"}},
        "george@thornbury.example.org": {"data": {"status": "accept_all"}},
        "lauren@thornbury.example.org": {"data": {"status": "accept_all"}},
        "ahmed@thornbury.example.org": {"data": {"status": "accept_all"}},
        "olivia@thornbury.example.org": {"data": {"status": "accept_all"}},
        "freya.clarke@westbrookdental.example.org": {"data": {"status": "valid"}},
        "tom.ashby@westbrookdental.example.org": {"data": {"status": "valid"}},
        "jess.okafor@westbrookdental.example.org": {"data": {"status": "valid"}},
        "ollie.grant@westbrookdental.example.org": {"data": {"status": "valid"}},
        "leah.simms@westbrookdental.example.org": {"data": {"status": "valid"}},
        "rhys.powell@westbrookdental.example.org": {"data": {"status": "valid"}},
        "sienna.cole@westbrookdental.example.org": {"data": {"status": "valid"}},
        "dylan.fox@westbrookdental.example.org": {"data": {"status": "valid"}},
        "amelia.rhodes@westbrookdental.example.org": {"data": {"status": "invalid"}},
        "cole.barker@westbrookdental.example.org": {"data": {"status": "valid"}},
        "poppy.hale@marlowpines.example.org": {"data": {"status": "valid"}},
        "benji.cross@marlowpines.example.org": {"data": {"status": "unknown"}},
        "nora.bell@marlowpines.example.org": {"data": {"status": "valid"}},
        "cedric.moss@marlowpines.example.org": {"data": {"status": "valid"}},
        "joan.pike@marlowpines.example.org": {"data": {"status": "valid"}},
        "dominic.shaw@marlowpines.example.org": {"data": {"status": "valid"}},
        "freya.marsh@marlowpines.example.org": {"data": {"status": "valid"}},
        "kieran.doyle@marlowpines.example.org": {"data": {"status": "invalid"}},
        "tessa.lowe@marlowpines.example.org": {"data": {"status": "valid"}},
        "ryan.beck@marlowpines.example.org": {"data": {"status": "valid"}},
        "brennan.holly@sedgemoor.example.org": {"data": {"status": "valid"}},
        "ferrier.max@sedgemoor.example.org": {"data": {"status": "valid"}},
        "crowther.ivy@sedgemoor.example.org": {"data": {"status": "valid"}},
        "harper.gus@sedgemoor.example.org": {"data": {"status": "valid"}},
        "burrows.ella@sedgemoor.example.org": {"data": {"status": "valid"}},
        "crofts.neil@sedgemoor.example.org": {"data": {"status": "valid"}},
        "hobbs.leanne@sedgemoor.example.org": {"data": {"status": "invalid"}},
        "parr.joel@sedgemoor.example.org": {"data": {"status": "valid"}},
        "lund.sanna@sedgemoor.example.org": {"data": {"status": "unknown"}},
        "tan.oscar@sedgemoor.example.org": {"data": {"status": "valid"}},
        "ruby.alder@tidewell.example.org": {"data": {"status": "valid"}},
        "finn.murray@tidewell.example.org": {"data": {"status": "valid"}},
        "chloe.bright@tidewell.example.org": {"data": {"status": "valid"}},
        "adam.hewitt@tidewell.example.org": {"data": {"status": "valid"}},
        "jade.coker@tidewell.example.org": {"data": {"status": "valid"}},
        "robin.walsh@tidewell.example.org": {"data": {"status": "invalid"}},
        "mia.chapman@tidewell.example.org": {"data": {"status": "valid"}},
        "gareth.owen@tidewell.example.org": {"data": {"status": "valid"}},
        "sophie.naylor@tidewell.example.org": {"data": {"status": "valid"}},
        "jamie.ross@tidewell.example.org": {"data": {"status": "valid"}},
    },
    "account": {
        "data": {
            "requests": {
                "credits": {
                    "used": 12,
                    "available": 50,
                    "remaining": 38,
                },
                "searches": {
                    "used": 0,
                    "available": 0,
                    "remaining": 0,
                },
                "verifications": {
                    "used": 0,
                    "available": 0,
                    "remaining": 0,
                },
            },
        },
    },
}

REOON_RESPONSES: dict[str, Any] = {
    "verify": {
        "mira.tan@asterharbour.example.com": {"status": "safe", "is_catch_all": False},
        "dev.iyer@asterharbour.example.com": {"status": "safe", "is_catch_all": False},
        "lena.ong@asterharbour.example.com": {"status": "safe", "is_catch_all": False},
        "ravi.menon@asterharbour.example.com": {"status": "unknown", "is_catch_all": False},
        "joy.lim@asterharbour.example.com": {"status": "invalid", "is_catch_all": False},
        "aidan.blake@asterharbour.example.com": {"status": "safe", "is_catch_all": False},
        "sofia.ng@asterharbour.example.com": {"status": "inbox_full", "is_catch_all": False},
        "tomas.berg@asterharbour.example.com": {"status": "safe", "is_catch_all": False},
        "ana.ruiz@asterharbour.example.com": {"status": "safe", "is_catch_all": False},
        "owen.park@asterharbour.example.com": {"status": "safe", "is_catch_all": False},
        "praman@bayline.example.com": {"status": "safe", "is_catch_all": False},
        "ksato@bayline.example.com": {"status": "safe", "is_catch_all": False},
        "glau@bayline.example.com": {"status": "safe", "is_catch_all": False},
        "nrahman@bayline.example.com": {"status": "invalid", "is_catch_all": False},
        "kasante@bayline.example.com": {"status": "safe", "is_catch_all": False},
        "emensah@bayline.example.com": {"status": "safe", "is_catch_all": False},
        "mchen@bayline.example.com": {"status": "safe", "is_catch_all": False},
        "vng@bayline.example.com": {"status": "safe", "is_catch_all": False},
        "sortiz@bayline.example.com": {"status": "safe", "is_catch_all": False},
        "clim@bayline.example.com": {"status": "inbox_full", "is_catch_all": False},
        "hana.mori@crescentmarine.example.com": {"status": "unknown", "is_catch_all": False},
        "ruth.ong@crescentmarine.example.com": {"status": "safe", "is_catch_all": False},
        "ben.adler@crescentmarine.example.com": {"status": "invalid", "is_catch_all": False},
        "alan.teo@crescentmarine.example.com": {"status": "safe", "is_catch_all": False},
        "dara.quek@crescentmarine.example.com": {"status": "safe", "is_catch_all": False},
        "marcus.wong@crescentmarine.example.com": {"status": "safe", "is_catch_all": False},
        "noor.hassan@crescentmarine.example.com": {"status": "safe", "is_catch_all": False},
        "greta.holm@crescentmarine.example.com": {"status": "safe", "is_catch_all": False},
        "simon.lok@crescentmarine.example.com": {"status": "safe", "is_catch_all": False},
        "ines_duarte@driftwoodfoods.example.com": {"status": "safe", "is_catch_all": False},
        "tara_vance@driftwoodfoods.example.com": {"status": "safe", "is_catch_all": False},
        "olu_bakare@driftwoodfoods.example.com": {"status": "invalid", "is_catch_all": False},
        "chloe_martin@driftwoodfoods.example.com": {"status": "inbox_full", "is_catch_all": False},
        "hugo_laurent@driftwoodfoods.example.com": {"status": "safe", "is_catch_all": False},
        "ellie_marsh@driftwoodfoods.example.com": {"status": "safe", "is_catch_all": False},
        "rohan_kapoor@driftwoodfoods.example.com": {"status": "safe", "is_catch_all": False},
        "iris_teo@driftwoodfoods.example.com": {"status": "safe", "is_catch_all": False},
        "jun_park@driftwoodfoods.example.com": {"status": "unknown", "is_catch_all": False},
        "leon_tay@driftwoodfoods.example.com": {"status": "safe", "is_catch_all": False},
        "amara.osei@evergreenclinics.example.com": {"status": "safe", "is_catch_all": False},
        "ivan.petrov@evergreenclinics.example.com": {"status": "invalid", "is_catch_all": False},
        "zoe.tan@evergreenclinics.example.com": {"status": "safe", "is_catch_all": False},
        "ming.li@evergreenclinics.example.com": {"status": "safe", "is_catch_all": False},
        "sanjay.mehta@evergreenclinics.example.com": {"status": "safe", "is_catch_all": False},
        "lucia.ferrer@evergreenclinics.example.com": {"status": "safe", "is_catch_all": False},
        "sarah.lopez@evergreenclinics.example.com": {"status": "inbox_full", "is_catch_all": False},
        "peter.yeo@evergreenclinics.example.com": {"status": "safe", "is_catch_all": False},
        "rosa.vidal@evergreenclinics.example.com": {"status": "safe", "is_catch_all": False},
        "dana.okoro@evergreenclinics.example.com": {"status": "safe", "is_catch_all": False},
        "m.kato@fernhillretail.example.com": {"status": "invalid", "is_catch_all": False},
        "o.reid@fernhillretail.example.com": {"status": "safe", "is_catch_all": False},
        "h.brooks@fernhillretail.example.com": {"status": "safe", "is_catch_all": False},
        "y.ali@fernhillretail.example.com": {"status": "safe", "is_catch_all": False},
        "e.lindqvist@fernhillretail.example.com": {"status": "safe", "is_catch_all": False},
        "g.soto@fernhillretail.example.com": {"status": "unknown", "is_catch_all": False},
        "a.lim@fernhillretail.example.com": {"status": "safe", "is_catch_all": False},
        "n.rao@fernhillretail.example.com": {"status": "safe", "is_catch_all": False},
        "p.gerber@fernhillretail.example.com": {"status": "safe", "is_catch_all": False},
        "l.wan@fernhillretail.example.com": {"status": "invalid", "is_catch_all": False},
        "ethan.koh@quillon.example.org": {"status": "safe", "is_catch_all": False},
        "gita.nair@quillon.example.org": {"status": "safe", "is_catch_all": False},
        "felix.wong@quillon.example.org": {"status": "safe", "is_catch_all": False},
        "bella.sim@quillon.example.org": {"status": "safe", "is_catch_all": False},
        "ryo.kadota@quillon.example.org": {"status": "safe", "is_catch_all": False},
        "helen.tay@quillon.example.org": {"status": "safe", "is_catch_all": False},
        "mark.dewi@quillon.example.org": {"status": "safe", "is_catch_all": False},
        "aisha.noor@quillon.example.org": {"status": "safe", "is_catch_all": False},
        "trevor.gill@quillon.example.org": {"status": "invalid", "is_catch_all": False},
        "emily@thornbury.example.org": {"status": "catch_all", "is_catch_all": True},
        "callum@thornbury.example.org": {"status": "catch_all", "is_catch_all": True},
        "ruby@thornbury.example.org": {"status": "catch_all", "is_catch_all": True},
        "jasper@thornbury.example.org": {"status": "catch_all", "is_catch_all": True},
        "isla@thornbury.example.org": {"status": "catch_all", "is_catch_all": True},
        "george@thornbury.example.org": {"status": "catch_all", "is_catch_all": True},
        "lauren@thornbury.example.org": {"status": "catch_all", "is_catch_all": True},
        "ahmed@thornbury.example.org": {"status": "catch_all", "is_catch_all": True},
        "olivia@thornbury.example.org": {"status": "catch_all", "is_catch_all": True},
        "freya.clarke@westbrookdental.example.org": {"status": "safe", "is_catch_all": False},
        "tom.ashby@westbrookdental.example.org": {"status": "safe", "is_catch_all": False},
        "jess.okafor@westbrookdental.example.org": {"status": "safe", "is_catch_all": False},
        "ollie.grant@westbrookdental.example.org": {"status": "safe", "is_catch_all": False},
        "leah.simms@westbrookdental.example.org": {"status": "safe", "is_catch_all": False},
        "rhys.powell@westbrookdental.example.org": {"status": "safe", "is_catch_all": False},
        "sienna.cole@westbrookdental.example.org": {"status": "safe", "is_catch_all": False},
        "dylan.fox@westbrookdental.example.org": {"status": "inbox_full", "is_catch_all": False},
        "amelia.rhodes@westbrookdental.example.org": {"status": "invalid", "is_catch_all": False},
        "cole.barker@westbrookdental.example.org": {"status": "safe", "is_catch_all": False},
        "poppy.hale@marlowpines.example.org": {"status": "safe", "is_catch_all": False},
        "benji.cross@marlowpines.example.org": {"status": "unknown", "is_catch_all": False},
        "nora.bell@marlowpines.example.org": {"status": "safe", "is_catch_all": False},
        "cedric.moss@marlowpines.example.org": {"status": "safe", "is_catch_all": False},
        "joan.pike@marlowpines.example.org": {"status": "safe", "is_catch_all": False},
        "dominic.shaw@marlowpines.example.org": {"status": "safe", "is_catch_all": False},
        "freya.marsh@marlowpines.example.org": {"status": "safe", "is_catch_all": False},
        "kieran.doyle@marlowpines.example.org": {"status": "invalid", "is_catch_all": False},
        "tessa.lowe@marlowpines.example.org": {"status": "safe", "is_catch_all": False},
        "ryan.beck@marlowpines.example.org": {"status": "safe", "is_catch_all": False},
        "brennan.holly@sedgemoor.example.org": {"status": "inbox_full", "is_catch_all": False},
        "ferrier.max@sedgemoor.example.org": {"status": "safe", "is_catch_all": False},
        "crowther.ivy@sedgemoor.example.org": {"status": "safe", "is_catch_all": False},
        "harper.gus@sedgemoor.example.org": {"status": "safe", "is_catch_all": False},
        "burrows.ella@sedgemoor.example.org": {"status": "safe", "is_catch_all": False},
        "crofts.neil@sedgemoor.example.org": {"status": "safe", "is_catch_all": False},
        "hobbs.leanne@sedgemoor.example.org": {"status": "invalid", "is_catch_all": False},
        "parr.joel@sedgemoor.example.org": {"status": "safe", "is_catch_all": False},
        "lund.sanna@sedgemoor.example.org": {"status": "unknown", "is_catch_all": False},
        "tan.oscar@sedgemoor.example.org": {"status": "safe", "is_catch_all": False},
        "ruby.alder@tidewell.example.org": {"status": "safe", "is_catch_all": False},
        "finn.murray@tidewell.example.org": {"status": "safe", "is_catch_all": False},
        "chloe.bright@tidewell.example.org": {"status": "safe", "is_catch_all": False},
        "adam.hewitt@tidewell.example.org": {"status": "inbox_full", "is_catch_all": False},
        "jade.coker@tidewell.example.org": {"status": "safe", "is_catch_all": False},
        "robin.walsh@tidewell.example.org": {"status": "invalid", "is_catch_all": False},
        "mia.chapman@tidewell.example.org": {"status": "safe", "is_catch_all": False},
        "gareth.owen@tidewell.example.org": {"status": "safe", "is_catch_all": False},
        "sophie.naylor@tidewell.example.org": {"status": "safe", "is_catch_all": False},
        "jamie.ross@tidewell.example.org": {"status": "safe", "is_catch_all": False},
    },
    "balance": {
        "api_status": "active",
        "remaining_daily_credits": 20,
        "remaining_instant_credits": 100,
    },
    "errors": {
        "quota": {
            "status": "error",
            "reason": "Insufficient credits on this account",
        },
        "auth": {
            "status": "error",
            "reason": "Invalid API key",
        },
    },
}
