"""Tests for sensitivity classification (src/piia_engram/sensitivity.py).

Emphasis: SAFE BY DEFAULT WITH ZERO CONFIG — PII + credentials protected
even when the user has set no restricted_fields.
"""

from __future__ import annotations

from piia_engram import sensitivity as sv


# ── zero-config safety (the product-owner requirement) ───────────────────


def test_pii_is_private_with_zero_config():
    for f in ["email", "phone", "address", "location", "real_name", "id_number"]:
        assert sv.classify_field(f) == "private", f

def test_credential_shaped_names_are_secret_with_zero_config():
    for f in ["password", "api_key", "apiKey", "access_token", "ENGRAM_SECRET",
              "db_credential", "private_key"]:
        assert sv.classify_field(f) == "secret", f

def test_ordinary_fields_default_to_work_not_public():
    for f in ["role", "language", "technical_level", "description"]:
        assert sv.classify_field(f) == "work", f


# ── restricted_fields is additive (power users) ──────────────────────────


def test_restricted_fields_raise_to_private():
    # a field that would otherwise be "work" becomes private when restricted
    assert sv.classify_field("description") == "work"
    assert sv.classify_field("description", restricted_fields=["description"]) == "private"

def test_restricted_fields_cannot_lower_the_builtin_floor():
    # even if a credential-shaped field is NOT in restricted_fields, it stays secret
    assert sv.classify_field("api_key", restricted_fields=[]) == "secret"


# ── knowledge items ──────────────────────────────────────────────────────

def test_secret_field_cannot_be_marked_public_LEAK_REGRESSION():
    # Codex round-3 P1: an item with a credential field + explicit public
    # must NOT stay public (it would leak to read-only-external).
    item = {"id": "3", "sensitivity": "public", "api_key": "sk-abc"}
    assert sv.classify_item(item) == "secret"

def test_nested_secret_field_floors_item_LEAK_REGRESSION():
    # Codex round-4 P1: sensitive field nested under metadata/steps/parameters
    # must still floor the item to secret (top-level-only scan leaked these).
    cases = [
        {"sensitivity": "public", "metadata": {"api_key": "sk-abc"}},
        {"sensitivity": "public", "steps": [{"note": "ok"}, {"private-key": "x"}]},
        {"sensitivity": "public", "parameters": {"openai_api_key": "x"}},
        {"sensitivity": "public", "a": {"b": {"c": {"access_token": "x"}}}},
    ]
    for item in cases:
        assert sv.classify_item(item) == "secret", item


def test_nested_secret_blocked_through_annotate_gate():
    from piia_engram import governance as gov
    items = [{"id": "ok", "summary": "fine", "sensitivity": "public"},
             {"id": "leak", "sensitivity": "public", "metadata": {"api_key": "sk"}}]
    allowed, _ = gov.gate(sv.annotate_items(items), "read-only-external")
    assert {i["id"] for i in allowed} == {"ok"}


def test_deeply_nested_truncation_fails_closed():
    # pathological depth → can't fully scan → fail closed (secret)
    obj = cur = {}
    for _ in range(20000):
        cur["x"] = {}
        cur = cur["x"]
    item = {"sensitivity": "public", "deep": obj}
    assert sv.classify_item(item) == "secret"


def test_pii_field_floors_item_to_private():
    item = {"id": "x", "sensitivity": "public", "email": "a@b.c"}
    assert sv.classify_item(item) == "private"

def test_explicit_public_honored_when_no_sensitive_fields():
    assert sv.classify_item({"id": "1", "summary": "x", "sensitivity": "public"}) == "public"

def test_field_name_separator_normalization():
    # Codex round-3 P2: api-key / private-key / api.key must be detected
    for f in ["api-key", "api.key", "API Key", "private-key", "private.key"]:
        assert sv.classify_field(f) == "secret", f


def test_separator_bypass_vectors_round5_LEAK_REGRESSION():
    # Codex round-5 P1: the old separator-normalization only handled -, ., space.
    # Other separators (/, :, [], camelCase) bypassed it and leaked. The token
    # classifier must catch ALL of these credential spellings as secret.
    for f in ["api/key", "api:key", "api[key]", "private/key", "client-secret",
              "client/secret", "access/key", "bearer token", "refresh:token",
              "apiKey", "APIKey", "openai.api.key", "x-api-key"]:
        assert sv.classify_field(f) == "secret", f


def test_pii_bypass_vectors_round5_LEAK_REGRESSION():
    # PII spellings that the separator approach missed must classify private.
    for f in ["contact.email", "email-address", "email/address", "contactEmail",
              "user.phone", "real-name", "realName", "id/number", "idNumber"]:
        assert sv.classify_field(f) == "private", f


def test_token_classifier_no_false_positives():
    # The token classifier must NOT over-flag benign fields. "key"/"access"/
    # "name"/"id" alone are too generic; substring collisions (valid_numbers
    # ⊃ "idnumber") must not trigger.
    for f in ["primary_key", "cache_key", "access_count", "first_name",
              "user_id", "valid_numbers", "monkey", "tokens_used", "company_size"]:
        assert sv.classify_field(f) != "secret", f
    # these stay at the work default (no PII/credential token)
    for f in ["primary_key", "cache_key", "access_count", "valid_numbers"]:
        assert sv.classify_field(f) == "work", f


