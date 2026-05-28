"""Sensitivity classification — assigns each field/item a sensitivity level
so the governance gate (governance.py) can enforce a ceiling per agent.

KEY DESIGN POINT (per product owner): **safe by default with ZERO config.**
Most users never set ``trust_boundaries.restricted_fields``, so protection
must NOT depend on configuration. Built-in defaults treat PII
(``ENCRYPTED_PROFILE_FIELDS`` — email/phone/address/…) as ``private`` and
credential-shaped field names as ``secret``, for everyone, out of the box.
A user's ``restricted_fields`` is an *additive* layer on top for power users
(like the founder, who has configured a lot) — it can only RAISE
sensitivity, never lower the built-in floor.

Levels (low → high): public < work < private < secret. Anything unlabeled
defaults to ``work`` (never ``public``) — fail toward not-leaking.

This module is pure (no I/O); it's the input layer feeding ``governance.gate``
and is not itself wired into the read path yet.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from .governance import SENSITIVITY_ORDER
from .storage import ENCRYPTED_PROFILE_FIELDS

VALID_LEVELS = ("public", "work", "private", "secret")
DEFAULT_LEVEL = "work"

# ── Token-based field-name classifier ────────────────────────────────────
# Codex round-5 P1: the old separator-normalization approach (api-key →
# api_key, then substring match) was bypassable with other separators —
# api/key, api:key, api[key], email-address, contact.email all slipped past
# and leaked. The fix is to TOKENIZE the field name instead of enumerating
# separators: split camelCase, then split on any run of non-alphanumeric
# characters. Every separator is a token boundary by construction, so no new
# separator can bypass it. Classification is by whole-token membership,
# token-group containment, and high-confidence credential suffixes/abbrevs —
# never arbitrary substrings, so benign names ("valid_numbers", "monkey") are
# not over-flagged.
#
# Codex round-6 P1: tokenizing fixed separators but left SPELLING bypasses —
# (a) glued vendor forms (openaiapikey / githubtoken / stripesecretkey),
# (b) abbreviations (pwd / creds), (c) zero-width chars splitting a word
# (a​p​i_key → a/p/i/key), (d) digit seams (api2_key → api2/key),
# (e) confusable scripts (eмail with a Cyrillic м). Defenses below: Unicode
# hygiene (NFKC + strip format chars + casefold), credential suffix/abbrev
# matching, digit-seam splitting, and a mixed-script fail-closed floor.

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_DIGIT_SEAM_RE = re.compile(r"(?<=[a-z])(?=[0-9])|(?<=[0-9])(?=[a-z])")
_NONALNUM_RE = re.compile(r"[^a-z0-9]+")
# Cyrillic + Greek letters are the common ASCII look-alikes (е, а, о, м, р, …).
_CONFUSABLE_SCRIPT_RE = re.compile(r"[Ͱ-ϿЀ-ӿ]")
_ASCII_LETTER_RE = re.compile(r"[A-Za-z]")


def _normalize_field_name(name: str) -> str:
    """Unicode-hygiene a field name before tokenizing: NFKC-fold (collapses
    fullwidth/compatibility forms like ＡＰＩ＿ＫＥＹ → API_KEY) and drop
    format/control chars (category ``Cf`` — zero-width space/joiner/etc.) so
    they can't be used to split a sensitive word (a​p​i_key → api_key).
    """
    s = unicodedata.normalize("NFKC", str(name or ""))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Cf")
    return s.strip()


def _field_tokens(name: str) -> list[str]:
    """Split a field name into lowercase alphanumeric tokens.

    ``apiKey`` → [api, key]; ``api/key`` / ``api[key]`` / ``API Key`` →
    [api, key]; ``email-address`` → [email, address]; ``api2_key`` →
    [api, 2, key] (digit seam). Separator-agnostic and Unicode-hygiened:
    any non-alphanumeric run is a token boundary, and zero-width/format chars
    are stripped first, so neither new separators nor invisible chars bypass.
    """
    s = _normalize_field_name(name)
    if not s:
        return []
    s = _CAMEL_RE.sub(" ", s).casefold()
    s = _DIGIT_SEAM_RE.sub(" ", s)
    return [t for t in _NONALNUM_RE.split(s) if t]


def _has_mixed_confusable_script(name: str) -> bool:
    """True if a field name mixes ASCII letters with Cyrillic/Greek letters —
    the signature of a confusable attack (eмail, аpi_key). Pure-CJK or
    pure-Cyrillic names are NOT flagged (no ASCII to imitate), so legitimate
    non-Latin field names are unaffected."""
    s = _normalize_field_name(name)
    return bool(_CONFUSABLE_SCRIPT_RE.search(s)) and bool(_ASCII_LETTER_RE.search(s))


# Single tokens that, on their own, mark a field as a credential → "secret".
# Glued separator-less forms (apikey, privatekey, …) tokenize to one token, so
# they live here too rather than needing a substring scan.
_SECRET_TOKENS = frozenset({
    "password", "passwd", "secret", "token", "credential", "credentials",
    "apikey", "privatekey", "secretkey", "accesskey", "clientsecret",
    "bearertoken", "refreshtoken", "accesstoken",
})
# Common credential abbreviations (whole-token match only).
_SECRET_ABBREVIATIONS = frozenset({"pwd", "creds", "cred"})
# High-confidence credential SUFFIXES — match a single glued token by its end
# (openaiapikey, githubtoken, stripesecretkey). These are deliberately full
# credential nouns, NOT a bare "key" (which would wrongly flag monkey /
# primary_key / public_key). "tokens_used" is safe: its token is "tokens",
# which does not end with "token".
_SECRET_SUFFIXES = (
    "apikey", "secretkey", "privatekey", "accesskey",
    "accesstoken", "authtoken", "bearertoken", "refreshtoken", "token",
    "password", "passwd", "passphrase",
    "secret", "credential", "credentials",
)
# Token GROUPS (ALL members must be present) that mark a field "secret".
# "key"/"access" alone are too generic (primary_key, access_count) to flag, so
# they only count in combination.
_SECRET_GROUPS = (
    frozenset({"api", "key"}),
    frozenset({"private", "key"}),
    frozenset({"access", "key"}),
    frozenset({"secret", "key"}),
    frozenset({"client", "secret"}),
    frozenset({"bearer", "token"}),
    frozenset({"refresh", "token"}),
)


def _token_is_secret(token: str) -> bool:
    """A single token reads as a credential: exact credential word, known
    abbreviation, or a high-confidence credential suffix (glued vendor form)."""
    if token in _SECRET_TOKENS or token in _SECRET_ABBREVIATIONS:
        return True
    return any(token.endswith(suffix) for suffix in _SECRET_SUFFIXES)

# PII → "private", derived from the fields already encrypted at rest so the
# floor matches what the product treats as sensitive. Single-word fields are
# single tokens; multi-word ones (real_name, id_number) contribute BOTH a
# group (real+name) and a glued single token (realname) for the separator-less
# spelling.
_PRIVATE_TOKENS = frozenset(
    {f.lower() for f in ENCRYPTED_PROFILE_FIELDS if "_" not in f}
    | {"".join(_field_tokens(f)) for f in ENCRYPTED_PROFILE_FIELDS if "_" in f}
)
_PRIVATE_GROUPS = tuple(
    frozenset(_field_tokens(f)) for f in ENCRYPTED_PROFILE_FIELDS if "_" in f
)


def _groups_match(tokens: set[str], groups: Iterable[frozenset]) -> bool:
    """True if any non-empty group is fully contained in ``tokens``."""
    return any(g and g <= tokens for g in groups)


# ── Codex round-7 P1: CJK (Chinese) semantic field names ─────────────────────
# The ASCII tokenizer treats every CJK ideograph as a separator, so a Chinese
# field name like 邮箱地址/密码/api密钥 tokenizes to nothing (or just the ASCII
# fragment) and fell through to the ``work`` default — then leaked to
# read-only-external when explicitly marked ``public``. Engram is a
# Chinese-first product, so CJK PII/credential field names MUST get the same
# floor as their English counterparts. CJK has no word separators, so these
# high-confidence whole terms are matched by substring. That is safe and does
# NOT reintroduce English false positives: these multi-byte ideographs never
# occur inside ASCII engineering identifiers, so all-ASCII names never match.
_SECRET_CJK_TERMS = (
    "密码", "密钥", "秘钥", "令牌", "口令", "凭证", "私钥",
    "访问令牌", "刷新令牌", "客户端密钥",
)
_PRIVATE_CJK_TERMS = (
    "邮箱", "邮箱地址", "电子邮箱", "手机号", "手机号码",
    "电话号码", "电话", "住址", "地址", "身份证", "身份证号",
    "真实姓名", "姓名",
)


def _contains_cjk_term(name: str, terms: tuple[str, ...]) -> bool:
    """True if a normalized field name contains any high-confidence CJK
    sensitive term. Substring matching is both necessary (CJK has no word
    separators to tokenize on) and safe (these ideographs never appear inside
    ASCII engineering identifiers, so this cannot reintroduce English false
    positives)."""
    s = _normalize_field_name(name)
    return any(term in s for term in terms)


def classify_field(name: str, restricted_fields: Iterable[str] = ()) -> str:
    """Sensitivity of a single (identity/profile) field by its NAME.

    Token-based and separator-agnostic for ASCII; high-confidence CJK terms are
    matched directly (CJK has no separators and tokenizes to nothing). The
    built-in floor applies with zero config; ``restricted_fields`` is an
    additive layer — it can only RAISE a field to ``private`` (the secret check
    runs first and is never lowered).
    """
    tokens = _field_tokens(name)
    tokenset = set(tokens)

    # Secret floor: ASCII credential tokens/groups, or a high-confidence CJK
    # credential term. The CJK check is NOT gated behind ``tokens`` because a
    # pure-CJK name like 密码/密钥 tokenizes to nothing.
    if (any(_token_is_secret(t) for t in tokens)
            or _groups_match(tokenset, _SECRET_GROUPS)
            or _contains_cjk_term(name, _SECRET_CJK_TERMS)):
        return "secret"
    if (bool(tokenset & _PRIVATE_TOKENS)
            or _groups_match(tokenset, _PRIVATE_GROUPS)
            or _contains_cjk_term(name, _PRIVATE_CJK_TERMS)):
        return "private"
    if not tokens:
        return DEFAULT_LEVEL
    restricted_groups = [frozenset(_field_tokens(f)) for f in restricted_fields]
    if _groups_match(tokenset, restricted_groups):
        return "private"
    # Confusable fail-closed: a name mixing ASCII with Cyrillic/Greek letters
    # (eмail, аpi_key) can't be trusted to tokenize correctly — floor it to
    # private so an explicit `public` can't leak it to read-only-external.
    if _has_mixed_confusable_script(name):
        return "private"
    return DEFAULT_LEVEL


def _field_floor(name: str, restricted_fields: Iterable[str] = ()) -> str:
    """Sensitivity FLOOR contributed by a field NAME: ``private``/``secret``
    if the name is sensitive, else ``public`` (no constraint). Used so an
    item carrying a sensitive-named field can't be marked below that level."""
    lvl = classify_field(name, restricted_fields)
    return lvl if lvl in ("private", "secret") else "public"


