"""Owner-run helpers for validating source-aware freshness anchors.

This module deliberately does not import Engram core or provenance. It may read
the current project filesystem / git config, but it never touches the Engram
store and it is not part of the pure ``compute_freshness`` read path.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from typing import Any
from urllib.parse import urlparse

VALID = "valid"
INVALID = "invalid"
UNKNOWN = "unknown"

_REMOTE_SCP_RE = re.compile(r"^(?:(?P<user>[^@\s]+)@)?(?P<host>[^:\s/\\]+):(?P<path>.+)$")
_DEP_SPLIT_RE = re.compile(r"\s*(?:===|==|~=|!=|<=|>=|<|>)\s*")
_DEP_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+|@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")
_CASE_INSENSITIVE_GIT_HOSTS = {"github.com", "gitlab.com", "bitbucket.org"}
_INDIRECT_REQUIREMENT_PREFIXES = ("-r", "--requirement", "-c", "--constraint")
_UNSUPPORTED_PYPROJECT_TOOL_TABLES = ("poetry", "pdm")


def _strip_git_suffix(path: str) -> str:
    clean = path.strip().strip("/")
    if clean.lower().endswith(".git"):
        clean = clean[:-4]
    return clean.strip("/")


def normalize_git_remote(url: str) -> str | None:
    """Normalize a git remote URL into ``host/path`` project id."""
    if not isinstance(url, str):
        return None
    text = url.strip()
    if not text or any(ch.isspace() for ch in text):
        return None

    match = None if "://" in text else _REMOTE_SCP_RE.match(text)
    if match:
        host = match.group("host").strip().lower()
        path = _strip_git_suffix(match.group("path"))
        if host in _CASE_INSENSITIVE_GIT_HOSTS:
            path = path.lower()
        if host and path and "/" in path and "\\" not in path:
            return f"{host}/{path}"
        return None

    parsed = urlparse(text)
    host = (parsed.hostname or "").strip().lower()
    path = _strip_git_suffix(parsed.path or "")
    if host in _CASE_INSENSITIVE_GIT_HOSTS:
        path = path.lower()
    if not host or not path or "/" not in path or "\\" in path:
        return None
    return f"{host}/{path}"


def read_project_id(root: str) -> str | None:
    """Read ``root``'s origin remote through git, including worktree layouts."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    remote = (completed.stdout or "").strip()
    if not remote:
        return None
    return normalize_git_remote(remote)


def parse_anchor_ref(ref: Any) -> dict[str, str] | None:
    """Parse an A.5a raw anchor string into a small typed dict."""
    if not isinstance(ref, str):
        return None
    text = ref.strip()
    if ":" not in text:
        return None
    kind, value = text.split(":", 1)
    kind = kind.strip().lower()
    value = value.strip()
    if kind not in {"dep", "file"} or not value:
        return None
    return {"kind": kind, "ref": value}


def _normalize_dep_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value.strip().lower())


def _extract_requirement_name(value: str) -> str | None:
    text = value.split("#", 1)[0].strip()
    if not text or text.startswith(("-", "http://", "https://", "git+")):
        return None
    if " @ " in text:
        text = text.split(" @ ", 1)[0].strip()
    text = text.split(";", 1)[0].strip()
    text = _DEP_SPLIT_RE.split(text, 1)[0].strip()
    text = text.split("[", 1)[0].strip()
    match = _DEP_NAME_RE.match(text)
    if not match:
        return None
    return _normalize_dep_name(match.group(1))


def _toml_loads(text: str) -> dict[str, Any] | None:
    try:
        import tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore[import-not-found,no-redef]
        except ModuleNotFoundError:
            return None
    try:
        data = tomllib.loads(text)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _dep_in_package_json(path: Path, wanted: str) -> tuple[bool, bool]:
    if not path.is_file():
        return False, False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False, False
    if not isinstance(data, dict):
        return False, False
    for section in (
        "dependencies",
        "devDependencies",
        "peerDependencies",
        "optionalDependencies",
    ):
        deps = data.get(section)
        if not isinstance(deps, dict):
            continue
        if any(_normalize_dep_name(str(name)) == wanted for name in deps):
            return True, True
    return False, True


def _dep_in_requirements(path: Path, wanted: str) -> tuple[bool, bool]:
    if not path.is_file():
        return False, False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return False, False
    indirect = False
    for line in lines:
        clean = line.split("#", 1)[0].strip()
        if clean.startswith(_INDIRECT_REQUIREMENT_PREFIXES):
            indirect = True
            continue
        name = _extract_requirement_name(line)
        if name == wanted:
            return True, True
    if indirect:
        return False, False
    return False, True


def _dep_in_pyproject(path: Path, wanted: str) -> tuple[bool, bool]:
    if not path.is_file():
        return False, False
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return False, False
    data = _toml_loads(text)
    if data is None:
        return False, False
    tool = data.get("tool")
    if isinstance(tool, dict) and any(
        isinstance(tool.get(name), dict)
        for name in _UNSUPPORTED_PYPROJECT_TOOL_TABLES
    ):
        return False, False
    project = data.get("project")
    if not isinstance(project, dict):
        return False, False
    candidates: list[str] = []
    recognized = False
    deps = project.get("dependencies")
    if isinstance(deps, list):
        recognized = True
        candidates.extend(str(dep) for dep in deps)
    optional = project.get("optional-dependencies")
    if isinstance(optional, dict):
        recognized = True
        for values in optional.values():
            if isinstance(values, list):
                candidates.extend(str(dep) for dep in values)
    if not recognized:
        return False, False
    for candidate in candidates:
        if _extract_requirement_name(candidate) == wanted:
            return True, True
    return False, True


def _check_dep_anchor(ref: str, root: Path) -> str:
    wanted = _normalize_dep_name(ref)
    readable_manifest = False
    uncertain_manifest = False

    package_json = root / "package.json"
    if package_json.is_file():
        found, readable = _dep_in_package_json(package_json, wanted)
        if found:
            return VALID
        if readable:
            readable_manifest = True
        else:
            uncertain_manifest = True

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        found, readable = _dep_in_pyproject(pyproject, wanted)
        if found:
            return VALID
        if readable:
            readable_manifest = True
        else:
            uncertain_manifest = True

    for req_path in sorted(root.glob("requirements*.txt")):
        found, readable = _dep_in_requirements(req_path, wanted)
        if found:
            return VALID
        if readable:
            readable_manifest = True
        else:
            uncertain_manifest = True

    if readable_manifest and not uncertain_manifest:
        return INVALID
    return UNKNOWN


def _path_within_root(root: Path, relative: str) -> Path | None:
    raw = Path(relative)
    if raw.is_absolute():
        return None
    try:
        root_resolved = root.resolve()
        candidate = (root / raw).resolve(strict=False)
    except OSError:
        return None
    if candidate == root_resolved or root_resolved in candidate.parents:
        return candidate
    return None


def _check_file_anchor(ref: str, root: Path) -> str:
    candidate = _path_within_root(root, ref)
    if candidate is None:
        return INVALID
    return VALID if candidate.exists() else INVALID


def check_anchor(parsed: dict[str, str] | None, root: str) -> str:
    """Check an already-parsed anchor against a project root."""
    if not isinstance(parsed, dict):
        return UNKNOWN
    kind = parsed.get("kind")
    ref = parsed.get("ref")
    if kind not in {"dep", "file"} or not isinstance(ref, str) or not ref.strip():
        return UNKNOWN
    project_root = Path(root).expanduser()
    if kind == "dep":
        return _check_dep_anchor(ref, project_root)
    return _check_file_anchor(ref, project_root)
