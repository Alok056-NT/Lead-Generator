"""The canned-provider switch (T19): honoured only with BOTH environment variables set."""

from __future__ import annotations

import json

import pytest

from leadgen.providers import fake, registry
from leadgen.providers.base import ProviderError

RULES = {
    "rules": [
        {
            "contains": ['"Northwind Logistics"', "head of learning"],
            "results": [
                {
                    "title": "Aria Tan - Head of Learning at Northwind Logistics | LinkedIn",
                    "link": "https://www.linkedin.com/in/aria-tan",
                    "snippet": "Head of Learning at Northwind Logistics - Singapore",
                }
            ],
        }
    ]
}


@pytest.fixture
def data_file(tmp_path):
    path = tmp_path / "fake.json"
    path.write_text(json.dumps(RULES), encoding="utf-8")
    return path


def test_one_variable_alone_does_nothing(monkeypatch, data_file):
    monkeypatch.setenv("LEADGEN_FAKE_DATA", str(data_file))
    monkeypatch.setenv("LEADGEN_FAKE_PROVIDERS", "1")
    assert fake.fakes_enabled() is False
    with pytest.raises(ProviderError) as err:
        registry.get_search_provider()
    assert err.value.code == "not_configured"
    monkeypatch.delenv("LEADGEN_FAKE_PROVIDERS")
    monkeypatch.setenv("LEADGEN_ALLOW_FAKES", "1")
    assert fake.fakes_enabled() is False


@pytest.mark.parametrize("value", ["0", "true", "yes", ""])
def test_only_the_value_1_counts(monkeypatch, value):
    monkeypatch.setenv("LEADGEN_FAKE_PROVIDERS", value)
    monkeypatch.setenv("LEADGEN_ALLOW_FAKES", "1")
    assert fake.fakes_enabled() is False
    monkeypatch.setenv("LEADGEN_FAKE_PROVIDERS", "1")
    monkeypatch.setenv("LEADGEN_ALLOW_FAKES", value)
    assert fake.fakes_enabled() is False


def test_both_variables_give_the_canned_provider(monkeypatch, data_file):
    monkeypatch.setenv("LEADGEN_FAKE_PROVIDERS", "1")
    monkeypatch.setenv("LEADGEN_ALLOW_FAKES", "1")
    monkeypatch.setenv("LEADGEN_FAKE_DATA", str(data_file))
    provider = registry.get_search_provider()
    hits = provider.search('site:linkedin.com/in ("head of learning") "Northwind Logistics" x')
    assert [h.link for h in hits] == ["https://www.linkedin.com/in/aria-tan"]
    assert provider.search('"Another Company" head of learning') == []


def test_a_missing_or_broken_data_file_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setenv("LEADGEN_FAKE_PROVIDERS", "1")
    monkeypatch.setenv("LEADGEN_ALLOW_FAKES", "1")
    with pytest.raises(ProviderError, match="LEADGEN_FAKE_DATA"):
        registry.get_search_provider()
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    monkeypatch.setenv("LEADGEN_FAKE_DATA", str(bad))
    with pytest.raises(ProviderError, match="cannot be read"):
        registry.get_search_provider()
    bad.write_text(json.dumps({"rules": "x"}), encoding="utf-8")
    with pytest.raises(ProviderError, match="list of rules"):
        registry.get_search_provider()