def _all_field_names(obj, *, max_nodes: int = 10000) -> tuple[set[str], bool]:
    """Collect dict keys nested ANYWHERE in ``obj`` (dicts/lists), iteratively
    (no recursion-limit risk). Returns (names, truncated). ``truncated`` is
    True if the node budget was hit — caller should fail closed, since an
    unscanned region might hide a sensitive field (e.g. metadata.api_key)."""
    names: set[str] = set()
    stack = [obj]
    count = 0
    while stack:
        cur = stack.pop()
        count += 1
        if count > max_nodes:
            return names, True
        if isinstance(cur, dict):
            for k, v in cur.items():
                names.add(str(k))
                stack.append(v)
        elif isinstance(cur, (list, tuple)):
            stack.extend(cur)
    return names, False


def classify_item(item: dict, restricted_fields: Iterable[str] = ()) -> str:
    """Sensitivity of a knowledge item (lesson/decision/playbook).

    Honors an explicit, valid ``item['sensitivity']``; otherwise defaults to
    ``work`` (never ``public``). ``restricted_fields`` is accepted for a
    future content-aware pass; v1 does not down-rank below ``work``.
    """
    if not isinstance(item, dict):
        return DEFAULT_LEVEL
    explicit = str(item.get("sensitivity", "")).strip().lower()
    base = explicit if explicit in VALID_LEVELS else DEFAULT_LEVEL
    # Field-name floor: an item carrying e.g. an `api_key` field — at ANY
    # nesting depth (metadata.api_key, steps[].private_key, …) — is at least
    # `secret`. Explicit level can only RAISE, never lower, this floor.
    names, truncated = _all_field_names(item)
    floor = "secret" if truncated else "public"  # fail closed if not fully scanned
    for name in names:
        f = _field_floor(name, restricted_fields)
        if SENSITIVITY_ORDER[f] > SENSITIVITY_ORDER[floor]:
            floor = f
    return base if SENSITIVITY_ORDER[base] >= SENSITIVITY_ORDER[floor] else floor


def annotate_items(items: Iterable[dict], restricted_fields: Iterable[str] = ()) -> list[dict]:
    """Return shallow copies of ``items`` with a ``sensitivity`` field set
    (for feeding straight into ``governance.gate``). Non-dict items are kept
    as-is (the gate fail-safes on them)."""
    out: list[dict] = []
    for it in items:
        if isinstance(it, dict):
            c = dict(it)
            c["sensitivity"] = classify_item(it, restricted_fields)
            out.append(c)
        else:
            out.append(it)
    return out
