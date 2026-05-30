# GUI Entry Doctor Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bounded MCP launch diagnostics to `engram doctor` so GUI client configs can be checked after the v3.37.0 `piia-engram-mcp` entry-point release.

**Architecture:** Extend the existing setup wizard doctor flow with small pure helpers that classify `engram` server entries and build safe `--help` probes. Keep the current path validation and `doctor --fix` behavior intact, and report launch probe failures as doctor issues.

**Tech Stack:** Python standard library only (`subprocess`, `Path`), existing `setup_wizard.py`, existing pytest suite.

---

### Task 1: Add Entry Classification Tests

**Files:**
- Modify: `tests/test_setup_wizard.py`

- [ ] **Step 1: Add failing tests for recommended and compatible entries**

Append this test class near `TestWriteMcpConfig` in `tests/test_setup_wizard.py`:

```python
class TestMcpEntryLaunchProbe:
    def test_uvx_entry_is_recommended_and_probeable(self):
        from piia_engram.setup_wizard import _classify_engram_entry

        entry = {
            "command": "uvx",
            "args": ["--from", "piia-engram", "piia-engram-mcp"],
        }

        result = _classify_engram_entry(entry)

        assert result["severity"] == "ok"
        assert result["style"] == "recommended-uvx"
        assert result["probe_argv"] == [
            "uvx", "--from", "piia-engram", "piia-engram-mcp", "--help",
        ]

    def test_console_script_entry_is_recommended_and_probeable(self):
        from piia_engram.setup_wizard import _classify_engram_entry

        result = _classify_engram_entry({"command": "piia-engram-mcp", "args": []})

        assert result["severity"] == "ok"
        assert result["style"] == "recommended-console-script"
        assert result["probe_argv"] == ["piia-engram-mcp", "--help"]

    def test_python_module_entry_is_compatible_and_probeable(self):
        from piia_engram.setup_wizard import _classify_engram_entry

        result = _classify_engram_entry({
            "command": "/usr/bin/python3",
            "args": ["-m", "piia_engram.mcp_server"],
        })

        assert result["severity"] == "ok"
        assert result["style"] == "compatible-python-module"
        assert result["probe_argv"] == [
            "/usr/bin/python3", "-m", "piia_engram.mcp_server", "--help",
        ]
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH='src'
E:\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m pytest tests/test_setup_wizard.py::TestMcpEntryLaunchProbe -q
```

Expected: FAIL because `_classify_engram_entry` does not exist yet.

### Task 2: Implement Entry Classification

**Files:**
- Modify: `src/piia_engram/setup_wizard.py`

- [ ] **Step 1: Add helper functions above `_validate_engram_entry`**

Insert these helpers before `def _validate_engram_entry(...)`:

```python
def _entry_args(entry: dict) -> list[str]:
    """Return entry args as strings; invalid args are represented as an empty list."""
    args = entry.get("args", [])
    if args is None:
        return []
    if not isinstance(args, list):
        return []
    return [str(arg) for arg in args]


def _classify_engram_entry(entry: dict) -> dict:
    """Classify an MCP `engram` entry and build a safe `--help` probe command."""
    command = str(entry.get("command") or "").strip()
    raw_args = entry.get("args", [])
    args = _entry_args(entry)

    if not command:
        return {
            "severity": "error",
            "style": "invalid",
            "message": "MCP entry is missing command",
            "probe_argv": None,
        }
    if raw_args is not None and not isinstance(raw_args, list):
        return {
            "severity": "error",
            "style": "invalid",
            "message": "MCP entry args must be a list",
            "probe_argv": None,
        }

    if command == "uvx":
        if args[:3] == ["--from", "piia-engram", "piia-engram-mcp"]:
            return {
                "severity": "ok",
                "style": "recommended-uvx",
                "message": "Entry point style: recommended uvx zero-install",
                "probe_argv": [command, *args, "--help"],
            }
        return {
            "severity": "warn",
            "style": "uvx-other",
            "message": "uvx entry should use: --from piia-engram piia-engram-mcp",
            "probe_argv": None,
        }

    if command == "piia-engram-mcp":
        return {
            "severity": "ok",
            "style": "recommended-console-script",
            "message": "Entry point style: recommended installed console script",
            "probe_argv": [command, *args, "--help"],
        }

    for index, arg in enumerate(args):
        if arg == "-m" and index + 1 < len(args):
            module_name = args[index + 1]
            if module_name == "piia_engram.mcp_server":
                return {
                    "severity": "ok",
                    "style": "compatible-python-module",
                    "message": "Entry point style: compatible python module",
                    "probe_argv": [command, *args, "--help"],
                }
            if "engram_core" in module_name:
                return {
                    "severity": "warn",
                    "style": "legacy-module",
                    "message": (
                        f"Uses old module name '{module_name}', use "
                        f"'{module_name.replace('engram_core', 'piia_engram')}'"
                    ),
                    "probe_argv": None,
                }

    if any(str(arg).endswith("mcp_server.py") for arg in args):
        return {
            "severity": "warn",
            "style": "legacy-script-path",
            "message": (
                "Uses direct mcp_server.py path; use "
                "[\"-m\", \"piia_engram.mcp_server\"] or piia-engram-mcp"
            ),
            "probe_argv": None,
        }

    return {
        "severity": "warn",
        "style": "unknown",
        "message": "Unknown MCP entry style; expected piia-engram-mcp, uvx, or python -m piia_engram.mcp_server",
        "probe_argv": None,
    }
```

