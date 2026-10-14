"""Tests for the compliance table."""

from __future__ import annotations

import re
from dataclasses import replace

import pytest

from leadgen.core import compliance
from leadgen.core.compliance import (
    DISCLAIMER,
    RULES,
    EmailRule,
    rule_for,
    send_gate,
)
from leadgen.core.countries import COUNTRIES


def test_every_country_has_a_rule_and_nothing_extra():
    assert set(RULES) == set(COUNTRIES)


@pytest.mark.parametrize("iso", sorted(COUNTRIES))
def test_rule_quality(iso):
    r = RULES[iso]
    assert r.country_iso2 == iso
    assert r.sources, iso
    assert all(s.startswith("https://") for s in r.sources)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", r.checked_on)
    assert r.summary.endswith(DISCLAIMER)
    sentences = re.split(r"(?<=[.!?])\s+", r.summary.strip())
    assert 2 <= len(sentences) <= 4, (iso, len(sentences))
    assert r.regime
    assert r.consent_model in compliance.CONSENT_MODELS
    assert r.default_policy in compliance.DEFAULT_POLICIES
    assert {"sender_identity", "unsubscribe"} <= set(r.required_elements)


def test_subject_label_rules():
    labelled = {iso: r.subject_label for iso, r in RULES.items() if r.subject_label}
    assert labelled == {"SG": "<ADV>", "VN": "[QC]", "CN": "AD"}
    for iso, r in RULES.items():
        assert ("subject_label" in r.required_elements) == (iso in labelled)


def test_known_policies():
    assert RULES["US"].default_policy == "allow_with_optout"
    assert RULES["US"].consent_model == "opt_out"
    assert "postal_address" in RULES["US"].required_elements
    for iso in ("CA", "DE", "AU", "NZ", "NL", "AT", "ES", "ZA", "JP", "CH", "IT", "DK"):
        assert RULES[iso].default_policy == "block_without_consent", iso
    assert RULES["GB"].consent_model == "opt_out"
    assert RULES["GB"].default_policy == "warn"
    assert RULES["SG"].default_policy == "allow_with_optout"


def test_eu_countries_share_base_sources_and_germany_is_stricter():
    assert compliance._EPRIVACY[0] in RULES["FR"].sources
    assert RULES["FR"].consent_model == "opt_out"
    assert RULES["IT"].default_policy == "block_without_consent"
    assert RULES["DE"].default_policy == "block_without_consent"


def test_rule_for_is_case_insensitive_and_unknown_is_conservative():
    assert rule_for("sg") is RULES["SG"]
    unknown = rule_for("zz")
    assert unknown.country_iso2 == "ZZ"
    assert unknown.default_policy == "warn"
    assert unknown.summary.endswith(DISCLAIMER)
    assert rule_for("").country_iso2 == "??"


def test_send_gate_dedupes_resolves_names_and_keeps_order():
    rules = send_gate(["SG", "singapore", "UK", "DE", "Atlantis"])
    assert [r.country_iso2 for r in rules] == ["SG", "GB", "DE", "ATLANTIS"]
    assert rules[-1].default_policy == "warn"
    assert send_gate([]) == []


def test_invalid_rule_values_rejected():
    base = RULES["US"]
    with pytest.raises(ValueError, match="consent_model"):
        replace(base, consent_model="maybe")
    with pytest.raises(ValueError, match="default_policy"):
        replace(base, default_policy="shrug")
    with pytest.raises(ValueError, match="required_elements"):
        replace(base, required_elements=("sender_identity", "fax_number"))
    with pytest.raises(ValueError, match="subject_label"):
        replace(base, subject_label="<ADV>")
    assert isinstance(base, EmailRule)


def test_ca_and_ch_require_postal_address_and_truthful_subject():
    for iso in ("CA", "CH"):
        elements = RULES[iso].required_elements
        assert "postal_address" in elements, iso
        assert "truthful_subject" in elements, iso
    assert any("52.01" in src for src in RULES["CA"].sources)


def test_it_and_dk_summaries_cover_business_recipients():
    for iso in ("IT", "DK"):
        summary = RULES[iso].summary
        assert "business recipients" in summary, iso
        assert "should be confirmed" not in summary and "did not settle" not in summary
        assert summary.endswith(DISCLAIMER)
    assert any("danskerhverv.dk" in src for src in RULES["DK"].sources)