def test_round5_attack_set_blocked_through_gate():
    # Codex round-5 verbatim acceptance: every separator-bypass item must be
    # excluded for a read-only-external agent after annotate -> gate.
    from piia_engram import governance as gov
    cases = [
        {"sensitivity": "public", "api/key": "sk"},
        {"sensitivity": "public", "api:key": "sk"},
        {"sensitivity": "public", "api[key]": "sk"},
        {"sensitivity": "public", "private/key": "pem"},
        {"sensitivity": "public", "client-secret": "x"},
        {"sensitivity": "public", "contact.email": "a@b.c"},
        {"sensitivity": "public", "email-address": "a@b.c"},
        {"sensitivity": "public", "profile": {"contact/email": "a@b.c"}},
    ]
    allowed, _ = gov.gate(sv.annotate_items(cases), "read-only-external")
    assert allowed == []


# ── Codex round-6: spelling bypasses (glued / abbrev / unicode / digit) ──────


def test_round6_glued_and_abbrev_credential_names_are_secret():
    # Glued vendor-prefix + credential-suffix forms and common abbreviations
    # must classify secret (they're real engineering field names).
    for f in ["openaiapikey", "githubtoken", "stripesecretkey", "stripeSecretKey",
              "userpassword", "awscredentials", "ssh_passphrase",
              "pwd", "passwd", "creds", "cred"]:
        assert sv.classify_field(f) == "secret", f


def test_round6_extra_likely_credential_spellings_blocked():
    # Codex round-6 §11.1 verbatim: these must be excluded for read-only-external.
    from piia_engram import governance as gov
    cases = [
        {"sensitivity": "public", "openaiapikey": "sk"},
        {"sensitivity": "public", "githubtoken": "ghp_x"},
        {"sensitivity": "public", "stripesecretkey": "sk_live"},
        {"sensitivity": "public", "pwd": "secret"},
        {"sensitivity": "public", "creds": "secret"},
    ]
    allowed, _ = gov.gate(sv.annotate_items(cases), "read-only-external")
    assert allowed == []


def test_zero_width_inside_sensitive_word_does_not_bypass():
    # Codex round-6 §11.2: zero-width chars must not split a sensitive word.
    from piia_engram import governance as gov
    zw = "​"
    assert sv.classify_field(f"a{zw}p{zw}i_key") == "secret"
    cases = [{"sensitivity": "public", f"a{zw}p{zw}i_key": "sk"}]
    allowed, _ = gov.gate(sv.annotate_items(cases), "read-only-external")
    assert allowed == []


def test_fullwidth_credential_name_is_normalized():
    # NFKC folds fullwidth forms, so they can't be used to dodge the classifier.
    assert sv.classify_field("ＡＰＩ＿ＫＥＹ") == "secret"


def test_digit_seam_credential_spellings_blocked():
    # Codex round-6 §11.3: a digit between letters must not break the word.
    assert sv.classify_field("api2key") == "secret"
    assert sv.classify_field("api2_key") == "secret"
    assert sv.classify_field("apiV2Key") == "secret"


def test_confusable_script_does_not_bypass():
    # Codex round-6 §10.4: ASCII letters mixed with Cyrillic/Greek look-alikes
    # can't be trusted to tokenize — fail closed to (at least) private so an
    # explicit `public` can't leak them.
    from piia_engram import governance as gov
    cyr_m = "м"      # Cyrillic 'м' imitating ASCII 'm'
    cyr_a = "а"      # Cyrillic 'а' imitating ASCII 'a'
    assert sv.classify_field(f"e{cyr_m}ail") in ("private", "secret")
    assert sv.classify_field(f"{cyr_a}pi_key") in ("private", "secret")
    cases = [
        {"sensitivity": "public", f"e{cyr_m}ail": "a@b.c"},
        {"sensitivity": "public", f"{cyr_a}pi_key": "sk"},
    ]
    allowed, _ = gov.gate(sv.annotate_items(cases), "read-only-external")
    assert allowed == []


def test_round6_pure_cjk_field_names_not_over_flagged():
    # The confusable defense must NOT fire on pure-CJK names (no ASCII to
    # imitate), so legitimate Chinese field names stay at the work default.
    # NOTE: 邮箱地址 moved to the CJK PII set in round 7 (now `private` — see
    # test_cjk_pii_fields_are_private); the benign names below stay `work`.
    for f in ["用户名", "技术栈", "备注"]:
        assert sv.classify_field(f) == "work", f


def test_round6_no_false_positives_after_hardening():
    # Codex round-6 §11.4: benign fields must stay un-flagged after the
    # suffix/abbrev/digit-seam hardening (no new over-protection creep).
    for f in ["primary_key", "cache_key", "access_count", "valid_numbers",
              "monkey", "tokens_used", "keynote", "public_key_algorithm",
              "first_name", "user_id"]:
        assert sv.classify_field(f) == "work", f


