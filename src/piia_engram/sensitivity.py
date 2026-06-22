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
# ── CJK terms as high-coverage ROOTS (proactive round-9 hardening) ───────────
# Rounds 7–8 enumerated whole words and kept leaking real synonyms (a self-run
# adversarial sweep found ~30 more: 微信号/验证码/助记词/凭据/银行账号/社保号/…).
# Manually listing every Chinese synonym never converges. Instead we list the
# high-signal ROOT of each family and rely on substring matching: 令牌 covers
# 访问令牌/刷新令牌/会话令牌; 密钥 covers api密钥/访问密钥/客户端密钥; 卡号 covers
# 银行卡号/信用卡号/储蓄卡号; 手机 covers 手机号/手机号码; 身份证 covers 身份证号/号码.
# This is the name-based half of the two-layer defense; the language-independent
# VALUE scanner (classify_value) is the converging safety net for everything a
# curated name list can still miss (incl. benign-named fields with secret values).
# Deliberate non-inclusions (over-broad → benign collisions): bare 账号/账户
# (用户账号 = a username, low sensitivity), bare 邮件 (邮件标题/内容 = subject/body),
# bare 钥 (公钥 = a *public* key, not a secret). Mirrors the bare-key/bare-auth
# boundary on the ASCII side.
_SECRET_CJK_TERMS = (
    "密码", "密钥", "秘钥", "私钥", "令牌", "口令",
    "凭证", "凭据", "助记词", "种子短语", "暗号", "验证码", "授权码",
)
_PRIVATE_CJK_TERMS = (
    # contact
    "邮箱", "电子邮件", "邮件地址", "电话", "手机", "微信", "联系方式",
    # identity documents
    "身份证", "证件", "护照", "签证", "驾驶证", "驾照",
    "社保", "医保", "学号", "工号", "车牌",
    # financial
    "银行卡", "信用卡", "储蓄卡", "卡号", "银行账号", "支付宝", "公积金",
    "工资", "薪资", "薪水", "年薪", "收入", "余额",
    # address & demographics
    "住址", "地址", "籍贯", "民族", "国籍", "生日", "出生",
    "真实姓名", "姓名",
)

_CJK_CHAR_RE = re.compile(r"[一-鿿]")


def _contains_cjk(s: str) -> bool:
    """True if ``s`` contains any CJK Unified ideograph."""
    return bool(_CJK_CHAR_RE.search(s))


def _contains_cjk_term(name: str, terms: tuple[str, ...]) -> bool:
    """True if a normalized field name contains any high-confidence CJK
    sensitive term. Substring matching is both necessary (CJK has no word
    separators to tokenize on) and safe (these ideographs never appear inside
    ASCII engineering identifiers, so this cannot reintroduce English false
    positives)."""
    s = _normalize_field_name(name)
    return any(term in s for term in terms)


def _restricted_field_matches(name: str, restricted_fields: Iterable[str]) -> bool:
    """True if ``name`` is covered by any user ``restricted_fields`` entry —
    the additive, raise-only power-user layer. Three match modes:

    1. token-group containment (ASCII, separator/case-agnostic) — the original
       behaviour, so restricted ``api key`` still raises ``api-key``/``apiKey``;
    2. normalized exact equality — covers names that tokenize to nothing;
    3. a CJK-containing restricted field as a normalized substring — Codex
       round-8 P2: pure-CJK names tokenize to nothing, so a user's explicit
       ``restricted_fields=["项目代号"]`` was silently ignored. Substring is
       gated to CJK-bearing restricted entries only (CJK never collides with
       ASCII identifiers), so this can't reintroduce ASCII false positives.
    """
    tokenset = set(_field_tokens(name))
    n = _normalize_field_name(name)
    for f in restricted_fields:
        group = frozenset(_field_tokens(f))
        if group and group <= tokenset:
            return True
        r = _normalize_field_name(f)
        if not r:
            continue
        if n == r:
            return True
        if _contains_cjk(r) and r in n:
            return True
    return False


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
    # User restricted_fields (additive, raise-only to private). Checked BEFORE
    # the empty-tokens early return so pure-CJK custom fields are honored.
    if _restricted_field_matches(name, restricted_fields):
        return "private"
    if not tokens:
        return DEFAULT_LEVEL
    # Confusable fail-closed: a name mixing ASCII with Cyrillic/Greek letters
    # (eмail, аpi_key) can't be trusted to tokenize correctly — floor it to
    # private so an explicit `public` can't leak it to read-only-external.
    if _has_mixed_confusable_script(name):
        return "private"
    return DEFAULT_LEVEL


