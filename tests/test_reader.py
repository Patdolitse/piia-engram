"""Round 4 — Self-Contained Reader tests (TDD RED first).

Locks the contract for ``piia_engram.reader`` and the ``read_web_content`` MCP
tool:

- Layer 1 (built-in): httpx + trafilatura, lazy-imported, returns a structured
  ``WebContent`` (never raises on network/parse failure).
- Layer 2 (sidecar): localhost:7890 preferred when alive, with a lazy TTL
  health cache and graceful fallback to Layer 1.
- MCP contract: ``read_web_content`` keeps returning ``str`` (schema snapshot
  pins ``returns: "str"``), the optional deps stay lazy, and long bodies are
  length-bounded.

Behavioural seams (``_fetch_html`` / ``_parse_html`` / ``_probe_sidecar`` /
``_now``) are monkeypatched so the orchestration and assembly logic is covered
without real network access or the ``[reader]`` extra installed.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import re
import sys
from pathlib import Path

import pytest

from piia_engram import mcp_server
from piia_engram import reader


def _run(coro):
    """Run an async coroutine synchronously in tests."""
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_sidecar_state():
    """Each test starts with a cleared sidecar health cache."""
    reader._reset_sidecar_state()
    yield
    reader._reset_sidecar_state()


# ---------------------------------------------------------------------------
# Layer 1 — Built-in reader (assembly + failure handling)
# ---------------------------------------------------------------------------


class TestBuiltinReader:
    def test_extract_structured_fields(self, monkeypatch: pytest.MonkeyPatch):
        """Successful extraction fills every WebContent field."""

        async def fake_fetch(url, *, timeout):
            return "<html><body>...</body></html>"

        def fake_parse(html, url):
            return {
                "title": "Hello World",
                "text": "the quick brown fox " * 30,
                "metadata": {"author": "Ada", "site_name": "Example"},
            }

        monkeypatch.setattr(reader, "_fetch_html", fake_fetch)
        monkeypatch.setattr(reader, "_parse_html", fake_parse)

        wc = _run(reader.extract_builtin("https://example.com/post"))

        assert isinstance(wc, reader.WebContent)
        assert wc.error is None
        assert wc.url == "https://example.com/post"
        assert wc.title == "Hello World"
        assert wc.content.strip()
        assert wc.excerpt and len(wc.excerpt) <= 200
        assert wc.word_count > 0
        assert wc.language in {"zh", "en", "unknown"}
        assert wc.source == "builtin"
        assert wc.fetched_at  # ISO timestamp present
        assert wc.metadata.get("author") == "Ada"

    def test_extract_unreachable_url(self, monkeypatch: pytest.MonkeyPatch):
        """Network failure becomes an error field, not a raised exception."""

        async def fake_fetch(url, *, timeout):
            raise OSError("name resolution failed")

        monkeypatch.setattr(reader, "_fetch_html", fake_fetch)

        wc = _run(reader.extract_builtin("https://nope.invalid"))

        assert wc.error is not None
        assert wc.content == ""
        assert wc.source == "builtin"

    def test_extract_respects_timeout(self, monkeypatch: pytest.MonkeyPatch):
        """A custom timeout is forwarded to the fetch layer."""
        captured: dict[str, int] = {}

        async def fake_fetch(url, *, timeout):
            captured["timeout"] = timeout
            return "<html></html>"

        def fake_parse(html, url):
            return {"title": "t", "text": "body text here", "metadata": {}}

        monkeypatch.setattr(reader, "_fetch_html", fake_fetch)
        monkeypatch.setattr(reader, "_parse_html", fake_parse)

        _run(reader.extract_builtin("https://example.com", timeout=7))

        assert captured["timeout"] == 7

    def test_extract_empty_result(self, monkeypatch: pytest.MonkeyPatch):
        """trafilatura returning nothing yields an actionable error message."""

        async def fake_fetch(url, *, timeout):
            return "<html><body></body></html>"

        def fake_parse(html, url):
            return None

        monkeypatch.setattr(reader, "_fetch_html", fake_fetch)
        monkeypatch.setattr(reader, "_parse_html", fake_parse)

        wc = _run(reader.extract_builtin("https://example.com"))

        assert wc.error is not None
        assert wc.content == ""
        # Message should point the user at a next step, not be a bare trace.
        assert any(tok in wc.error for tok in ("提取", "extract", "内容", "content"))

    def test_extract_unsupported_url_scheme(self):
        """Non-http(s) schemes are rejected before any fetch."""
        wc = _run(reader.extract_builtin("ftp://files.example.com/a.txt"))

        assert wc.error is not None
        assert "http" in wc.error.lower()

    def test_builtin_install_hint_when_httpx_absent(self, monkeypatch: pytest.MonkeyPatch):
        """Without the [reader] extra, the built-in path surfaces an install hint."""
        monkeypatch.setitem(sys.modules, "httpx", None)

        wc = _run(reader.extract_builtin("https://example.com"))

        assert wc.error is not None
        assert "pip install" in wc.error
        assert "reader" in wc.error


# ---------------------------------------------------------------------------
# Layer 2 — Sidecar routing + health cache
# ---------------------------------------------------------------------------


class TestSidecarRouting:
    def test_sidecar_preferred_when_available(self, monkeypatch: pytest.MonkeyPatch):
        """When the sidecar is alive and succeeds, builtin is never invoked."""
        monkeypatch.setattr(reader, "sidecar_available", lambda **k: True)

        async def fake_sidecar(url, *, timeout):
            return reader.WebContent(
                url=url, content="from sidecar", source="sidecar", fetched_at="t"
            )

        builtin_called = {"hit": False}

        async def fake_builtin(url, *, timeout):
            builtin_called["hit"] = True
            return reader.WebContent(
                url=url, content="from builtin", source="builtin", fetched_at="t"
            )

        monkeypatch.setattr(reader, "extract_sidecar", fake_sidecar)
        monkeypatch.setattr(reader, "extract_builtin", fake_builtin)

        wc = _run(reader.extract_web_content("https://example.com"))

        assert wc.source == "sidecar"
        assert builtin_called["hit"] is False

    def test_sidecar_down_falls_back_to_builtin(self, monkeypatch: pytest.MonkeyPatch):
        """When the sidecar is down, the sidecar path is skipped entirely."""
        monkeypatch.setattr(reader, "sidecar_available", lambda **k: False)

        async def boom(url, *, timeout):
            raise AssertionError("sidecar called while marked down")

        async def fake_builtin(url, *, timeout):
            return reader.WebContent(
                url=url, content="from builtin", source="builtin", fetched_at="t"
            )

        monkeypatch.setattr(reader, "extract_sidecar", boom)
        monkeypatch.setattr(reader, "extract_builtin", fake_builtin)

        wc = _run(reader.extract_web_content("https://example.com"))

        assert wc.source == "builtin"

    def test_sidecar_malformed_json_falls_back(self, monkeypatch: pytest.MonkeyPatch):
        """A sidecar that returns an error result triggers builtin fallback."""
        monkeypatch.setattr(reader, "sidecar_available", lambda **k: True)

        async def fake_sidecar(url, *, timeout):
            return reader.WebContent(
                url=url, source="sidecar", fetched_at="t",
                error="malformed JSON from sidecar",
            )

        async def fake_builtin(url, *, timeout):
            return reader.WebContent(
                url=url, content="from builtin", source="builtin", fetched_at="t"
            )

        monkeypatch.setattr(reader, "extract_sidecar", fake_sidecar)
        monkeypatch.setattr(reader, "extract_builtin", fake_builtin)

        wc = _run(reader.extract_web_content("https://example.com"))

        assert wc.error is None
        assert wc.source == "builtin"

    def test_orchestrator_survives_sidecar_raise(self, monkeypatch: pytest.MonkeyPatch):
        """A crashing sidecar must never block the builtin fallback."""
        monkeypatch.setattr(reader, "sidecar_available", lambda **k: True)

        async def boom(url, *, timeout):
            raise RuntimeError("sidecar blew up")

        async def fake_builtin(url, *, timeout):
            return reader.WebContent(
                url=url, content="from builtin", source="builtin", fetched_at="t"
            )

        monkeypatch.setattr(reader, "extract_sidecar", boom)
        monkeypatch.setattr(reader, "extract_builtin", fake_builtin)

        wc = _run(reader.extract_web_content("https://example.com"))

        assert wc.source == "builtin"

    def test_sidecar_health_cache_expires(self, monkeypatch: pytest.MonkeyPatch):
        """The health probe is cached within TTL and re-run once it expires."""
        clock = {"t": 1000.0}
        monkeypatch.setattr(reader, "_now", lambda: clock["t"])

        probes = {"n": 0}

        def fake_probe(**kwargs):
            probes["n"] += 1
            return True

        monkeypatch.setattr(reader, "_probe_sidecar", fake_probe)

        # First call probes.
        assert reader.sidecar_available(ttl=300) is True
        assert probes["n"] == 1

        # Within TTL: cached, no new probe.
        clock["t"] = 1200.0
        reader.sidecar_available(ttl=300)
        assert probes["n"] == 1

        # Past TTL: re-probe.
        clock["t"] = 1400.0
        reader.sidecar_available(ttl=300)
        assert probes["n"] == 2


# ---------------------------------------------------------------------------
# MCP-layer contract guards
# ---------------------------------------------------------------------------


class TestMcpContract:
    def test_mcp_returns_str_not_dict(self, monkeypatch: pytest.MonkeyPatch):
        """read_web_content must keep returning str (schema snapshot pins it)."""

        async def fake_extract(url, *, timeout=30):
            return reader.WebContent(
                url=url, title="Title", content="body text",
                excerpt="body text", word_count=2, language="en",
                source="builtin", fetched_at="t",
            )

        monkeypatch.setattr(reader, "extract_web_content", fake_extract)

        result = _run(mcp_server.read_web_content(url="https://example.com"))

        assert isinstance(result, str)
        assert "body text" in result

    def test_imports_without_reader_extra(self, monkeypatch: pytest.MonkeyPatch):
        """No top-level httpx/trafilatura import in reader.py or the admin tool.

        With both extras forced absent, reloading reader must not raise — proving
        the optional deps are lazy-loaded inside functions (Codex MUST-FIX #2).
        """
        monkeypatch.setitem(sys.modules, "httpx", None)
        monkeypatch.setitem(sys.modules, "trafilatura", None)

        importlib.reload(reader)  # must not raise ImportError
        assert reader.WebContent is not None

        def _has_toplevel_import(src: str, mod: str) -> bool:
            return re.search(rf"(?m)^(?:import {mod}\b|from {mod}\b)", src) is not None

        src_root = Path(reader.__file__).parent
        reader_src = (src_root / "reader.py").read_text(encoding="utf-8")
        admin_src = (src_root / "mcp_tools_admin.py").read_text(encoding="utf-8")
        for mod in ("httpx", "trafilatura"):
            assert not _has_toplevel_import(reader_src, mod), f"reader.py top-imports {mod}"
            assert not _has_toplevel_import(admin_src, mod), f"admin top-imports {mod}"

    def test_mcp_no_reader_gives_install_hint(self, monkeypatch: pytest.MonkeyPatch):
        """No sidecar + no builtin extra surfaces a pip install hint."""

        async def fake_extract(url, *, timeout=30):
            return reader.WebContent(
                url=url, source="none", fetched_at="t",
                error='内置 reader 未安装且边车未运行。pip install "piia-engram[reader]"',
            )

        monkeypatch.setattr(reader, "extract_web_content", fake_extract)

        result = _run(mcp_server.read_web_content(url="https://example.com"))

        assert isinstance(result, str)
        assert "pip install" in result
        assert "reader" in result

    def test_mcp_output_length_bounded(self, monkeypatch: pytest.MonkeyPatch):
        """A very long body is truncated in the MCP string output."""
        big = "字" * 50000

        async def fake_extract(url, *, timeout=30):
            return reader.WebContent(
                url=url, title="T", content=big, excerpt=big[:200],
                word_count=50000, language="zh", source="builtin", fetched_at="t",
            )

        monkeypatch.setattr(reader, "extract_web_content", fake_extract)

        result = _run(mcp_server.read_web_content(url="https://example.com"))

        assert len(result) < 20000  # well below the raw 50000 chars
        assert ("truncat" in result.lower()) or ("截断" in result)

    def test_mcp_error_output_bounded(self, monkeypatch: pytest.MonkeyPatch):
        """A pathologically long error string is also clipped (not just body)."""
        big_error = "X" * 50000

        async def fake_extract(url, *, timeout=30):
            return reader.WebContent(
                url=url, source="sidecar", fetched_at="t", error=big_error
            )

        monkeypatch.setattr(reader, "extract_web_content", fake_extract)

        result = _run(mcp_server.read_web_content(url="https://example.com"))

        assert len(result) < 20000


# ---------------------------------------------------------------------------
# Language heuristic
# ---------------------------------------------------------------------------


class TestLanguageHeuristic:
    def test_detect_language_distinguishes_scripts(self):
        assert reader._detect_language("这是一段中文内容") == "zh"
        assert reader._detect_language("this is english text") == "en"
        # Japanese kana must not be misreported as Chinese.
        assert reader._detect_language("これはにほんごのテキストです") != "zh"


# ---------------------------------------------------------------------------
# Sidecar adapter — JSON handling (replaces the old urllib mock tests that
# were bound to read_web_content's implementation detail; Codex SHOULD-FIX #5)
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestSidecarAdapter:
    def test_sidecar_success(self, monkeypatch: pytest.MonkeyPatch):
        import urllib.request

        payload = json.dumps(
            {"content": "Hello World", "source": "youtube", "error": None}
        ).encode("utf-8")
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda *a, **k: _FakeResp(payload)
        )

        wc = _run(reader.extract_sidecar("https://youtu.be/x"))

        assert wc.error is None
        assert "Hello World" in wc.content
        assert wc.source == "sidecar"

    def test_sidecar_error_field(self, monkeypatch: pytest.MonkeyPatch):
        import urllib.request

        payload = json.dumps({"error": "page not found", "content": ""}).encode("utf-8")
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda *a, **k: _FakeResp(payload)
        )

        wc = _run(reader.extract_sidecar("https://example.com"))

        assert wc.error is not None
        assert "page not found" in wc.error

    def test_sidecar_empty_content(self, monkeypatch: pytest.MonkeyPatch):
        import urllib.request

        payload = json.dumps({"content": "", "source": "test"}).encode("utf-8")
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda *a, **k: _FakeResp(payload)
        )

        wc = _run(reader.extract_sidecar("https://example.com"))

        assert wc.error is not None

    def test_sidecar_malformed_json(self, monkeypatch: pytest.MonkeyPatch):
        import urllib.request

        monkeypatch.setattr(
            urllib.request, "urlopen", lambda *a, **k: _FakeResp(b"<not json>")
        )

        wc = _run(reader.extract_sidecar("https://example.com"))

        assert wc.error is not None

    def test_sidecar_nonstring_fields_no_raise(self, monkeypatch: pytest.MonkeyPatch):
        """Legal JSON with wrong field types must not raise (graceful fallback)."""
        import urllib.request

        payload = json.dumps(
            {"content": ["not", "a", "string"], "metadata": "nope", "title": 7}
        ).encode("utf-8")
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda *a, **k: _FakeResp(payload)
        )

        wc = _run(reader.extract_sidecar("https://example.com"))  # must not raise

        assert wc.error is not None

    def test_sidecar_url_error(self, monkeypatch: pytest.MonkeyPatch):
        import urllib.error
        import urllib.request

        def raise_url_error(*a, **k):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", raise_url_error)

        wc = _run(reader.extract_sidecar("https://example.com"))

        assert wc.error is not None

    def test_sidecar_hard_failure_marks_dead(self, monkeypatch: pytest.MonkeyPatch):
        """A hard connection failure flips the cached health to dead."""
        import urllib.error
        import urllib.request

        def raise_url_error(*a, **k):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", raise_url_error)

        _run(reader.extract_sidecar("https://example.com"))

        assert reader._sidecar_state["alive"] is False

    def test_sidecar_generic_exception(self, monkeypatch: pytest.MonkeyPatch):
        import urllib.request

        def raise_generic(*a, **k):
            raise ValueError("unexpected")

        monkeypatch.setattr(urllib.request, "urlopen", raise_generic)

        wc = _run(reader.extract_sidecar("https://example.com"))

        assert wc.error is not None


# ---------------------------------------------------------------------------
# Real trafilatura parse from a local HTML string (no network). Runs only when
# the [reader] extra is installed; skipped otherwise (Codex NICE-TO-HAVE #1).
# ---------------------------------------------------------------------------


class TestBuiltinParseLive:
    def test_parse_local_html_extracts_body_and_strips_chrome(self):
        pytest.importorskip("trafilatura")

        html = """
        <html><head><title>Sample Title</title></head>
        <body>
          <nav>menu noise that should be stripped</nav>
          <article>
            <h1>Sample Title</h1>
            <p>This is the first paragraph of real article body text.</p>
            <p>Here is a second paragraph with more substantive content.</p>
          </article>
          <footer>copyright junk</footer>
        </body></html>
        """

        parsed = reader._parse_html(html, "https://example.com/sample")

        assert parsed is not None
        assert isinstance(parsed["title"], str)
        assert "first paragraph" in parsed["text"]
        assert "second paragraph" in parsed["text"]
        assert "menu noise" not in parsed["text"]