# ── Codex round-7: CJK (Chinese) semantic PII / credential field names ───────


def test_cjk_pii_fields_are_private():
    # Engram is a Chinese-first product; CJK PII field names must get the same
    # private floor as their English counterparts. They tokenize to nothing, so
    # the ASCII token classifier missed them and they leaked as `work`.
    for f in ["邮箱", "邮箱地址", "电子邮箱", "手机号", "手机号码",
              "电话号码", "身份证号", "真实姓名", "住址"]:
        assert sv.classify_field(f) == "private", f


def test_cjk_credential_fields_are_secret():
    for f in ["密码", "密钥", "秘钥", "令牌", "口令", "凭证", "私钥",
              "访问令牌", "刷新令牌", "客户端密钥"]:
        assert sv.classify_field(f) == "secret", f


def test_cjk_mixed_credential_fields_are_secret():
    # A CJK credential term glued to an ASCII vendor/verb prefix must still be
    # secret — the CJK term wins even when the ASCII fragment alone wouldn't.
    for f in ["api密钥", "access令牌", "openai密钥", "用户密码"]:
        assert sv.classify_field(f) == "secret", f


def test_cjk_benign_fields_stay_work():
    # CJK term matching must not over-flag ordinary Chinese field names.
    for f in ["用户名", "技术栈", "备注", "标题", "摘要", "领域", "角色"]:
        assert sv.classify_field(f) == "work", f


def test_cjk_sensitive_fields_blocked_through_external_gate():
    # Codex round-7 verbatim acceptance: every CJK PII/credential item marked
    # explicitly public must be excluded for a read-only-external agent.
    from piia_engram import governance as gov
    cases = [
        {"sensitivity": "public", "邮箱地址": "a@b.c"},
        {"sensitivity": "public", "手机号": "13800000000"},
        {"sensitivity": "public", "密码": "x"},
        {"sensitivity": "public", "密钥": "x"},
        {"sensitivity": "public", "令牌": "x"},
        {"sensitivity": "public", "api密钥": "sk"},
        {"sensitivity": "public", "access令牌": "t"},
        {"sensitivity": "public", "openai密钥": "sk"},
        {"sensitivity": "public", "profile": {"邮箱地址": "a@b.c"}},
    ]
    allowed, _ = gov.gate(sv.annotate_items(cases), "read-only-external")
    assert allowed == []


# ── Codex round-8: broader CJK PII synonyms + CJK restricted_fields ──────────


def test_cjk_private_synonyms_are_private():
    # Codex round-8 P1: common Chinese PII synonyms beyond the first draft set
    # (电子邮件 = email, 手机 = phone, 证件号/银行卡号/护照号 = identity/financial
    # document numbers) must also floor to private.
    for f in ["电子邮件", "邮件地址", "联系邮箱",
              "手机", "联系电话",
              "证件号", "证件号码",
              "银行卡号", "银行卡号码",
              "护照号", "护照号码"]:
        assert sv.classify_field(f) == "private", f


def test_cjk_private_synonyms_blocked_through_external_gate():
    from piia_engram import governance as gov
    cases = [
        {"sensitivity": "public", "电子邮件": "a@b.c"},
        {"sensitivity": "public", "手机": "13800000000"},
        {"sensitivity": "public", "证件号": "x"},
        {"sensitivity": "public", "银行卡号": "x"},
        {"sensitivity": "public", "护照号": "x"},
    ]
    allowed, _ = gov.gate(sv.annotate_items(cases), "read-only-external")
    assert allowed == []


def test_cjk_restricted_fields_raise_to_private():
    # Codex round-8 P2: a user's explicit pure-CJK restricted_fields must be
    # honored (they tokenized to nothing, so the old code ignored them).
    for f in ["项目代号", "内部计划", "备注"]:
        assert sv.classify_field(f, restricted_fields=[f]) == "private", f


def test_cjk_restricted_fields_blocked_through_external_gate():
    from piia_engram import governance as gov
    restricted = ["项目代号", "内部计划", "备注"]
    cases = [{"sensitivity": "public", f: "x"} for f in restricted]
    allowed, _ = gov.gate(
        sv.annotate_items(cases, restricted_fields=restricted),
        "read-only-external",
    )
    assert allowed == []


def test_cjk_restricted_fields_do_not_overflag_without_restriction():
    # The CJK restricted layer is additive: without restriction these stay work.
    for f in ["项目代号", "内部计划", "备注"]:
        assert sv.classify_field(f) == "work", f


def test_cjk_benign_fields_stay_work_after_synonym_expansion():
    # The broadened CJK PII table must not over-flag ordinary Chinese names.
    for f in ["用户名", "技术栈", "标题", "摘要", "领域", "角色"]:
        assert sv.classify_field(f) == "work", f


# ── Round-9 proactive hardening: CJK ROOTS (self-found adversarial sweep) ─────


