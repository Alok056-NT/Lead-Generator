"""Tests for the country table and location matching."""

from __future__ import annotations

import pytest

from leadgen.core import countries
from leadgen.core.countries import COUNTRIES, UnknownCountryError, get, matches_location

REQUIRED = (
    "SG MY ID PH TH VN IN AE SA QA GB IE US CA AU NZ DE FR NL BE ES IT SE DK NO FI CH AT PL PT "
    "BR MX ZA NG KE JP KR HK TW CN"
).split()


def test_all_required_countries_present():
    assert set(REQUIRED) <= set(COUNTRIES)
    assert len(countries.all_countries()) == len(COUNTRIES)


def test_rows_are_well_formed():
    for iso, c in COUNTRIES.items():
        assert c.iso2 == iso and len(iso) == 2 and iso.isupper()
        assert c.gl == ("uk" if iso == "GB" else iso.lower())
        assert 5 <= len(c.cities) <= 15, iso
        assert c.name and c.locale and c.timezone
        assert all(s == s.lower() for s in c.linkedin_subdomains)


def test_timezones_load():
    from zoneinfo import ZoneInfo

    for c in COUNTRIES.values():
        ZoneInfo(c.timezone)


def test_bad_timezone_rejected():
    with pytest.raises(ValueError, match="time zone"):
        countries.Country("X", "XX", "xx", (), (), (), "en-XX", "Mars/Olympus")


def test_no_term_clashes_between_countries():
    # building the indexes raises on a clash; rebuild to prove the table is clean
    countries._build_indexes()


def test_get_by_code_name_alias():
    assert get("Singapore").iso2 == "SG"
    assert get("sg").iso2 == "SG"
    assert get(" SG ").iso2 == "SG"
    assert get("uk").iso2 == "GB"
    assert get("United Kingdom").iso2 == "GB"
    assert get("england").iso2 == "GB"
    assert get("UAE").iso2 == "AE"
    assert get("Deutschland").iso2 == "DE"
    assert get("Brasil").iso2 == "BR"
    assert get("MÉXICO").iso2 == "MX"


@pytest.mark.parametrize("bad", ["Atlantis", "", "   ", "X"])
def test_get_unknown_raises_clear_error(bad):
    with pytest.raises(UnknownCountryError, match="Unknown country"):
        get(bad)


def test_get_non_string_raises():
    with pytest.raises(UnknownCountryError):
        get(None)  # type: ignore[arg-type]


def test_subdomain_alone_matches():
    assert matches_location("SG", "https://sg.linkedin.com/in/x", "")
    assert matches_location("GB", "https://uk.linkedin.com/in/x", None)
    assert not matches_location("IN", "https://sg.linkedin.com/in/x", "")
    assert matches_location(get("SG"), "sg.linkedin.com/in/x", "")  # no scheme


def test_www_subdomain_never_decides():
    assert not matches_location("US", "https://www.linkedin.com/in/x", "")
    assert matches_location("SG", "https://www.linkedin.com/in/x", "Location: Singapore")


def test_non_linkedin_host_ignored():
    assert not matches_location("SG", "https://sg.example.com/in/x", "")
    assert not matches_location("SG", "not a url at all", "")


def test_explicit_location_beats_subdomain_and_company_name():
    text = "Experience: Singapore Airlines · Location: Mumbai"
    url = "https://sg.linkedin.com/in/x"
    assert not matches_location("SG", url, text)
    assert matches_location("IN", url, text)


def test_company_name_alone_counts_without_location_field():
    assert matches_location("SG", "https://www.linkedin.com/in/x", "Experience: Singapore Airlines")


def test_postcode_location_is_ignored_then_subdomain_decides():
    url = "https://sg.linkedin.com/in/x"
    assert matches_location("SG", url, "Location: 228211 · 500+ connections")
    # no subdomain and nothing else recognisable -> False
    assert not matches_location("SG", "https://www.linkedin.com/in/x", "Location: 228211")


def test_city_match():
    assert matches_location("IN", None, "Head of Ops. Location: Bengaluru")
    assert matches_location("IN", None, "Based in Bangalore, working on logistics")
    assert not matches_location("SG", None, "Location: Bengaluru")


def test_location_value_stops_at_separators():
    text = "Location: Mumbai · Experience: Singapore Airlines"
    assert matches_location("IN", None, text)
    assert not matches_location("SG", None, text)
    assert matches_location("IN", None, "Location: Mumbai | Singapore Airlines")
    assert matches_location("IN", None, "Location: Mumbai. Singapore Airlines")


def test_country_name_outranks_city_in_location_value():
    # Birmingham is a UK city, but the value names the United States explicitly
    text = "Location: Birmingham, Alabama, United States"
    assert matches_location("US", None, text)
    assert not matches_location("GB", None, text)


def test_longest_name_wins_over_embedded_name():
    assert matches_location("AU", None, "Location: New South Wales")
    assert not matches_location("GB", None, "Location: New South Wales")
    assert matches_location("GB", None, "Location: Northern Ireland")
    assert not matches_location("IE", None, "Location: Northern Ireland")
    assert matches_location("US", None, "Location: New Mexico")
    assert not matches_location("MX", None, "Location: New Mexico")
    assert matches_location("US", None, "Based in New England")
    assert not matches_location("GB", None, "Based in New England")


def test_accent_and_case_folding():
    assert matches_location("PL", None, "Location: KRAKOW")
    assert matches_location("PL", None, "Location: Kraków")
    assert matches_location("SE", None, "Location: Malmo")
    assert matches_location("BR", None, "Location: São Paulo")
    assert matches_location("PL", None, "Location: Łódź")
    assert matches_location("DE", None, "Location: Düsseldorf")


def test_whole_words_only():
    assert not matches_location("IN", None, "Indiana is a US state")
    assert not matches_location("NG", None, "Niger")
    assert not matches_location("IN", None, "Mumbaikar")


def test_unknown_places_do_not_match():
    assert not matches_location("US", None, "Location: Narnia")
    assert not matches_location("GB", None, "")
    assert not matches_location("GB", None, None)


def test_first_recognised_location_field_is_used():
    text = "Location: 228211 · Location: Mumbai"
    assert matches_location("IN", None, text)


def test_matches_location_accepts_name_string():
    assert matches_location("Singapore", "https://sg.linkedin.com/in/x", "")
    with pytest.raises(UnknownCountryError):
        matches_location("Atlantis", None, "")