# ── Layer 2: language-independent VALUE scanner ──────────────────────────────
# A field-NAME classifier — in any language — fundamentally cannot catch a
# benign-named field that holds a sensitive VALUE (e.g. {"备注": "sk-proj-…"} or
# {"note": "alice@example.com"}). This layer inspects VALUES for high-confidence
# credential shapes (-> secret) and PII shapes (-> private). It is language-
# agnostic, so it's the part of the defense that actually converges instead of
# chasing synonyms. Credentials are matched anywhere (a leaked key is
# catastrophic and the shapes have ~zero false positives); PII is floored only
# for "field-like" (short/atomic) values so a long lesson body that merely
# mentions a contact address is treated as content, not hidden.

# High-confidence credential shapes (well-known token formats).
_SECRET_VALUE_RE = re.compile(
    r"sk-[A-Za-z0-9_\-]{16,}"                 # OpenAI (incl. sk-proj-)
    r"|sk_(?:live|test)_[A-Za-z0-9]{10,}"     # Stripe secret key
    r"|rk_(?:live|test)_[A-Za-z0-9]{10,}"     # Stripe restricted key
    r"|gh[pousr]_[A-Za-z0-9]{20,}"            # GitHub PAT / OAuth
    r"|github_pat_[A-Za-z0-9_]{20,}"          # GitHub fine-grained PAT
    r"|glpat-[A-Za-z0-9_\-]{20,}"             # GitLab PAT
    r"|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}"     # AWS access key id (+ temp)
    r"|AIza[0-9A-Za-z_\-]{35,}"               # Google API key
    r"|ya29\.[0-9A-Za-z_\-]{20,}"             # Google OAuth access token
    r"|xox[baprs]-[0-9A-Za-z\-]{10,}"         # Slack token (bot/user/app/refresh)
    r"|xapp-[0-9A-Za-z\-]{10,}"               # Slack app-level token
    r"|hf_[A-Za-z0-9]{20,}"                   # HuggingFace
    r"|pypi-[A-Za-z0-9_\-]{20,}"              # PyPI upload token
    r"|cfut_[A-Za-z0-9_\-]{20,}"              # Cloudflare API token
    r"|eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"  # JWT
    r"|-----BEGIN[ A-Z]*PRIVATE KEY-----"     # PEM private key block
)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_CN_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")          # CN mobile (11)
_CN_ID_RE = re.compile(r"(?<![0-9Xx])\d{17}[0-9Xx](?![0-9Xx])")  # CN ID (18)
_CARD_RE = re.compile(r"(?<!\d)\d{13,19}(?!\d)")               # card candidate
_CN_MOBILE_BARE = re.compile(r"1[3-9]\d{9}")                   # CN mobile, no anchors
# Codex round-10 P1: phone/card are often written with spaces/hyphens/country
# code (4111 1111 1111 1111 / 138-0013-8000 / +86 138 0013 8000). A digit-run
# token interleaved with presentation separators (optionally a leading +). Used
# ONLY on short values, and every candidate is still validated by Luhn /
# ISO 7064 checksum / the exact 11-digit mobile pattern — so normalization
# widens the accepted FORMAT without lowering the confidence bar (a random
# formatted business number won't pass Luhn or the mobile shape).
# Codex round-11 P1: a CN resident ID's ISO 7064 check digit can be ``X``, so
# the candidate may legitimately end in X/x (110105 19491231 002X). Allow X/x
# in the run and as the final char; non-CN-ID candidates still must be pure
# digits to reach the card/phone checks (compact.isdigit() guard below).
# Codex round-13 P1: the SAME card/phone/ID must not flip private -> public just
# by swapping the visible separator. Spaces and hyphens alone are not how PII is
# written in the wild (555.123.4567, 06.12.34.56.78, 4111/1111/...). So the
# separator class is a small, explicit *presentation-separator allowlist*:
#   \s  whitespace (space/tab/newline; NFKC already folded U+3000/NBSP/figure/
#       narrow-NBSP to ASCII space in _normalize_visible_text)
#   -   hyphen
#   .   dot          /   slash   (NFKC folds fullwidth U+FF0E/U+FF0F to these)
#   middle-dot family: U+00B7 · , U+2027 ‧ , U+30FB ・ (NFKC folds halfwidth
#       U+FF65 to U+30FB; none of these fold to ASCII, so they stay in the class)
# Deliberately EXCLUDED (higher false-positive surface, evaluate separately):
#   , (thousands separators)  : (time/port/log)  ; _  — these can also carry PII
#   but collide with far more benign numeric formats; the high-confidence
#   validators would catch real PII, but the candidate-discovery cost isn't worth
#   it yet. Tracked as a known limitation, NOT a silent gap.
# The separator allowlist controls candidate DISCOVERY only; the Luhn / ISO 7064
# / 1[3-9]\d{9} validators remain the real gate, so widening separators widens
# FORMAT, not confidence. Residual risk is two-sided: a *false negative* on an
# exotic separator (documented), AND a small *false positive* rate — a random
# short grouped-digit run can coincidentally pass Luhn (~1/10) or match the
# CN-mobile shape (e.g. "release 1.38.0013.8000"), so the validators NARROW but
# do not structurally ELIMINATE over-classification. That is safety-first
# over-protection (a utility cost), NOT a leak. Tightening it would mean
# constraining group shapes (card 4-4-4-4, phone 3-4-4, +86 prefix); deferred
# until real-usage feedback (Codex r14 non-blocking note).
_FORMATTED_NUM_SEP = r"[\s\-./·‧・]+"
_FORMATTED_NUM_RE = re.compile(r"\+?[0-9][0-9Xx\s\-./·‧・]{8,}[0-9Xx]")