def test_cjk_credential_roots_cover_more_secrets():
    # A self-run sweep found these credential synonyms still leaked; the root
    # list (凭据/助记词/验证码/授权码/暗号 + 令牌/密码/私钥 roots) now covers them.
    for f in ["凭据", "助记词", "私钥助记词", "种子短语", "暗号",
              "验证码", "短信验证码", "授权码",
              "会话令牌", "钱包私钥", "登录密码", "支付密码"]:
        assert sv.classify_field(f) == "secret", f


def test_cjk_pii_roots_cover_more_pii():
    for f in ["微信", "微信号", "联系方式",
              "社保号", "医保卡号", "学号", "工号", "车牌号",
              "银行账号", "信用卡号", "储蓄卡号", "卡号", "支付宝账号",
              "工资", "薪资", "收入", "余额", "公积金",
              "籍贯", "民族", "国籍", "生日", "出生日期"]:
        assert sv.classify_field(f) == "private", f


def test_cjk_public_key_is_not_secret():
    # a *public* key is not a secret — the root list deliberately omits bare 钥.
    assert sv.classify_field("公钥") == "work"


def test_cjk_generic_account_word_not_overflagged():
    # bare 账号/账户 are too generic (用户账号 = a username) — left at work on
    # purpose; the specific 银行账号 is the one that floors to private.
    for f in ["账号", "账户", "用户账号"]:
        assert sv.classify_field(f) == "work", f
    assert sv.classify_field("银行账号") == "private"


def test_cjk_roots_blocked_through_external_gate():
    from piia_engram import governance as gov
    cases = [{"sensitivity": "public", k: "x"} for k in
             ["微信号", "验证码", "助记词", "银行账号", "社保号", "工资", "生日", "国籍"]]
    allowed, _ = gov.gate(sv.annotate_items(cases), "read-only-external")
    assert allowed == []


def test_annotate_then_gate_blocks_secret_field_item():
    # end-to-end leak regression through annotate -> gate
    from piia_engram import governance as gov
    items = [{"id": "ok", "summary": "fine", "sensitivity": "public"},
             {"id": "leak", "sensitivity": "public", "access_token": "t"}]
    allowed, _ = gov.gate(sv.annotate_items(items), "read-only-external")
    assert {i["id"] for i in allowed} == {"ok"}  # the token item must be blocked


def test_item_defaults_to_work():
    assert sv.classify_item({"id": "1", "summary": "x"}) == "work"

def test_item_honors_explicit_valid_level():
    assert sv.classify_item({"sensitivity": "public"}) == "public"
    assert sv.classify_item({"sensitivity": "secret"}) == "secret"

def test_item_invalid_level_falls_back_to_work():
    assert sv.classify_item({"sensitivity": "ultra"}) == "work"

def test_non_dict_item_is_work():
    assert sv.classify_item(None) == "work"


# ── annotate_items feeds the gate ────────────────────────────────────────

def test_annotate_sets_sensitivity_and_preserves_nondict():
    items = [{"id": "1", "summary": "a"}, None, {"id": "2", "sensitivity": "public"}]
    out = sv.annotate_items(items)
    assert out[0]["sensitivity"] == "work"
    assert out[1] is None
    assert out[2]["sensitivity"] == "public"
    # originals not mutated
    assert "sensitivity" not in items[0]


def test_annotate_then_gate_blocks_pii_for_external(monkeypatch):
    # end-to-end: an item marked private (e.g. derived from a PII field) must
    # not reach a read-only-external agent after annotate→gate.
    from piia_engram import governance as gov
    items = [{"id": "pub", "sensitivity": "public"}, {"id": "sec", "sensitivity": "private"}]
    allowed, _ = gov.gate(sv.annotate_items(items), "read-only-external")
    assert {i["id"] for i in allowed} == {"pub"}


# ── Layer 2: language-independent VALUE scanner (round-10 two-layer defense) ──
# A field-NAME classifier (in ANY language) cannot catch a benign-named field
# that holds a sensitive VALUE. classify_value inspects the value itself for
# high-confidence credential shapes (-> secret) and PII shapes (-> private),
# and classify_item floors the item on values found at ANY depth.

def test_value_scanner_detects_credentials_as_secret():
    # well-known credential token shapes, regardless of field name / language
    creds = [
        "sk-proj-abcdefghijklmnop1234",          # OpenAI project key
        "sk-abcdefghijklmnop1234567890",          # OpenAI key
        "sk_live_abcdefghij1234567890",           # Stripe secret key
        "rk_test_abcdefghij1234567890",           # Stripe restricted key
        "ghp_0123456789abcdefghij0123456789",     # GitHub PAT
        "gho_0123456789abcdefghij0123456789",     # GitHub OAuth
        "github_pat_0123456789abcdefghij0123",    # GitHub fine-grained PAT
        "AKIAIOSFODNN7EXAMPLE",                    # AWS access key id
        "ASIAIOSFODNN7EXAMPLE",                    # AWS temp access key id
        "AIzaSyA1234567890abcdefghijklmnopqrstuvw",  # Google API key (39 chars)
        "ya29.a0AbCdEfGhIjKlMnOpQrStUvWxYz",       # Google OAuth token
        "xoxb-1234567890-abcdefghijkl",            # Slack bot token
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmN",  # JWT
        "-----BEGIN RSA PRIVATE KEY-----",         # PEM block
    ]
    for c in creds:
        assert sv.classify_value(c) == "secret", c

