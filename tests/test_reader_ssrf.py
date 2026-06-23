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


class TestDnsResolutionCheck:
    """1-5: _is_private_url must resolve DNS and block domains pointing to
    private IPs (DNS rebinding defense)."""

    def test_domain_resolving_to_private_ip_is_blocked(self, monkeypatch):
        """A public-looking domain whose A record points to 127.0.0.1 must be
        blocked (DNS rebinding attack vector)."""
        import socket
        original = socket.getaddrinfo

        def fake_getaddrinfo(host, port, *args, **kwargs):
            if host == "evil.example.com":
                return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 80))]
            return original(host, port, *args, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        assert _is_private_url("http://evil.example.com/steal"), (
            "Domain resolving to 127.0.0.1 was not blocked"
        )

    def test_domain_resolving_to_rfc1918_is_blocked(self, monkeypatch):
        """A domain resolving to 10.x.x.x must be blocked."""
        import socket
        original = socket.getaddrinfo

        def fake_getaddrinfo(host, port, *args, **kwargs):
            if host == "internal.attacker.com":
                return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.5", 80))]
            return original(host, port, *args, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        assert _is_private_url("http://internal.attacker.com/data"), (
            "Domain resolving to 10.0.0.5 was not blocked"
        )

    def test_domain_resolving_to_metadata_ip_is_blocked(self, monkeypatch):
        """A domain resolving to 169.254.169.254 must be blocked."""
        import socket
        original = socket.getaddrinfo

        def fake_getaddrinfo(host, port, *args, **kwargs):
            if host == "metadata-proxy.evil.com":
                return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.169.254", 80))]
            return original(host, port, *args, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        assert _is_private_url("http://metadata-proxy.evil.com/latest/"), (
            "Domain resolving to cloud metadata IP was not blocked"
        )

    def test_dns_failure_is_treated_as_blocked(self, monkeypatch):
        """If DNS resolution fails, the URL should be treated as blocked
        (fail-closed)."""
        import socket

        def failing_getaddrinfo(host, port, *args, **kwargs):
            raise socket.gaierror("DNS resolution failed")

        monkeypatch.setattr(socket, "getaddrinfo", failing_getaddrinfo)
        assert _is_private_url("http://nonexistent.invalid/path"), (
            "DNS failure should be treated as blocked (fail-closed)"
        )


class TestRedirectHopValidation:
    """1-5: _fetch_html must validate the final URL after redirects."""

    def test_redirect_to_private_ip_is_blocked(self, monkeypatch):
        """A redirect from public URL to a private IP must be caught by
        checking the response's effective URL after follow_redirects."""
        import asyncio
        import socket
        import httpx
        from piia_engram import reader

        # Make DNS resolution pass for the public domain
        original_getaddrinfo = socket.getaddrinfo

        def patched_getaddrinfo(host, port, *args, **kwargs):
            if host in ("public.example.com", "127.0.0.1"):
                if host == "public.example.com":
                    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 80))]
                return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 80))]
            return original_getaddrinfo(host, port, *args, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", patched_getaddrinfo)

        class FakeResponse:
            status_code = 200
            text = "<html><body>secret</body></html>"
            url = httpx.URL("http://127.0.0.1:8080/admin")

            def raise_for_status(self):
                pass

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def get(self, url, **kwargs):
                return FakeResponse()

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: FakeClient())

        result = asyncio.run(
            reader.extract_web_content("http://public.example.com/page")
        )
        assert result.error, "Redirect to private IP was not blocked"
        assert "redirect" in result.error.lower() or "private" in result.error.lower(), (
            f"Error not SSRF-related: {result.error}"
        )
