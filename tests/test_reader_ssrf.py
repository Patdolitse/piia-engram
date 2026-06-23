"""S3-2: read_web_content must block private/loopback/metadata URLs.

Bug: reader.py accepts any URL with http(s) scheme — no blocklist for
private IPs, loopback, or cloud metadata endpoints.
"""

from __future__ import annotations

import pytest

from piia_engram.reader import _is_private_url


class TestPrivateUrlBlocklist:
    @pytest.mark.parametrize("url", [
        "http://127.0.0.1/admin",
        "http://127.0.0.1:8080/api",
        "http://localhost/secret",
        "http://localhost:3000",
        "http://[::1]/admin",
        "http://0.0.0.0/",
    ])
    def test_loopback_blocked(self, url):
        assert _is_private_url(url), f"Loopback URL not blocked: {url}"

    @pytest.mark.parametrize("url", [
        "http://10.0.0.1/internal",
        "http://10.255.255.255/",
        "http://172.16.0.1/secret",
        "http://172.31.255.255/",
        "http://192.168.1.1/router",
        "http://192.168.0.100:8080/",
    ])
    def test_private_rfc1918_blocked(self, url):
        assert _is_private_url(url), f"RFC1918 URL not blocked: {url}"

    @pytest.mark.parametrize("url", [
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.169.254/metadata/instance",
        "http://metadata.google.internal/",
    ])
    def test_cloud_metadata_blocked(self, url):
        assert _is_private_url(url), f"Cloud metadata URL not blocked: {url}"

    @pytest.mark.parametrize("url", [
        "https://example.com/page",
        "https://github.com/repo",
        "http://1.2.3.4/public",
        "https://docs.python.org/3/",
    ])
    def test_public_urls_allowed(self, url):
        assert not _is_private_url(url), f"Public URL wrongly blocked: {url}"