def test_value_scanner_detects_pii_as_private():
    pii = [
        "alice@example.com",          # email
        "13800138000",                # CN mobile
        "11010519491231002X",         # CN resident ID (valid ISO 7064 checksum)
        "110101199003078515",         # CN resident ID (valid checksum)
        "4111111111111111",           # Visa test card (Luhn-valid)
        "4242424242424242",           # Visa test card (Luhn-valid)
    ]
    for p in pii:
        assert sv.classify_value(p) == "private", p

def test_value_scanner_ignores_benign_values():
    benign = [
        "hello world", "just a normal note", "v4.2.1", "2026-05-29",
        "ENGRAM_GOVERNANCE", "technical_level=expert",
        "1234567890123",              # 13 digits but Luhn-invalid -> not a card
        "abc",                        # too short for anything
        "", "   ",                    # empty / whitespace
        42, True, False, 3.14, None, ["a", "b"], {"k": "v"},
    ]
    for b in benign:
        assert sv.classify_value(b) == "public", repr(b)

def test_value_scanner_does_not_floor_long_freetext_on_incidental_pii():
    # a long lesson body that merely MENTIONS a contact must stay content
    # (not floored), so only short "field-like" values trip the PII floor.
    long_body = (
        "In our retro we agreed the team should email the vendor at "
        "sales-team@bigcorp.example to confirm delivery for next quarter, "
        "and to follow up on the outstanding invoices from last month too."
    )
    assert len(long_body) > sv._PII_SHORT_MAXLEN
    assert sv.classify_value(long_body) == "public"
    # but a whole-value email of ANY length is still PII
    assert sv.classify_value("a-very-long-but-still-just-an-address@example.com") == "private"

def test_benign_named_field_with_secret_value_is_caught_LEAK_REGRESSION():
    # THE gap a name-only classifier can never close: innocuous field name,
    # explicit public, but the VALUE is a live credential -> must be secret.
    cases = [
        {"sensitivity": "public", "备注": "sk-proj-abcdefghijklmnop1234"},
        {"sensitivity": "public", "note": "ghp_0123456789abcdefghij0123456789"},
        {"sensitivity": "public", "comment": "-----BEGIN OPENSSH PRIVATE KEY-----"},
    ]
    for item in cases:
        assert sv.classify_item(item) == "secret", item

def test_benign_named_field_with_pii_value_floors_to_private():
    cases = [
        {"sensitivity": "public", "note": "alice@example.com"},
        {"sensitivity": "public", "备注": "13800138000"},
        {"sensitivity": "public", "contact": "11010519491231002X"},
        {"sensitivity": "public", "phone": 13800138000},  # int value
    ]
    for item in cases:
        assert sv.classify_item(item) == "private", item

def test_value_scanner_floors_item_at_any_depth():
    # a secret value buried deep in lists/dicts still floors the whole item
    item = {"sensitivity": "public",
            "data": {"steps": [{"ok": "fine"},
                               {"payload": ["x", {"deep": "ghp_0123456789abcdefghij0123456789"}]}]}}
    assert sv.classify_item(item) == "secret"

def test_value_scanner_blocked_through_external_gate():
    # end-to-end: a benign-named item whose VALUE is a credential/PII must not
    # reach a read-only-external agent after annotate→gate.
    from piia_engram import governance as gov
    items = [
        {"id": "ok", "summary": "nothing sensitive here", "sensitivity": "public"},
        {"id": "key", "sensitivity": "public", "note": "sk-proj-abcdefghijklmnop1234"},
        {"id": "mail", "sensitivity": "public", "note": "alice@example.com"},
    ]
    allowed, _ = gov.gate(sv.annotate_items(items), "read-only-external")
    assert {i["id"] for i in allowed} == {"ok"}

def test_value_scanner_does_not_overflag_a_normal_lesson():
    # a realistic benign lesson item stays at its explicit/default level
    item = {"id": "L1", "kind": "lesson", "sensitivity": "public",
            "title": "Prefer composition over inheritance",
            "body": "When a class hierarchy grows fragile, switch to composition. "
                    "See the strategy pattern for an example, version v4.2.1."}
    assert sv.classify_item(item) == "public"


# ── round-10 behavior LOCKS (per round-9 reviewer §9.3) ──────────────────────
# These pin down deliberate conservative-promotion behavior so that a future
# "let's reduce false positives" change trips a test and is recognized as a
# GOVERNANCE POLICY CHANGE rather than a silent refactor.