_CN_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_CN_ID_CHECKSUM = "10X98765432"
_PII_SHORT_MAXLEN = 64  # PII floors only "field-like" short values, not prose


def _luhn_ok(num: str) -> bool:
    """Luhn (mod-10) check — filters ~90% of random digit runs so a 13–19 digit
    value must actually look like a payment card before it floors to private."""
    total, alt = 0, False
    for ch in reversed(num):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _scan_cn_id(s: str) -> bool:
    """True if ``s`` contains a mainland-China resident ID with a valid ISO
    7064 MOD-11-2 checksum (the checksum makes this high-confidence, not just
    'any 18 digits')."""
    for m in _CN_ID_RE.finditer(s):
        d = m.group()
        total = sum((ord(c) - 48) * w for c, w in zip(d[:17], _CN_ID_WEIGHTS))
        if _CN_ID_CHECKSUM[total % 11] == d[17].upper():
            return True
    return False


def _scan_formatted_groups(groups: list[str]) -> bool:
    """A formatted PII is *some run* of consecutive separator-delimited digit
    groups (e.g. a 16-digit card written as four 4-digit groups). Slide every
    consecutive-group window, compact it, and validate with the SAME
    high-confidence checks as the contiguous path. This is what stops a greedy
    candidate that swallows *several* formatted PII (Codex round-12 P1) from
    failing whole-candidate validation and leaking the inner valid PII —
    each true PII still surfaces as its own window.

    Bounded O(n^2) on a tiny ``n`` (the caller only reaches here for short
    values, ``len(s) <= _PII_SHORT_MAXLEN``), and the inner loop breaks as soon
    as the compacted run exceeds the longest valid shape (a 19-digit card)."""
    n = len(groups)
    for i in range(n):
        compact = ""
        for j in range(i, n):
            compact += groups[j]
            if len(compact) > 19:        # nothing valid is longer than a 19-digit card
                break
            # CN resident ID first — it alone may end in X/x; _scan_cn_id enforces
            # the \d{17}[0-9Xx] shape + ISO 7064 checksum, so a bad 18-char run fails.
            if len(compact) == 18 and _scan_cn_id(compact):       # CN resident ID
                return True
            if not compact.isdigit():
                continue
            if 13 <= len(compact) <= 19 and _luhn_ok(compact):    # payment card
                return True
            mob = compact[2:] if (len(compact) == 13 and compact.startswith("86")) else compact
            if _CN_MOBILE_BARE.fullmatch(mob):                    # CN mobile (+ 86 prefix)
                return True
    return False


