"""Self-contained web content reader (Round 4).

Three layers, orchestrated by :func:`extract_web_content`:

* **Layer 2 — Sidecar** (``localhost:7890/extract``): preferred when alive.
  Zero extra dependencies (stdlib ``urllib``), so it works even without the
  ``[reader]`` extra installed. Useful for special platforms (YouTube
  subtitles, Bilibili, WeChat, ...).
* **Layer 1 — Built-in** (``httpx`` + ``trafilatura``): self-contained
  extraction that covers standard web pages once ``pip install
  "piia-engram[reader]"`` has run. The optional deps are imported lazily so
  the MCP server still starts without them.
* **Error**: when neither layer can produce content, the returned
  :class:`WebContent` carries an actionable ``error`` (e.g. the install hint).

The public functions never raise on network/parse failure — every outcome is a
:class:`WebContent`. ``WebContent`` is an internal structure; the MCP tool
``read_web_content`` formats it into a ``str`` (the schema snapshot pins the
tool's return type to ``str``).
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

# --- configuration ----------------------------------------------------------

SIDECAR_URL = "http://localhost:7890/extract"
SIDECAR_HOST = "localhost"
SIDECAR_PORT = 7890
_SIDECAR_TTL = 300.0  # seconds the health probe result is cached
_DEFAULT_TIMEOUT = 30
_EXCERPT_CHARS = 200
_USER_AGENT = (
    "Mozilla/5.0 (compatible; PiiaEngramReader/1.0; "
    "+https://github.com/Patdolitse/piia-engram)"
)

# Broad CJK range (Han + kana + Hangul) for word counting; Han-only for the
# "zh" language decision so Japanese/Korean text isn't misreported as Chinese.
_CJK_RE = re.compile(r"[一-鿿぀-ヿ가-힯]")
_HAN_RE = re.compile(r"[一-鿿]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9]+")


# --- structured result ------------------------------------------------------


@dataclass
class WebContent:
    """Structured extraction result. ``error`` is ``None`` on success."""

    url: str
    title: str = ""
    content: str = ""
    excerpt: str = ""
    word_count: int = 0
    language: str = "unknown"  # "zh" | "en" | "unknown"
    source: str = "builtin"  # which layer produced it: "builtin" | "sidecar"
    fetched_at: str = ""
    metadata: dict = field(default_factory=dict)
    error: str | None = None


# --- small helpers ----------------------------------------------------------


def _now() -> float:
    """Monotonic clock for the health-cache TTL (patched in tests)."""
    return time.monotonic()


def _now_iso() -> str:
    """Wall-clock UTC timestamp for ``WebContent.fetched_at``."""
    return datetime.now(timezone.utc).isoformat()


def _is_http_url(url: str) -> bool:
    return isinstance(url, str) and url.lower().startswith(("http://", "https://"))


def _make_excerpt(text: str, limit: int = _EXCERPT_CHARS) -> str:
    collapsed = " ".join(text.split())
    return collapsed[:limit]


def _detect_language(text: str) -> str:
    """Naive Han-vs-ASCII heuristic. Returns ``zh`` / ``en`` / ``unknown``.

    Only Han ideographs count toward ``zh`` — kana/Hangul-heavy text falls
    through to ``unknown`` rather than being misreported as Chinese.
    """
    if not text:
        return "unknown"
    han = len(_HAN_RE.findall(text))
    ascii_alpha = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if han == 0 and ascii_alpha == 0:
        return "unknown"
    return "zh" if han >= ascii_alpha else "en"


def _count_words(text: str) -> int:
    """Word count that works for both space-delimited and CJK scripts."""
    return len(_CJK_RE.findall(text)) + len(_ASCII_WORD_RE.findall(text))


def install_hint() -> str:
    """Actionable message when neither sidecar nor built-in reader is usable."""
    return (
        '未能读取：本地边车未运行，且内置 reader 依赖未安装。\n'
        '安装内置 reader：pip install "piia-engram[reader]"\n'
        '或启动边车后重试。\n'
        "Cannot read: no local sidecar running and the built-in reader deps "
        'are missing. Install with: pip install "piia-engram[reader]" '
        "(or start the sidecar and retry)."
    )


# --- sidecar health (lazy TTL probe; no /health endpoint needed) ------------

_sidecar_state: dict[str, object] = {"alive": None, "checked_at": 0.0}


def _reset_sidecar_state() -> None:
    """Clear the cached health result (used by tests and after hard failures)."""
    _sidecar_state["alive"] = None
    _sidecar_state["checked_at"] = 0.0


def _probe_sidecar(
    *, host: str = SIDECAR_HOST, port: int = SIDECAR_PORT, timeout: float = 2.0
) -> bool:
    """Liveness probe: can we open a TCP connection to the sidecar port?

    The sidecar exposes no ``/health`` route, so a socket connect is the
    cheapest reliable "something is listening" signal.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def sidecar_available(*, ttl: float = _SIDECAR_TTL) -> bool:
    """Return whether the sidecar is reachable, caching the result for ``ttl``."""
    alive = _sidecar_state["alive"]
    checked_at = _sidecar_state["checked_at"]
    if alive is not None and (_now() - float(checked_at)) < ttl:
        return bool(alive)
    result = _probe_sidecar()
    _sidecar_state["alive"] = result
    _sidecar_state["checked_at"] = _now()
    return result