def test_cjk_root_overpromotion_locked_round10():
    # Round-10 broadened whole-word CJK terms to high-coverage ROOTS, which
    # intentionally over-promotes some non-private-context fields. This is
    # accepted fail-closed behavior for a governance floor, NOT a bug. Note
    # 护照有效期 is private under round-10 (护照 root) — a DELIBERATE change from
    # round-9 (dd6c90f), where 护照 was only a whole word and it was work.
    assert sv.classify_field("密码学") == "secret"        # 密码 root
    assert sv.classify_field("密钥长度") == "secret"      # 密钥 root
    assert sv.classify_field("手机型号") == "private"     # 手机 root
    assert sv.classify_field("电话会议") == "private"     # 电话 root
    assert sv.classify_field("护照有效期") == "private"   # 护照 root (round-10 change)
    assert sv.classify_field("身份证照片") == "private"   # 身份证 root

def test_cjk_pii_derived_fields_are_private_round10():
    for f in ["电子邮件地址", "身份证照片", "银行卡号码", "护照号码"]:
        assert sv.classify_field(f) == "private", f

def test_cjk_benign_boundary_fields_stay_work_round10():
    # roots are deliberately NOT over-broad: these must NOT be flagged.
    for f in ["银行名称", "邮件内容", "用户名", "技术栈"]:
        assert sv.classify_field(f) == "work", f

def test_value_scanner_benign_shapes_stay_public_round10():
    # high-entropy / numeric shapes that must NOT trip the value scanner.
    for v in [
        "v4.2.1", "4.2.1-rc.1", "2026-05-29",
        "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",          # 32-hex (commit/md5-like)
        "550e8400-e29b-41d4-a716-446655440000",       # UUID
        "dGhpcyBpcyBhIHRlc3Qgc3RyaW5n",               # base64 text
        "model=gpt-4-0613-preview",
        "1234567890123456",                            # 16 digits, Luhn-invalid
        "12345678901",                                 # 11 digits, not 1[3-9]…
        "370101199001011234",                          # 18 digits, bad CN-ID checksum
    ]:
        assert sv.classify_value(v) == "public", v

def test_value_scanner_conservative_promotions_locked_round10():
    # documented conservative promotions (fail-closed, accepted over-protection):
    # any value shaped like an OpenAI key (sk- + 16-char body) -> secret, and a
    # short "field-like" value that embeds a real phone/card -> private.
    assert sv.classify_value("sk-skip-this-not-a-real-key") == "secret"
    assert sv.classify_value("order-2024-0001-13800138000") == "private"


# ── Codex round-10 FAIL fixes: dict-key-as-value + formatted PII ─────────────

def test_value_scanner_scans_sensitive_dict_keys_round10_regression():
    # P1: a dict KEY that is itself a live secret/PII value is visible text and
    # must floor the item (it used to leak as public — key was only a name).
    cases = [
        ({"sensitivity": "public", "tokens": {"sk-proj-abcdefghijklmnop1234": True}}, "secret"),
        ({"sensitivity": "public", "tokens": {"ghp_0123456789abcdefghij0123456789": "enabled"}}, "secret"),
        ({"sensitivity": "public", "contacts": {"alice@example.com": "owner"}}, "private"),
        ({"sensitivity": "public", "contacts": {"13800138000": "owner"}}, "private"),
    ]
    for item, expected in cases:
        assert sv.classify_item(item) == expected, item

def test_sensitive_dict_keys_blocked_through_external_gate_round10_regression():
    from piia_engram import governance as gov
    items = [
        {"id": "ok", "sensitivity": "public", "summary": "normal"},
        {"id": "leak", "sensitivity": "public", "tokens": {"sk-proj-abcdefghijklmnop1234": True}},
    ]
    allowed, _ = gov.gate(sv.annotate_items(items), "read-only-external")
    assert {i["id"] for i in allowed} == {"ok"}

def test_value_scanner_detects_formatted_cn_phone_and_cards_round10_regression():
    # P1: phone/card written with spaces / hyphens / country code.
    for v in [
        "138-0013-8000", "138 0013 8000",
        "+86 138 0013 8000", "86-138-0013-8000",
        "4111 1111 1111 1111", "4111-1111-1111-1111",
    ]:
        assert sv.classify_value(v) == "private", v

def test_formatted_pii_values_blocked_through_external_gate_round10_regression():
    from piia_engram import governance as gov
    items = [
        {"id": "phone", "sensitivity": "public", "note": "+86 138 0013 8000"},
        {"id": "card", "sensitivity": "public", "note": "4111 1111 1111 1111"},
    ]
    allowed, _ = gov.gate(sv.annotate_items(items), "read-only-external")
    assert allowed == []

def test_formatted_number_false_positive_protection_round10():
    # normalization widens FORMAT, not confidence: formatted numbers that are
    # NOT a valid card (Luhn) / mobile / ID must still be public.
    for v in [
        "1234 5678 9012 3456",   # 16 digits, Luhn-invalid -> not a card
        "order-2024-0001",
        "2024-0001-0002",
        "2026-05-29", "2026 05 29",
        "192.168.1.100",
        "2026-05-29 13:00:00",
    ]:
        assert sv.classify_value(v) == "public", v

