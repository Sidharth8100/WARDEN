"""Unit tests for URL canonicalization in warden_net."""

import hashlib

import pytest

from warden_net.canonicalize import canonicalize_url


def test_canonicalize_url_normalizes_scheme_and_host():
    """Verify lowercase scheme, host, and port stripping."""
    res = canonicalize_url("HTTP://Example.COM:80/path/to/page")
    assert res.url == "http://example.com/path/to/page"
    assert res.registrable_domain == "example.com"
    assert res.url_hash == hashlib.sha256(b"http://example.com/path/to/page").digest()


def test_canonicalize_url_preserves_non_default_port():
    """Verify non-default ports are kept in canonical URL."""
    res = canonicalize_url("https://example.com:8443/test")
    assert res.url == "https://example.com:8443/test"
    assert res.registrable_domain == "example.com"


def test_canonicalize_url_normalizes_dot_segments():
    """Verify path normalization resolves '.' and '..' segments."""
    res = canonicalize_url("http://example.com/a/b/../c/./d")
    assert res.url == "http://example.com/a/c/d"


def test_canonicalize_url_strips_fragment():
    """Verify fragment identifiers are discarded."""
    res = canonicalize_url("http://example.com/page#section-3")
    assert res.url == "http://example.com/page"


def test_canonicalize_url_sorts_query_parameters():
    """Verify query parameters are sorted stably for hashing."""
    res1 = canonicalize_url("http://example.com/?z=1&a=2&m=hello")
    res2 = canonicalize_url("http://example.com/?a=2&m=hello&z=1")
    assert res1.url == "http://example.com/?a=2&m=hello&z=1"
    assert res1.url == res2.url
    assert res1.url_hash == res2.url_hash


def test_canonicalize_url_registrable_domain_etld_plus_one():
    """Verify registrable domain correctly extracts eTLD+1 via tldextract."""
    res1 = canonicalize_url("http://www.example.co.uk/index")
    res2 = canonicalize_url("http://shop.example.co.uk/items")
    assert res1.registrable_domain == "example.co.uk"
    assert res2.registrable_domain == "example.co.uk"


def test_canonicalize_url_idna_encoding():
    """Verify non-ASCII internationalized domain names are encoded using IDNA."""
    # 'münchen.de' -> 'xn--mnchen-3ya.de'
    res = canonicalize_url("http://münchen.de/")
    assert "xn--mnchen-3ya.de" in res.url
    assert "xn--mnchen-3ya.de" in res.registrable_domain


def test_canonicalize_url_rejects_unsupported_schemes():
    """Verify non-http/https schemes are rejected."""
    with pytest.raises(ValueError, match="Unsupported URL scheme"):
        canonicalize_url("ftp://example.com/resource")

    with pytest.raises(ValueError, match="Unsupported URL scheme"):
        canonicalize_url("file:///etc/passwd")

    with pytest.raises(ValueError, match="Unsupported URL scheme"):
        canonicalize_url("javascript:alert(1)")


def test_canonicalize_url_rejects_empty():
    """Verify empty string or whitespace is rejected."""
    with pytest.raises(ValueError, match="URL cannot be empty"):
        canonicalize_url("   ")