def _mark_sidecar_dead() -> None:
    _sidecar_state["alive"] = False
    _sidecar_state["checked_at"] = _now()


# --- Layer 1: built-in reader (lazy optional deps) --------------------------


async def _fetch_html(url: str, *, timeout: int) -> str:
    """GET ``url`` and return the response body. Lazy-imports ``httpx``."""
    import httpx  # lazy — keeps MCP server importable without the [reader] extra

    headers = {"User-Agent": _USER_AGENT}
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=timeout, headers=headers
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


def _parse_html(html: str, url: str) -> dict | None:
    """Extract main text + metadata via ``trafilatura``. Lazy-imported.

    Returns ``{"title", "text", "metadata"}`` or ``None`` if nothing usable.
    """
    import trafilatura  # lazy — optional [reader] dependency

    text = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=True,
        favor_recall=True,
    )
    if not text:
        return None

    title = ""
    metadata: dict = {}
    try:
        meta = trafilatura.extract_metadata(html)
    except Exception:
        meta = None
    if meta is not None:
        title = getattr(meta, "title", "") or ""
        for key in ("author", "date", "sitename", "description", "categories", "tags"):
            value = getattr(meta, key, None)
            if value:
                metadata[key] = value
    return {"title": title, "text": text, "metadata": metadata}


async def extract_builtin(url: str, *, timeout: int = _DEFAULT_TIMEOUT) -> WebContent:
    """Layer 1: self-contained extraction. Never raises — errors land in ``error``."""
    fetched_at = _now_iso()

    if not _is_http_url(url):
        return WebContent(
            url=url,
            source="builtin",
            fetched_at=fetched_at,
            error="仅支持 http/https 链接。/ Only http(s) URLs are supported.",
        )

    try:
        html = await _fetch_html(url, timeout=timeout)
    except ImportError:
        return WebContent(url=url, source="builtin", fetched_at=fetched_at, error=install_hint())
    except Exception as exc:  # network / HTTP status / DNS — keep it graceful
        return WebContent(
            url=url,
            source="builtin",
            fetched_at=fetched_at,
            error=f"抓取失败 / fetch failed: {exc}",
        )

    try:
        parsed = _parse_html(html, url)
    except ImportError:
        return WebContent(url=url, source="builtin", fetched_at=fetched_at, error=install_hint())
    except Exception as exc:
        return WebContent(
            url=url,
            source="builtin",
            fetched_at=fetched_at,
            error=f"解析失败 / parse failed: {exc}",
        )

    text = (parsed or {}).get("text", "") if parsed else ""
    text = (text or "").strip()
    if not text:
        return WebContent(
            url=url,
            source="builtin",
            fetched_at=fetched_at,
            error="未能提取到正文内容。请确认链接可访问。/ Could not extract content.",
        )

    title = (parsed.get("title") or "").strip()
    metadata = dict(parsed.get("metadata") or {})
    return WebContent(
        url=url,
        title=title,
        content=text,
        excerpt=_make_excerpt(text),
        word_count=_count_words(text),
        language=_detect_language(text),
        source="builtin",
        fetched_at=fetched_at,
        metadata=metadata,
    )


