"""Static checks for remote telemetry dashboard password rotation tooling."""

from pathlib import Path


ROTATE_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "rotate_telemetry_dashboard_password.ps1"
)


def test_dashboard_password_rotation_script_is_owner_handoff_first():
    source = ROTATE_SCRIPT.read_text(encoding="utf-8")

    assert "DASH_PASSWORD" in source
    assert "wrangler secret put DASH_PASSWORD" in source
    assert "Owner handoff" in source
    assert "Dry run only" in source
    assert "RNGCryptoServiceProvider" in source
    assert "<NUL set /p" in source
    assert "^[A-Za-z0-9_-]+$" in source
    assert "hunter2" not in source
