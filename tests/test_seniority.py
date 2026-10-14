"""Seniority levels and the top-leader check (PLAN section 6 and 11.7)."""

from __future__ import annotations

import pytest
from fixtures.titles import TITLE_CASES

from leadgen.services.seniority import LEVELS, is_top_leader, meets, seniority

SENIORITY_CASES = [
    # Manager ranks.
    ("Operations Manager", "manager"),
    ("Assistant Manager", "manager"),
    ("Deputy Manager, Finance", "manager"),
    ("Associate Manager", "manager"),
    ("Team Lead", "manager"),
    ("HR Business Partner", "manager"),  # the one Partner that counts as manager
    ("Senior HR Business Partner", "manager"),
    ("General Manager", "manager"),
    # Director ranks.
    ("Director of Sales", "director"),
    ("Assistant Director", "director"),
    ("Deputy Director", "director"),
    ("Associate Director", "director"),
    ("Executive Director", "director"),  # "Executive" is not individual here
    ("Regional Director at Example Energy", "director"),
    # VP ranks.
    ("AVP Marketing", "vp"),
    ("SVP, Operations", "vp"),
    ("EVP Sales", "vp"),
    ("VP of Finance", "vp"),
    ("Vice President", "vp"),
    ("Vice-President Operations", "vp"),
    ("Assistant Vice President", "vp"),
    ("Executive Vice President", "vp"),
    ("Head of Learning", "vp"),
    ("Head, Safety and Health", "vp"),
    ("Global Head of People", "vp"),
    ("HR Head", "vp"),  # trailing "Head"
    ("Head, HR", "vp"),
    ("Head - Learning & Development", "vp"),  # "Head" + separator, like "Head,"
    ("Head – HR", "vp"),
    ("Head — Safety", "vp"),
    ("Head | Talent Development", "vp"),
    ("Head: Training", "vp"),
    ("Country Head", "vp"),
    ("Country Head Singapore", "vp"),
    ("Country Head - Singapore", "vp"),
    ("Country Head | APAC", "vp"),
    ("Country Head of HR", "vp"),
    ("MD", "vp"),
    ("MD - Sales", "vp"),
    ("Managing Director", "vp"),  # a rank, not the head of the company
    ("Group Managing Director", "vp"),
    # C-suite.
    ("CEO", "c_suite"),
    ("COO at Example Rail", "c_suite"),
    ("CFO", "c_suite"),
    ("CTO", "c_suite"),
    ("CHRO", "c_suite"),
    ("CMO", "c_suite"),
    ("Chief Executive Officer", "c_suite"),
    ("Chief of Staff", "c_suite"),
    ("Founder", "c_suite"),
    ("Cofounder at Northwind Logistics", "c_suite"),
    ("Co-Founder & Director", "c_suite"),
    ("Owner", "c_suite"),
    ("President", "c_suite"),
    ("President & CEO", "c_suite"),
    ("Managing Partner", "c_suite"),
    ("Senior Partner", "c_suite"),
    ("Equity Partner", "c_suite"),
    ("Group CEO and Managing Director", "c_suite"),
    ("Group CEO & MD", "c_suite"),
    ("Managing Director and Chief Executive", "c_suite"),
    # Individuals.
    ("Junior Manager", "individual"),  # "Junior" before the rank word
    ("Junior Recruiter", "individual"),
    ("HR Intern", "individual"),
    ("Management Trainee", "individual"),
    ("Trainee Manager", "individual"),
    ("Associate", "individual"),
    ("Senior Associate", "individual"),
    ("Account Executive", "individual"),
    ("Executive Assistant", "individual"),
    ("Training Specialist", "individual"),
    ("Project Coordinator", "individual"),
    ("Compliance Officer", "individual"),
    ("Talent Acquisition Partner", "individual"),
    ("People Partner", "individual"),
    ("Finance Business Partner", "individual"),  # only HR Business Partner counts
    ("Partner", "individual"),
    ("Software Engineer", "individual"),
    ("", "individual"),
    # Rank words that are not ranks.
    ("Product Owner", "individual"),
    ("Process Owner", "individual"),
    ("Lead Generation Specialist", "individual"),
    ("Executive Assistant to the CEO", "individual"),
    ("Assistant to the Managing Director", "individual"),
    ("Machine Learning Engineering Lead", "manager"),
    # Rank before a junior word stays senior.
    ("Manager, Intern Programme", "manager"),
    ("Head of Graduate Trainee Programme", "vp"),
    # Role part only.
    ("Analyst at Head Office Solutions", "individual"),
    ("Engineer @ Chief Dynamics", "individual"),
    ("@ Chief Dynamics", "c_suite"),  # nothing before "@": the whole title is read
]


@pytest.mark.parametrize(("title", "expected"), SENIORITY_CASES)
def test_seniority_table(title, expected):
    assert seniority(title) == expected


def test_table_is_big_enough():
    assert len(SENIORITY_CASES) >= 30


@pytest.mark.parametrize(("title", "_group", "expected"), TITLE_CASES)
def test_fixture_titles(title, _group, expected):
    assert seniority(title) == expected, title


def test_levels_order():
    assert LEVELS == ("individual", "manager", "director", "vp", "c_suite")


def test_meets():
    assert meets("Operations Manager", "manager")
    assert meets("Operations Manager", "individual")
    assert not meets("Operations Manager", "director")
    assert meets("CEO", "c_suite")
    assert not meets("Managing Director", "c_suite")
    assert not meets("HR Executive", "manager")
    assert meets("Head - Learning & Development", "manager")
    assert meets("MD - Sales", "manager")
    assert meets("Cofounder at Northwind Logistics", "manager")
    with pytest.raises(ValueError, match="unknown seniority level"):
        meets("CEO", "boss")


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("CEO at Example Facility Services", True),
        ("Founder", True),
        ("Co-Founder, Northwind Logistics", True),
        ("Owner", True),
        ("Business Owner", True),
        ("Proprietor", True),
        ("Chairman", True),
        ("Chief Executive Officer", True),
        ("President", True),
        ("President and CEO", True),
        ("Managing Partner", True),
        ("Country Manager, Singapore", True),
        ("Country Head", True),
        ("Country Head Singapore", True),
        ("Country Head - Singapore", True),
        ("Country Head | APAC", True),
        ("Country Head, APAC", True),
        ("Cofounder at Northwind Logistics", True),
        ("Group CEO & MD", True),
        ("MD - Sales", False),
        ("Deputy Country Head", False),
        ("Country Head of HR", False),  # a head of HR, not of the country
        ("Head - Learning & Development", False),
        ("General Manager", True),
        ("Group CEO and Managing Director", True),
        ("Managing Director", False),  # a rank at large organisations
        ("MD", False),
        ("Principal", False),
        ("Executive Director", False),
        ("Vice President", False),
        ("Vice-President of Operations", False),
        ("Senior Vice President, Human", False),
        ("Assistant General Manager", False),
        ("Deputy CEO", False),
        ("Associate Founder", False),
        ("Product Owner", False),
        ("Executive Assistant to the CEO", False),
        ("Engineer at Chairman Foods", False),  # employer is ignored
        ("Operations Manager", False),
        ("", False),
    ],
)
def test_is_top_leader(title, expected):
    assert is_top_leader(title) is expected