def test_value_scanner_detects_gitlab_and_slack_app_tokens_round10():
    assert sv.classify_value("glpat-1234567890abcdefghij") == "secret"
    assert sv.classify_value("xapp-1-A1234567890-abcdefghij") == "secret"


# ── Codex round-11 FAIL regressions: value-side Unicode hygiene + CN-ID X ──

def test_value_scanner_normalizes_unicode_values_round11_regression():
    # Fullwidth digits (common in a CJK-first product) and zero-width
    # insertions must NOT slip a credential/PII shape past the value scanner.
    assert sv.classify_value("１３８００１３８０００") == "private"  # １３８００１３８０００
    assert sv.classify_value("４１１１ １１１１ １１１１ １１１１") == "private"  # ４１１１ … (Luhn-valid card)
    assert sv.classify_value("sk-proj-abcdefghij​klmnop1234") == "secret"  # zero-width inside token

def test_unicode_value_bypasses_blocked_through_external_gate_round11_regression():
    from piia_engram import governance as gov
    items = [
        {"id": "phone", "sensitivity": "public", "note": "１３８００１３８０００"},
        {"id": "card", "sensitivity": "public", "note": "４１１１ １１１１ １１１１ １１１１"},
        {"id": "key", "sensitivity": "public", "note": "sk-proj-abcdefghij​klmnop1234"},
    ]
    allowed, _ = gov.gate(sv.annotate_items(items), "read-only-external")
    assert allowed == []

def test_value_scanner_detects_formatted_cn_id_with_x_round11_regression():
    # CN resident-ID ISO 7064 check digit can be X — formatted form must still
    # floor to private (contiguous form already did).
    assert sv.classify_value("110105 19491231 002X") == "private"
    assert sv.classify_value("110105-19491231-002X") == "private"
    assert sv.classify_value("11010519491231002X") == "private"  # contiguous baseline

def test_sensitive_unicode_dict_keys_blocked_round11_regression():
    # A dict KEY is visible text too; fullwidth / zero-width keys must be caught.
    assert sv.classify_item({"sensitivity": "public", "tokens": {"sk-proj-abcdefghij​klmnop1234": True}}) == "secret"
    assert sv.classify_item({"sensitivity": "public", "phones": {"１３８００１３８０００": "owner"}}) == "private"

def test_unicode_normalization_false_positive_guards_round11():
    # Normalization must not over-promote: fullwidth dates / versions /
    # Luhn-invalid cards still public after NFKC folding.
    for v in [
        "２０２６－０５－２９",  # ２０２６－０５－２９
        "ｖ４．２．１",                          # ｖ４．２．１
        "１２３４ ５６７８ ９０１２ ３４５６",  # １２３４ … Luhn-invalid
    ]:
        assert sv.classify_value(v) == "public", v


# ── Codex round-12 FAIL regressions: greedy-run multi-PII windowing ──
# A greedy FORMATTED candidate that swallows SEVERAL formatted PII used to
# compact into one over-long run, fail whole-candidate validation, and leak the
# inner valid PII as public. The fix slides consecutive separator-delimited
# group windows so each true PII still surfaces.

def test_value_scanner_detects_multiple_formatted_cards_round12_regression():
    # Two Luhn-valid cards packed into one field (4111… + 4242…).
    assert sv.classify_value("4111 1111 1111 1111 4242 4242 4242 4242") == "private"
    assert sv.classify_value("4111-1111-1111-1111-4242-4242-4242-4242") == "private"

def test_value_scanner_detects_multiple_formatted_cn_phones_round12_regression():
    # Two valid CN mobiles back to back.
    assert sv.classify_value("138 0013 8000 139 0013 8000") == "private"
    assert sv.classify_value("138-0013-8000 139-0013-8000") == "private"

def test_value_scanner_detects_multiple_formatted_cn_ids_round12_regression():
    # Two valid CN resident IDs (first ends in X) back to back.
    assert sv.classify_value("110105 19491231 002X 110101 19900307 8515") == "private"

def test_multiple_formatted_pii_values_blocked_through_external_gate_round12_regression():
    from piia_engram import governance as gov
    items = [
        {"id": "cards", "sensitivity": "public", "note": "4111 1111 1111 1111 4242 4242 4242 4242"},
        {"id": "phones", "sensitivity": "public", "note": "138 0013 8000 139 0013 8000"},
        {"id": "ids", "sensitivity": "public", "note": "110105 19491231 002X 110101 19900307 8515"},
    ]
    allowed, _ = gov.gate(sv.annotate_items(items), "read-only-external")
    assert allowed == []

def test_multiple_formatted_pii_dict_keys_blocked_round12_regression():
    # A dict KEY carrying multiple formatted PII is visible text too.
    assert sv.classify_item({"sensitivity": "public", "x": {"4111 1111 1111 1111 4242 4242 4242 4242": 1}}) == "private"

