"""Static contract checks for the Cloudflare telemetry worker."""

from pathlib import Path


WORKER_INDEX = Path(__file__).resolve().parents[1] / "worker" / "src" / "index.js"
WORKER_SCHEMA = Path(__file__).resolve().parents[1] / "worker" / "schema.sql"


def test_worker_accepts_and_stores_analysis_contract_v1_fields():
    source = WORKER_INDEX.read_text(encoding="utf-8")
    schema = WORKER_SCHEMA.read_text(encoding="utf-8")

    for field in (
        "prev_version",
        "session_type",
        "install_age_bucket",
        "error_categories",
    ):
        assert f"'{field}'" in source
        assert field in schema


def test_dashboard_copy_uses_daily_id_wording_instead_of_user_count_claims():
    source = WORKER_INDEX.read_text(encoding="utf-8")

    assert "匿名日 ID" in source
    assert "daily_id 按 UTC 日期轮换" in source
    assert "今日活跃安装估算" in source
    assert "近 7 天匿名安装·日" in source
    assert "近 30 天匿名安装·日" in source
    assert "anonymous_daily_id_activity" in source
    assert "anonymous_install_days" in source
    assert "近 7 天活跃安装估算" not in source
    assert "近 30 天活跃安装估算" not in source
    assert "独立用户" not in source
    assert "用户数" not in source