- [ ] **Step 2: Run Task 1 tests to verify GREEN**

Run the same targeted command from Task 1.

Expected: PASS.

### Task 3: Add Probe Execution and Doctor Integration

**Files:**
- Modify: `src/piia_engram/setup_wizard.py`
- Modify: `tests/test_setup_wizard.py`

- [ ] **Step 1: Add probe tests**

Add these tests to `TestMcpEntryLaunchProbe`:

```python
    def test_probe_success_reports_ok(self, monkeypatch):
        from piia_engram.setup_wizard import _probe_mcp_entry

        calls = []

        class Result:
            returncode = 0
            stdout = "Engram MCP Server"
            stderr = ""

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return Result()

        monkeypatch.setattr("piia_engram.setup_wizard.subprocess.run", fake_run)

        result = _probe_mcp_entry({"command": "piia-engram-mcp", "args": []})

        assert result is None
        assert calls[0][0] == ["piia-engram-mcp", "--help"]
        assert calls[0][1]["timeout"] == 5

    def test_probe_nonzero_reports_issue(self, monkeypatch):
        from piia_engram.setup_wizard import _probe_mcp_entry

        class Result:
            returncode = 2
            stdout = ""
            stderr = "bad option"

        monkeypatch.setattr(
            "piia_engram.setup_wizard.subprocess.run",
            lambda *a, **kw: Result(),
        )

        issue = _probe_mcp_entry({"command": "piia-engram-mcp", "args": []})

        assert issue is not None
        assert "exited with code 2" in issue

    def test_probe_timeout_reports_issue(self, monkeypatch):
        from piia_engram.setup_wizard import _probe_mcp_entry

        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=5)

        monkeypatch.setattr("piia_engram.setup_wizard.subprocess.run", fake_run)

        issue = _probe_mcp_entry({"command": "piia-engram-mcp", "args": []})

        assert issue == "MCP launch probe timed out after 5s"
```

- [ ] **Step 2: Add `_probe_mcp_entry` below `_classify_engram_entry`**

```python
def _probe_mcp_entry(entry: dict, *, timeout: int = 5) -> str | None:
    """Run a bounded `--help` probe for safe MCP entry shapes."""
    classification = _classify_engram_entry(entry)
    probe_argv = classification.get("probe_argv")
    if not probe_argv:
        return None
    try:
        result = subprocess.run(
            probe_argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"MCP launch probe timed out after {timeout}s"
    except Exception as exc:
        return f"MCP launch probe failed: {exc}"

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        suffix = f": {detail[0][:160]}" if detail else ""
        return f"MCP launch probe exited with code {result.returncode}{suffix}"
    return None
```

- [ ] **Step 3: Integrate classification and probe into `_validate_engram_entry`**

Inside `_validate_engram_entry`, after retrieving `engram`, add:

```python
    classification = _classify_engram_entry(engram)
    if classification["severity"] in ("warn", "error"):
        issues.append(classification["message"])
    probe_issue = _probe_mcp_entry(engram)
    if probe_issue:
        issues.append(probe_issue)
```

