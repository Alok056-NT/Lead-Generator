"""Designation groups, templates and `classify` (PLAN section 6)."""

from __future__ import annotations

import pytest
from fixtures.titles import TITLE_CASES

from leadgen.services import designations as d
from leadgen.services.designations import DesignationError, DesignationGroup, classify

HARVEY = [d.TEMPLATES[k] for k in ("ld", "safety_ops", "hr", "owners")]
LD, SAFETY, HR, OWNERS = (d.TEMPLATES[k].label for k in ("ld", "safety_ops", "hr", "owners"))
OTHER = d.DESIGNATION_OTHER

# (title, expected label) with Harvey's group order: ld, safety_ops, hr, owners.
CLASSIFY_CASES = [
    # Card examples (titles only, invented employers).
    ("Head of IT Operations", OTHER),  # false friend: "IT operations"
    ("CEO at Example Facility Services", OWNERS),  # role part only; "Facility" is the employer
    ("Senior Vice President, Human", OTHER),  # "vice president" is vp, "human" alone is nothing
    ("Training Manager@Example Utility", LD),  # role part before "@"
    ("Machine Learning Engineering Lead", OTHER),  # false friend
    # False friends.
    ("Deep Learning Researcher", OTHER),
    ("Reinforcement Learning Scientist", OTHER),
    ("Statistical Learning Analyst", OTHER),
    ("Model Training Lead", OTHER),
    ("Quality of Life Manager", OTHER),
    ("Network Operations Manager", OTHER),
    ("Cloud Operations Lead", OTHER),
    ("Security Operations Director", OTHER),
    ("Cyber Operations Manager", OTHER),
    ("Data Operations Analyst", OTHER),
    ("IT Infrastructure Operations Manager", OTHER),
    ("Cloud Operations", OTHER),  # only a false friend, still a non-empty title
    ("Operations Manager", SAFETY),  # the real thing still matches
    ("Learning and Development Manager", LD),  # "learning" is a rule word on its own
    # Vice president is never "president".
    ("Vice President, Sales", OTHER),
    ("Vice-President Marketing", OTHER),
    ("Executive Vice President", OTHER),
    ("President", OWNERS),
    ("President and CEO", OWNERS),
    ("Vice President of Operations", SAFETY),
    # L&D spellings.
    ("L&D Lead", LD),
    ("L & D Business Partner", LD),
    ("L and D Manager", LD),
    ("Head of L&D at Example Retail", LD),
    # Whole words, prefixes, hyphens.
    ("Chrome Developer", OTHER),  # "hr" must not match "Chrome"
    ("Recruiting Lead", HR),  # "recruit*" prefix
    ("Human Resources Business Partner", HR),  # "human resource*" prefix
    ("Co-Founder", OWNERS),  # hyphenated keyword
    ("Cofounder and CTO", OWNERS),
    ("Facility Manager", SAFETY),  # "facilit*" prefix
    ("Plant Supervisor", SAFETY),
    ("Site Manager", SAFETY),  # multi-word keyword
    ("Site Engineer", OTHER),  # multi-word keyword needs the whole run
    ("Talent Development Lead", LD),
    ("Employee Development Manager", LD),  # keyword added to the Harvey list
    ("HRBP", HR),
    # First matching group wins.
    ("Head of Training & Safety", LD),
    ("People Operations Manager", SAFETY),
    ("Chief People Officer", HR),
    ("Managing Director", OWNERS),
    # Role part only.
    ("Marketing Manager at Example Training Ltd", OTHER),
    ("HR Director @ Example Foods", HR),
    ("Engineer at Safety First Ltd", OTHER),
    ("Operations Lead at Example Operations", SAFETY),
    ("@ Example Safety", SAFETY),  # nothing before "@": falls back to the whole title
    # Case and punctuation.
    ("HEAD OF TRAINING", LD),
    ("training, learning & development", LD),
]


@pytest.mark.parametrize(("title", "expected"), CLASSIFY_CASES)
def test_classify_table(title, expected):
    assert classify(title, HARVEY) == expected


def test_classify_table_is_big_enough():
    assert len(CLASSIFY_CASES) >= 40


def test_classify_empty_title():
    assert classify("", HARVEY) == ""
    assert classify("   ", HARVEY) == ""
    assert classify("-- !!", HARVEY) == ""
    assert classify(None, HARVEY) == ""  # type: ignore[arg-type]


def test_classify_no_groups_is_other():
    assert classify("Head of Training", []) == OTHER


def test_group_order_decides():
    ops_first = [d.TEMPLATES["safety_ops"], d.TEMPLATES["facilities"]]
    fac_first = [d.TEMPLATES["facilities"], d.TEMPLATES["safety_ops"]]
    assert classify("Facilities Manager", ops_first) == SAFETY
    assert classify("Facilities Manager", fac_first) == d.TEMPLATES["facilities"].label


def test_classify_accepts_plain_dicts():
    groups = [{"label": "Chefs", "keywords": ["Chef*"], "search_terms": ["head chef"]}]
    assert classify("Head Chef at Example Bistro", groups) == "Chefs"
    assert classify("Sous-Chef", groups) == "Chefs"


