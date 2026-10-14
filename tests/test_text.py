"""Tests for text utilities."""

from __future__ import annotations

import pytest

from leadgen.core import text


def test_slugify_unicode_and_punctuation():
    assert text.slugify("Zoë's Café & Bar!") == "zoe-s-cafe-bar"
    assert text.slugify("  Straße  Größe ") == "strasse-grosse"
    assert text.slugify("Łódź Ørsted") == "lodz-orsted"
    assert text.slugify("日本語") == ""
    assert text.slugify("../../etc/passwd") == "etc-passwd"


def test_normalize_whitespace():
    assert text.normalize_whitespace("  a \n\t b   c ") == "a b c"
    assert text.normalize_whitespace("") == ""


def test_strip_html():
    html = "<div><p>Hello <b>world</b></p><script>bad()</script><style>p{}</style><p>Bye</p></div>"
    assert text.strip_html(html) == "Hello world Bye"
    assert text.strip_html("plain text") == "plain text"


def test_truncate():
    assert text.truncate("short", 10) == "short"
    assert text.truncate("abcdefghij", 5) == "abcd…"
    assert len(text.truncate("x" * 100, 20)) == 20
    assert text.truncate("abc", 0) == ""
    with pytest.raises(ValueError):
        text.truncate("abc", -1)


def test_similarity_bounds():
    assert text.similarity("Hello  World", "hello world") == 1.0
    assert text.similarity("abc", "xyz") == 0.0
    assert text.similarity("", "") == 1.0
    assert text.similarity("abc", "") == 0.0
    mid = text.similarity("Hello Aria, quick note", "Hello Ben, quick note")
    assert 0.0 < mid < 1.0