# --- Layer 2: sidecar adapter (stdlib only) ---------------------------------


async def extract_sidecar(url: str, *, timeout: int = _DEFAULT_TIMEOUT) -> WebContent:
    """Layer 2: POST to the local sidecar. Never raises — errors land in ``error``.

    Hard failures (connection/refused) mark the health cache dead so the
    orchestrator stops preferring the sidecar until the TTL refreshes.
    """
    import urllib.error
    import urllib.request

    fetched_at = _now_iso()
    payload = json.dumps({"url": url}).encode("utf-8")
    request = urllib.request.Request(
        SIDECAR_URL, data=payload, headers={"Content-Type": "application/json"}
    )

    def _blocking_post() -> str:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.read().decode("utf-8")

    try:
        raw = await asyncio.to_thread(_blocking_post)
    except urllib.error.URLError as exc:
        _mark_sidecar_dead()
        return WebContent(
            url=url,
            source="sidecar",
            fetched_at=fetched_at,
            error=f"边车未运行 / sidecar unavailable: {exc}",
        )
    except Exception as exc:
        _mark_sidecar_dead()
        return WebContent(
            url=url,
            source="sidecar",
            fetched_at=fetched_at,
            error=f"边车请求失败 / sidecar request failed: {exc}",
        )

    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        _mark_sidecar_dead()
        return WebContent(
            url=url,
            source="sidecar",
            fetched_at=fetched_at,
            error=f"边车返回非法 JSON / malformed sidecar JSON: {exc}",
        )

    if not isinstance(data, dict):
        _mark_sidecar_dead()
        return WebContent(
            url=url,
            source="sidecar",
            fetched_at=fetched_at,
            error="边车返回非对象 JSON / sidecar JSON was not an object",
        )

    if data.get("error"):
        return WebContent(
            url=url, source="sidecar", fetched_at=fetched_at, error=str(data["error"])
        )

    # Defensive: the sidecar response is untrusted. Coerce each field by type
    # instead of assuming shape, so anomalous-but-legal JSON falls back to the
    # built-in reader rather than raising.
    content_raw = data.get("content")
    content = content_raw.strip() if isinstance(content_raw, str) else ""
    if not content:
        return WebContent(
            url=url,
            source="sidecar",
            fetched_at=fetched_at,
            error="边车未返回内容 / sidecar returned no content",
        )

    meta_raw = data.get("metadata")
    metadata = dict(meta_raw) if isinstance(meta_raw, dict) else {}
    origin = data.get("source")
    if isinstance(origin, str) and origin:
        metadata.setdefault("origin", origin)
    title_raw = data.get("title")
    title = title_raw.strip() if isinstance(title_raw, str) else ""
    return WebContent(
        url=url,
        title=title,
        content=content,
        excerpt=_make_excerpt(content),
        word_count=_count_words(content),
        language=_detect_language(content),
        source="sidecar",
        fetched_at=fetched_at,
        metadata=metadata,
    )


# --- orchestrator -----------------------------------------------------------


async def extract_web_content(
    url: str, *, timeout: int = _DEFAULT_TIMEOUT, prefer_sidecar: bool = True
) -> WebContent:
    """Sidecar (when alive) → built-in → error. Returns a :class:`WebContent`."""
    if prefer_sidecar and sidecar_available():
        try:
            result = await extract_sidecar(url, timeout=timeout)
        except Exception:
            # extract_sidecar is meant to never raise; guard anyway so a future
            # bug there can never block the built-in fallback.
            _mark_sidecar_dead()
            result = None
        if result is not None and result.error is None:
            return result
        # Sidecar unavailable or errored → fall through to the built-in reader.
    return await extract_builtin(url, timeout=timeout)