def _has_formatted_pii(s: str) -> bool:
    """Catch phone/card PII written with presentation separators (whitespace,
    hyphen, dot, slash, middle-dot family — see ``_FORMATTED_NUM_SEP``) or a
    country code. Each candidate is split into its separator-delimited digit
    groups, and every consecutive-group window is validated by the SAME
    high-confidence checks as the contiguous path (Luhn for cards, ISO 7064 for
    CN IDs, the exact 1[3-9]\\d{9} shape for CN mobiles, incl. an optional
    86 / +86 country code).

    Two properties matter for the governance gate:
    * Window scanning (not one greedy compacted run) keeps multiple formatted
      PII packed into one field from hiding behind an over-long no-match (r12).
    * The separator allowlist for DISCOVERY and SPLIT is the same set, so the
      same card/phone/ID can't drop from private to public just by swapping a
      visible separator (r13). The validators stay the confidence gate."""
    for m in _FORMATTED_NUM_RE.finditer(s):
        groups = [g for g in re.split(_FORMATTED_NUM_SEP, m.group().replace("+", "")) if g]
        if _scan_formatted_groups(groups):
            return True
    return False


def _has_pii_pattern(s: str) -> bool:
    return bool(
        _EMAIL_RE.search(s)
        or _CN_PHONE_RE.search(s)
        or _scan_cn_id(s)
        or any(_luhn_ok(m.group()) for m in _CARD_RE.finditer(s))
        or _has_formatted_pii(s)
    )


def _normalize_visible_text(s: str) -> str:
    """Value-side twin of :func:`_normalize_field_name` (Codex round-11 P1):
    NFKC-fold and strip Unicode format (``Cf``) chars so the value scanner sees
    the same canonical text an agent reads. Fullwidth digits/letters fold to
    ASCII (４１１１→4111, ｓｋ→sk — common in a CJK-first product) and zero-width /
    bidi insertions are removed (sk-proj-…​…→sk-proj-……). NO casefold: the
    credential regexes are case-sensitive (AKIA / AIza / ya29.)."""
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Cf")
    return s.strip()


def classify_value(value) -> str:
    """Sensitivity FLOOR implied by a field VALUE, independent of its name and
    of human language: high-confidence credential shapes -> ``secret``, PII
    shapes -> ``private``, otherwise ``public`` (no constraint).

    Only ``str``/``int`` scalars are inspected (ints are stringified, so a phone
    stored as a number is still caught); ``bool`` and other types are ``public``.

    The value is Unicode-hygiened first (``_normalize_visible_text``) so neither
    fullwidth input nor an adversarial zero-width insertion can slip a shape
    past the regexes; the ``<=64`` short-value gate is measured on the
    normalized text so fullwidth padding can't dodge it either.
    """
    if isinstance(value, bool):
        return "public"
    if isinstance(value, int):
        s = str(value)
    elif isinstance(value, str):
        s = value
    else:
        return "public"
    s = _normalize_visible_text(s)
    if not s:
        return "public"
    # Credentials: matched ANYWHERE, any length (catastrophic, near-zero FP).
    if _SECRET_VALUE_RE.search(s):
        return "secret"
    # PII: a whole-value email of any length, or any PII pattern inside a short
    # ("field-like") value. Long free text is treated as content, not floored.
    if _EMAIL_RE.fullmatch(s) or (len(s) <= _PII_SHORT_MAXLEN and _has_pii_pattern(s)):
        return "private"
    return "public"


