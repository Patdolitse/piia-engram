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