Keep the existing path, module-name, and environment validation below it for compatibility.

- [ ] **Step 4: Run probe tests**

Run:

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH='src'
E:\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m pytest tests/test_setup_wizard.py::TestMcpEntryLaunchProbe -q
```

Expected: PASS.

### Task 4: Add Doctor Output Regression Tests

**Files:**
- Modify: `tests/test_setup_wizard.py`

- [ ] **Step 1: Add doctor regression tests**

Add this test class near existing doctor tests:

```python
class TestDoctorLaunchProbeIntegration:
    def test_doctor_reports_probe_failure(self, tmp_path, monkeypatch, capsys):
        from piia_engram.setup_wizard import run_doctor

        config_path = tmp_path / ".claude" / ".mcp.json"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps({
            "mcpServers": {
                "engram": {"command": "piia-engram-mcp", "args": []},
            }
        }), encoding="utf-8")

        monkeypatch.setattr(
            "piia_engram.setup_wizard._tool_configs",
            lambda: {"claude_code": {"name": "Claude Code", "config_paths": [config_path], "verified": True}},
        )
        monkeypatch.setattr(
            "piia_engram.setup_wizard._probe_mcp_entry",
            lambda entry: "MCP launch probe exited with code 2: bad option",
        )

        result = run_doctor(fix=False)
        out = capsys.readouterr().out

        assert result > 0
        assert "MCP launch probe exited with code 2" in out

    def test_doctor_skips_probe_for_legacy_script_path(self, tmp_path, monkeypatch, capsys):
        from piia_engram.setup_wizard import run_doctor

        config_path = tmp_path / ".claude" / ".mcp.json"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps({
            "mcpServers": {
                "engram": {"command": "python", "args": ["/tmp/mcp_server.py"]},
            }
        }), encoding="utf-8")

        called = False

        def fake_probe(entry):
            nonlocal called
            called = True
            return None

        monkeypatch.setattr(
            "piia_engram.setup_wizard._tool_configs",
            lambda: {"claude_code": {"name": "Claude Code", "config_paths": [config_path], "verified": True}},
        )
        monkeypatch.setattr("piia_engram.setup_wizard._probe_mcp_entry", fake_probe)

        result = run_doctor(fix=False)
        out = capsys.readouterr().out

        assert result > 0
        assert called is False
        assert "direct mcp_server.py path" in out
```

- [ ] **Step 2: Update integration logic if needed**

If the legacy script test shows `fake_probe` was called, change `_validate_engram_entry` so it only calls `_probe_mcp_entry` when `classification["probe_argv"]` exists:

```python
    if classification.get("probe_argv"):
        probe_issue = _probe_mcp_entry(engram)
        if probe_issue:
            issues.append(probe_issue)
```

- [ ] **Step 3: Run doctor tests**

Run:

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH='src'
E:\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m pytest tests/test_setup_wizard.py::TestMcpEntryLaunchProbe tests/test_setup_wizard.py::TestDoctorLaunchProbeIntegration tests/test_setup_wizard.py::TestDoctorFix -q
```

Expected: PASS.

### Task 5: Verification and Commit

**Files:**
- Modify: `src/piia_engram/setup_wizard.py`
- Modify: `tests/test_setup_wizard.py`

- [ ] **Step 1: Run targeted setup wizard suite**

Run:

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH='src'
E:\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m pytest tests/test_setup_wizard.py -q
```

Expected: PASS.

- [ ] **Step 2: Run packaging/publish guards**

Run:

```powershell
$env:PYTHONIOENCODING='utf-8'
E:\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe scripts/check_publish_allowlist.py
E:\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe scripts/release_sanitize_check.py --internal --strict
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 3: Commit implementation**

Run:

```powershell
git add src/piia_engram/setup_wizard.py tests/test_setup_wizard.py
git commit -m "feat(setup): add doctor mcp entry launch probe"
```

Expected: commit succeeds. Do not push without user approval.

## Self-Review

- Spec coverage: The plan implements entry classification, bounded `--help` probing, doctor issue reporting, and targeted tests. It excludes setup wizard redesign and release work.
- Placeholder scan: No placeholder tasks remain; each test and implementation step includes concrete code.
- Type consistency: Helpers consistently accept `dict` entries and return `dict` or `str | None` as described.
