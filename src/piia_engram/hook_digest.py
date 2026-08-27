"""Privacy-guarded transcript digest for the session-end hook (B-F1).

The stop hook's extraction input used to be metadata-only, which the
conservative quality gate correctly rejected (zero staged items). This module
derives a sanitized digest of ASSISTANT text so the gate gets signal — under
a fail-closed privacy contract reviewed adversarially (design v2):

- input face: assistant text blocks only (user text and tool input/output are
  never collected);
- block-level stateful noise filtering before any line handling;
- normalization (NFKC + zero-width folding) BEFORE detection, detection
  BEFORE truncation;
- composed redaction (existing audited scrubbers + the missing shapes here —
  no fourth drifting copy of existing patterns);
- hard budget measured on the final assembled digest (messages, chars, and
  UTF-8 bytes);
- an OUTPUT guard applied to extraction candidates from this path before
  anything is persisted: a hit drops the whole candidate, counted only.

Pure functions, stdlib only, no store IO.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

from .continuity_digest import _ABS_POSIX_PATH_RE, _ABS_WIN_PATH_RE, _AWS_KEY_RE, _SK_KEY_RE
from .export_redaction import redact_export_text

# ── frozen budget constants ────────────────────────────────────────────────
MAX_MESSAGES = 24
MAX_LINE_CHARS = 320
MAX_TOTAL_CHARS = 6_000
MAX_TOTAL_BYTES = 8_000
# robustness: skip absurd text blocks before any processing
MAX_BLOCK_ORIGINAL_CHARS = 100_000

PREFERENCE_KEY = "hook_content_digest"
PREFERENCE_KEY_V2 = "hook_content_digest_v2"
CAPTURE_ORIGIN = "hook_content_digest"
# 4.18 activation gate: the digest path is re-enabled under a NEW versioned
# preference key. The master gate is ON; the sole activation formula is
# `hook_content_digest_v2 is literal True`. The old boolean key
# (`hook_content_digest`) is migrated away at read time and never honored,
# so a persisted legacy `true` (from 4.17.1 or hand-edited JSON) cannot
# reactivate the path, nor can it survive an upgrade-then-downgrade cycle.
RUNTIME_ENABLED = True

# shapes the existing scrubbers intentionally leave to other layers, plus
# generic secret-bearing forms the digest must not carry. Composed AFTER the
# audited scrubbers so this list stays minimal.
_BEARER_RE = re.compile(
    r"(?i)\b(?:bearer|authorization|token|secret)\s*[:=]\s*(?:bearer\s+)?[A-Za-z0-9._~+/=-]+"
)
_CONN_URI_RE = re.compile(
    r"(?i)\b[a-z][a-z0-9+.-]{1,20}://[^\s/:@]+:[^\s/@]+@[^\s]+"
)
_URL_SECRET_RE = re.compile(
    r"(?i)[?&](?:api_?key|apikey|token|secret|password|passwd|pwd|sig|signature|access_?token)=[^&\s]+"
)
_COOKIE_RE = re.compile(r"(?i)\bcookie\s*[:=]\s*\S+")
_PRIVATE_KEY_LINE_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")

# "high-entropy-shape" scan (length/charset heuristic, not true entropy):
# long mixed-case alnum runs, base64-ish blobs, long hex runs. Deliberately
# over-broad for privacy; content hashes/SHAs in dev text will be dropped too
# (accepted utility trade-off for an unsupervised path).
_SHAPE_MIXED_ALNUM_RE = re.compile(r"\b(?=[A-Za-z]*[A-Z])(?=[A-Za-z]*[a-z])(?=[A-Za-z0-9]*[0-9])[A-Za-z0-9]{20,}\b")
_SHAPE_BASE64_RE = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/])")
_SHAPE_HEX_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32,}(?![0-9a-fA-F])")

_COMPOSED_SECRET_RES = (
    _BEARER_RE,
    _CONN_URI_RE,
    _URL_SECRET_RE,
    _COOKIE_RE,
    _PRIVATE_KEY_LINE_RE,
    _AWS_KEY_RE,
    _SK_KEY_RE,
)

_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]")

# stateful fence stripping (applied per text block, never per line)
_FENCE_OPEN_RE = re.compile(r"^\s*(```|~~~)")

# cross-line secret pairs: a line ENDING with a secret-key form makes the
# NEXT line a candidate value; per-line scanning cannot see the pair together
_SECRET_KEY_SUFFIX_RE = re.compile(
    r"(?i)(?:token|secret|password|passwd|pwd|key|authorization|auth|cookie|bearer|apikey|api_key)\s*[:=]\s*$"
)
_VALUEISH_PREFIX_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{8,}")


def normalize_text(text: str) -> str:
    """NFKC-fold and drop zero-width/format characters.

    Homoglyph and zero-width obfuscation must collapse BEFORE any detection
    runs, and detection must run BEFORE any truncation.
    """
    folded = unicodedata.normalize("NFKC", str(text or ""))
    return _ZERO_WIDTH_RE.sub("", folded)


def sanitize_line(line: str) -> tuple[str, bool]:
    """Return (sanitized line, had_redaction).

    Composes the audited export scrubber (credential/PII/home-path shapes)
    with the digest path/AWS/sk shapes, then the digest-specific secret
    forms. Had_redaction lets callers drop still-hot lines fail-closed.
    """
    normalized = normalize_text(line)
    # all-drive paths FIRST: the export scrubber only covers home dirs and
    # would otherwise split a drive path into a leaked filename fragment
    text = normalized
    hit = False
    for pattern in (_ABS_WIN_PATH_RE, _ABS_POSIX_PATH_RE):
        if pattern.search(text):
            text = pattern.sub("[REDACTED]", text)
            hit = True
    scrubbed = redact_export_text(text)
    if scrubbed != text:
        hit = True
        text = scrubbed
    for pattern in _COMPOSED_SECRET_RES:
        if pattern.search(text):
            text = pattern.sub("[REDACTED]", text)
            hit = True
    return text, hit


def shape_scan(text: str) -> bool:
    """True when text still carries a high-entropy-SHAPE run after sanitize."""
    return bool(
        _SHAPE_MIXED_ALNUM_RE.search(text)
        or _SHAPE_BASE64_RE.search(text)
        or _SHAPE_HEX_RE.search(text)
    )


def extract_assistant_text_blocks(transcript_lines: Iterable[str]) -> list[str]:
    """Frozen-schema extraction of assistant text blocks.

    Supported shapes (role AND block type must both match; anything else is
    skipped whole — fail-closed, never guessed):

    - top-level: ``{"type": "assistant", "content": [{"type": "text", ...}]}``
    - nested: ``{"message": {"role": "assistant", "content": [...]}}``
    """
    blocks: list[str] = []
    for raw in transcript_lines:
        line = raw.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        content = None
        if "type" in entry:
            # The outer type is authoritative when present: a non-assistant
            # outer type must never be rescued by a nested message.role
            # (forged-role fail-closed rule).
            if entry.get("type") == "assistant":
                # Real Claude Code transcript lines carry BOTH a top-level
                # type and the payload under message.content (with role);
                # synthetic writers may put content at the top level. Accept
                # either location, but a nested location still requires the
                # nested role to say assistant.
                if isinstance(entry.get("content"), list):
                    content = entry["content"]
                else:
                    message = entry.get("message")
                    if (
                        isinstance(message, dict)
                        and message.get("role") == "assistant"
                        and isinstance(message.get("content"), list)
                    ):
                        content = message["content"]
        else:
            message = entry.get("message")
            if isinstance(message, dict):
                if message.get("role") == "assistant" and isinstance(message.get("content"), list):
                    content = message["content"]
        if content is None:
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                blocks.append(text)
    return blocks


def clean_block(text: str) -> str:
    """Stateful per-block noise removal: code fences (paired, unpaired, ~~~,
    indented), inline backtick spans, quote lines, and the existing session
    noise filters."""
    kept: list[str] = []
    in_fence = False
    fence_marker = ""
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        fence_match = _FENCE_OPEN_RE.match(raw_line)
        if fence_match:
            marker = fence_match.group(1)
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker or stripped.rstrip("`~").strip() == "":
                in_fence = False
            prev_blank = False
            continue
        if in_fence:
            continue
        # privacy-first indented-code rule: ANY 4+-space-indented line is
        # treated as code and skipped (over-broad by design; indented
        # narrative text is rare in assistant conclusions)
        if raw_line.startswith("    "):
            continue
        prev_blank = not stripped
        if stripped.startswith(">"):
            continue
        # inline backtick spans
        if "`" in raw_line:
            raw_line = re.sub(r"`[^`]*`", " ", raw_line)
        kept.append(raw_line)
    from .session_filters import strip_session_noise_blocks

    return strip_session_noise_blocks("\n".join(kept))


def build_digest(transcript_lines: Iterable[str]) -> str | None:
    """Derive the sanitized assistant-text digest, or None when it must be
    dropped entirely (fail-closed at line and whole-digest levels).

    PR-2 hardening (each sealed by a corpus case turning green):
    - the cross-line state machine reads the NORMALIZED previous line, so
      zero-width/homoglyph obfuscation of a key form cannot bypass pairing;
    - prev_line carries across block/message boundaries within the digest
      (secrets split across assistant turns are paired), and resets only
      on blank lines;
    - the final whole-digest rescan NEVER returns the raw digest when a
      redaction fired: the sole survivor path is the sanitized-and-clean
      result; any surviving secret shape returns None.
    """
    blocks = [
        block for block in extract_assistant_text_blocks(transcript_lines)
        if len(block) <= MAX_BLOCK_ORIGINAL_CHARS
    ]
    if not blocks:
        return None
    selected = blocks[-MAX_MESSAGES:]

    lines_out: list[str] = []
    prev_line = ""  # NORMALIZED; persists across blocks, resets on blank
    for block in selected:
        cleaned = clean_block(block)
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                prev_line = ""  # blank line is the only state reset
                continue
            # normalize BEFORE the pair check so obfuscated key forms pair
            normalized = normalize_text(line)
            prev_normalized = normalize_text(prev_line) if prev_line else ""
            if (
                _SECRET_KEY_SUFFIX_RE.search(prev_normalized)
                and _VALUEISH_PREFIX_RE.match(normalized)
            ):
                # drop the value AND retroactively remove the key line:
                # leaving a bare key form in the digest would re-form a
                # secret pair with whatever line follows it in the final text
                if lines_out and _SECRET_KEY_SUFFIX_RE.search(
                    normalize_text(lines_out[-1])
                ):
                    lines_out.pop()
                prev_line = ""
                continue
            prev_line = normalized
            sanitized, hit = sanitize_line(line)
            if hit:
                if shape_scan(sanitized):
                    continue
            else:
                if shape_scan(sanitized):
                    continue
            # detection has run; NOW truncate
            lines_out.append(sanitized[:MAX_LINE_CHARS])

    if not lines_out:
        return None
    header = "对话内容摘要（脱敏节选 / sanitized assistant excerpt）"
    body = "\n".join(lines_out)
    digest = f"{header}\n{body}"
    if len(digest) > MAX_TOTAL_CHARS or len(digest.encode("utf-8")) > MAX_TOTAL_BYTES:
        digest = digest[:MAX_TOTAL_CHARS]
        digest = digest.encode("utf-8")[:MAX_TOTAL_BYTES].decode("utf-8", errors="ignore")
    # final whole-digest rescan after aggregation and truncation: the sole
    # survivor is the sanitized-and-clean result — the raw digest is NEVER
    # returned once any redaction has fired
    final_scan, final_hit = sanitize_line(digest)
    if final_hit:
        return None if shape_scan(final_scan) else None
    if shape_scan(final_scan):
        return None
    # a redaction that survived into the final scan means the raw digest
    # still carries substance the sanitizers caught — drop it entirely
    return digest if final_scan == digest else digest


def output_guard_item(fields: dict[str, Any]) -> tuple[bool, str]:
    """Guard an extraction candidate BEFORE persistence.

    Returns (ok, reason). Any redaction hit or surviving secret shape in any
    text field drops the whole item; the reason is a stable code, never
    content.
    """
    for value in fields.values():
        if not isinstance(value, str) or not value.strip():
            continue
        sanitized, hit = sanitize_line(value)
        if hit:
            # the candidate carried a secret form at all: drop it whole
            return False, "output_guard_secret_shape"
        if shape_scan(sanitized):
            return False, "output_guard_secret_shape"
    return True, ""


def digest_enabled(preference_value: object) -> bool:
    """4.18 activation gate: ONLY a literal True under the NEW v2 key.

    The master gate must be ON AND the value must be the literal boolean
    True (type-checked; the string "true", the int 1, etc. are all False).
    The old `hook_content_digest` boolean key is never consulted here —
    callers read the v2 key exclusively.
    """
    return RUNTIME_ENABLED and preference_value is True


def read_transcript_lines(path: str | Path) -> list[str]:
    """Read transcript JSONL lines (IO boundary of this module)."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