def test_multiple_formatted_pii_false_positive_guards_round12():
    # Window scanning must not invent PII out of benign grouped digits.
    for v in [
        "SKU-1234-5678-9012",          # 12 digits across groups, no valid window
        "SKU-1234-5678-X",             # trailing X, no valid window
        "ISBN 978-0-306-40615-7",      # Luhn-invalid 13-digit run
        "2026-05-29 13:00:00",         # date + time
        "order-2024-0001 order-2024-0002",
    ]:
        assert sv.classify_value(v) == "public", v


# ── Codex round-13 FAIL regressions: presentation-separator allowlist ──
# The SAME card/phone/CN-ID must not flip private->public just by swapping the
# visible separator. Discovery + split share one allowlist: whitespace (incl.
# tab/newline and NFKC-folded fullwidth/NBSP/figure/narrow spaces), hyphen, dot,
# slash, and the middle-dot family (U+00B7 / U+2027 / U+30FB; NFKC folds
# fullwidth dot U+FF0E / slash U+FF0F to ASCII and halfwidth middot U+FF65 to
# U+30FB). The Luhn / ISO 7064 / 1[3-9]\d{9} validators stay the confidence gate.

def test_value_scanner_detects_dot_slash_middot_formatted_cards_round13_regression():
    for v in [
        "4111.1111.1111.1111",
        "4111/1111/1111/1111",
        "4111·1111·1111·1111",   # MIDDLE DOT
        "4111‧1111‧1111‧1111",   # HYPHENATION POINT
        "4111・1111・1111・1111",   # KATAKANA MIDDLE DOT
        "4111．1111．1111．1111",   # fullwidth dot -> NFKC '.'
        "4111／1111／1111／1111",   # fullwidth slash -> NFKC '/'
        "4111\t1111\t1111\t1111",               # tab
        "4111\n1111\n1111\n1111",               # newline
        "4111 1111/1111 1111",                  # mixed space + slash
    ]:
        assert sv.classify_value(v) == "private", repr(v)

def test_value_scanner_detects_dot_slash_middot_formatted_cn_phones_round13_regression():
    for v in [
        "138.0013.8000",
        "138/0013/8000",
        "138·0013·8000",
        "138.0013-8000",                        # mixed dot + hyphen
        "138．0013．8000",              # fullwidth dot
    ]:
        assert sv.classify_value(v) == "private", repr(v)

def test_value_scanner_detects_dot_slash_middot_formatted_cn_ids_round13_regression():
    for v in [
        "110105.19491231.002X",
        "110105/19491231/002X",
        "110105·19491231·002X",
        "110105.19491231-002X",                 # mixed
    ]:
        assert sv.classify_value(v) == "private", repr(v)

def test_formatted_pii_separator_variants_blocked_through_external_gate_round13_regression():
    from piia_engram import governance as gov
    items = [
        {"id": "card_dot", "sensitivity": "public", "note": "4111.1111.1111.1111"},
        {"id": "card_slash", "sensitivity": "public", "note": "4111/1111/1111/1111"},
        {"id": "phone_dot", "sensitivity": "public", "note": "138.0013.8000"},
        {"id": "phone_middot", "sensitivity": "public", "note": "138·0013·8000"},
        {"id": "id_slash", "sensitivity": "public", "note": "110105/19491231/002X"},
    ]
    allowed, _ = gov.gate(sv.annotate_items(items), "read-only-external")
    assert allowed == []

def test_formatted_pii_separator_variants_dict_keys_blocked_round13_regression():
    # A dict KEY written with dot/slash separators is visible text too.
    assert sv.classify_item({"sensitivity": "public", "x": {"4111.1111.1111.1111": 1}}) == "private"
    assert sv.classify_item({"sensitivity": "public", "x": {"138/0013/8000": 1}}) == "private"

def test_formatted_pii_separator_false_positive_guards_round13():
    # The separator allowlist must not over-promote benign dotted/slashed numbers.
    for v in [
        "SKU.1234.5678.9012",
        "SKU/1234/5678/9012",
        "ISBN 978-0-306-40615-7",
        "2026/05/29 13:00:00",
        "2026.05.29 13:00:00",
        "2026.05.29",
        "192.168.1.100",
        "255.255.255.0",
        "10.20.30.40:8080",
        "31.2304 121.4737",          # geo coordinates
        "v4.2.1",
        "1234.5678.9012.3456",       # Luhn-invalid 16
        # deliberately EXCLUDED separators stay public (tracked known limitation)
        "4111,1111,1111,1111",
        "4111;1111;1111;1111",
        "4111_1111_1111_1111",
        "4111:1111:1111:1111",
    ]:
        assert sv.classify_value(v) == "public", repr(v)

def test_contiguous_no_separator_blob_is_known_limitation_round13():
    # Documented known limitation (NOT a silent gap): a long no-separator digit
    # blob is NOT sliced into card-sized windows (would balloon Luhn false
    # positives over order numbers / hashes / serials). Left public on purpose.
    assert sv.classify_value("41111111111111114242424242424242") == "public"