def test_keywords_with_ampersand_and_spaces():
    groups = [DesignationGroup("R&D", ["r&d", "L and D"], ["r&d director"])]
    assert groups[0].keywords == ["r&d", "l and d"]
    assert classify("Head of R&D", groups) == "R&D"
    assert classify("Head of R & D", groups) == "R&D"  # "&" splits words in titles and keywords
    assert classify("L & D Manager", groups) == "R&D"
    assert classify("Sales Manager", groups) == OTHER


def test_matches_is_per_group():
    assert d.matches("Training Manager at Example Safety", d.TEMPLATES["ld"])
    assert not d.matches("Training Manager at Example Safety", d.TEMPLATES["safety_ops"])
    assert d.matches("Training Manager", d.TEMPLATES["ld"].to_dict())


def test_title_words_and_role_part():
    assert d.role_part("CEO at Example Foods") == "CEO"
    assert d.role_part("Training Manager@Example Utility") == "Training Manager"
    assert d.role_part("Manager at A at B") == "Manager"
    assert d.title_words("L & D / Vice-President") == ["l&d", "vp"]
    assert d.title_words("Machine Learning Engineer") == ["engineer"]


# --- templates ------------------------------------------------------------------------------


def test_templates_cover_the_card_list():
    assert list(d.TEMPLATES) == [
        "ld", "hr", "safety_ops", "owners", "marketing", "sales", "it", "finance",
        "procurement", "facilities", "customer_service",
    ]


def test_every_template_is_sensible():
    for key, group in d.TEMPLATES.items():
        assert group.label, key
        assert 4 <= len(group.search_terms) <= 7, key
        assert len(group.keywords) >= 8, key
        assert all(k == k.lower() for k in group.keywords), key
        assert len(set(group.keywords)) == len(group.keywords), key


def test_first_four_templates_keep_harveys_rules():
    ld, hr, ops, owners = (d.TEMPLATES[k] for k in ("ld", "hr", "safety_ops", "owners"))
    assert ld.label == "L&D / Training heads"
    assert {
        "learning", "l&d", "training", "trainer", "capability", "instructional", "elearning",
        "employee development", "people development", "staff development",
        "leadership development",
    } <= set(ld.keywords)
    assert len(ld.keywords) == 16
    assert hr.label == "HR leaders"
    assert hr.keywords == [
        "hr", "hrbp", "human resource*", "people", "talent", "recruit*", "culture",
        "employee experience", "chro",
    ]
    assert ops.label == "Safety / Operations"
    assert ops.keywords == [
        "safety", "ehs", "wsh", "hse", "operation*", "facilit*", "quality", "compliance",
        "maintenance", "production", "plant", "site manager",
    ]
    assert owners.label == "Business owners / MDs"
    assert len(owners.keywords) == 16
    assert {"ceo", "founder", "md", "general manager", "country head"} <= set(owners.keywords)


def test_list_templates():
    listed = d.list_templates()
    assert [t["key"] for t in listed] == list(d.TEMPLATES)
    assert listed[0]["label"] == "L&D / Training heads"
    listed[0]["keywords"].append("changed")  # copies: the templates stay intact
    assert "changed" not in d.TEMPLATES["ld"].keywords


@pytest.mark.parametrize(("title", "group", "_seniority"), TITLE_CASES)
def test_fixture_titles_match_only_their_group(title, group, _seniority):
    hits = [key for key, g in d.TEMPLATES.items() if d.matches(title, g)]
    assert hits == ([] if group == "other" else [group]), title


def test_fixture_is_big_enough():
    assert len(TITLE_CASES) >= 60


# --- group validation -----------------------------------------------------------------------


def test_group_cleans_input():
    keywords = ["  Safety ", "safety", "OPERATION*"]
    g = DesignationGroup("  My  Group ", keywords, [" head  of x ", ""])
    assert g.label == "My Group"
    assert g.keywords == ["safety", "operation*"]
    assert g.search_terms == ["head of x"]
    assert g.to_dict() == {
        "label": "My Group",
        "keywords": ["safety", "operation*"],
        "search_terms": ["head of x"],
    }


@pytest.mark.parametrize(
    "data",
    [
        {"label": "", "keywords": ["a"], "search_terms": ["a"]},
        {"keywords": ["a"], "search_terms": ["a"]},
        {"label": "X", "keywords": [], "search_terms": ["a"]},
        {"label": "X", "keywords": ["a"], "search_terms": []},
        {"label": "X", "keywords": "abc", "search_terms": ["a"]},
        {"label": "X", "keywords": [1], "search_terms": ["a"]},
        {"label": "X", "keywords": ["a"], "search_terms": ["a"], "extra": 1},
        {"label": 5, "keywords": ["a"], "search_terms": ["a"]},
        "not a dict",
    ],
)
def test_group_rejects_bad_input(data):
    with pytest.raises(DesignationError):
        DesignationGroup.from_dict(data)


def test_resolve_groups_mixes_keys_dicts_and_groups():
    custom = {"label": "Chefs", "keywords": ["chef"], "search_terms": ["head chef"]}
    groups = d.resolve_groups([" LD ", custom, d.TEMPLATES["hr"]])
    assert [g.label for g in groups] == ["L&D / Training heads", "Chefs", "HR leaders"]
    groups[0].keywords.append("x")  # a template key gives an independent copy
    assert "x" not in d.TEMPLATES["ld"].keywords


def test_resolve_groups_unknown_key():
    with pytest.raises(DesignationError, match="Valid keys: ld, hr"):
        d.resolve_groups(["nope"])