def _field_floor(name: str, restricted_fields: Iterable[str] = ()) -> str:
    """Sensitivity FLOOR contributed by a field NAME: ``private``/``secret``
    if the name is sensitive, else ``public`` (no constraint). Used so an
    item carrying a sensitive-named field can't be marked below that level."""
    lvl = classify_field(name, restricted_fields)
    return lvl if lvl in ("private", "secret") else "public"


def _walk_item(obj, *, max_nodes: int = 10000) -> tuple[set[str], list, bool]:
    """Walk ``obj`` (dicts/lists/tuples) iteratively (no recursion-limit risk),
    collecting BOTH dict keys (field names) AND scalar leaf VALUES. Returns
    ``(names, values, truncated)``. ``values`` holds ``str``/``int`` leaves
    (the only types ``classify_value`` inspects), excluding ``bool``.
    ``truncated`` is True if the node budget was hit — the caller should fail
    closed, since an unscanned region might hide a sensitive field or value
    (e.g. metadata.api_key, or a key buried deep in a payload)."""
    names: set[str] = set()
    values: list = []
    stack = [obj]
    count = 0
    while stack:
        cur = stack.pop()
        count += 1
        if count > max_nodes:
            return names, values, True
        if isinstance(cur, dict):
            for k, v in cur.items():
                names.add(str(k))
                stack.append(v)
        elif isinstance(cur, (list, tuple)):
            stack.extend(cur)
        elif isinstance(cur, str) or (isinstance(cur, int) and not isinstance(cur, bool)):
            values.append(cur)
    return names, values, False


def _all_field_names(obj, *, max_nodes: int = 10000) -> tuple[set[str], bool]:
    """Back-compat thin wrapper over :func:`_walk_item` returning just the
    field names and the truncation flag (drops the collected values)."""
    names, _values, truncated = _walk_item(obj, max_nodes=max_nodes)
    return names, truncated


def classify_item(item: dict, restricted_fields: Iterable[str] = ()) -> str:
    """Sensitivity of a knowledge item (lesson/decision/playbook).

    Honors an explicit, valid ``item['sensitivity']``; otherwise defaults to
    ``work`` (never ``public``). The result can only be RAISED above the
    explicit/default base by two floors, never lowered:

    * **name floor** — a sensitive field NAME at ANY nesting depth
      (metadata.api_key, steps[].private_key, 密码, …);
    * **value floor** — a sensitive field VALUE at any depth (a credential
      shape -> secret, a PII shape -> private) regardless of how benign its
      field name is. This is the language-independent half that closes the
      benign-name / sensitive-value gap a name list can never cover.

    Codex round-10 P1: a dict KEY is also visible text returned to the agent,
    so a key that is *itself* a secret/PII string (e.g.
    ``{"tokens": {"sk-proj-…": true}}``) must be value-scanned too — every
    field name is run through BOTH ``_field_floor`` (as a name) and
    ``classify_value`` (as visible text).
    """
    if not isinstance(item, dict):
        return DEFAULT_LEVEL
    explicit = str(item.get("sensitivity", "")).strip().lower()
    base = explicit if explicit in VALID_LEVELS else DEFAULT_LEVEL
    names, values, truncated = _walk_item(item)
    floor = "secret" if truncated else "public"  # fail closed if not fully scanned
    for name in names:
        f = _field_floor(name, restricted_fields)
        if SENSITIVITY_ORDER[f] > SENSITIVITY_ORDER[floor]:
            floor = f
        if floor == "secret":
            break
        # a key can ITSELF be a secret/PII value (it is returned to the agent)
        kf = classify_value(name)
        if SENSITIVITY_ORDER[kf] > SENSITIVITY_ORDER[floor]:
            floor = kf
        if floor == "secret":
            break
    if floor != "secret":
        for v in values:
            vf = classify_value(v)
            if SENSITIVITY_ORDER[vf] > SENSITIVITY_ORDER[floor]:
                floor = vf
            if floor == "secret":
                break
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
