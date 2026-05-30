"""Detect and repair high-confidence mojibake in Engram text fields.

The storage layer already reads/writes UTF-8. This module handles the other
failure mode: a client decoded UTF-8 bytes with the wrong codec before calling
Engram, so the persisted Python string is already damaged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import shutil
from typing import Any

from .storage import _write_json


_COMMON_CJK_TERMS = (
    "\u53d1\u5e03",  # release/publish
    "\u6d41\u7a0b",
    "\u6d4b\u8bd5",
    "\u6cbb\u7406",
    "\u6743\u9650",
    "\u7f16\u7801",
    "\u4e71\u7801",
    "\u5de5\u5177",
    "\u9879\u76ee",
    "\u7ecf\u9a8c",
    "\u51b3\u7b56",
    "\u8bb0\u5fc6",
    "\u7528\u6237",
    "\u4efb\u52a1",
    "\u6587\u4ef6",
    "\u5199\u5165",
    "\u8bfb\u53d6",
    "\u68c0\u67e5",
    "\u81ea\u52a8",
    "\u914d\u7f6e",
    "\u7248\u672c",
    "\u65f6\u95f4",
    "\u95ee\u9898",
    "\u8fd4\u56de",
    "\u4fdd\u5b58",
    "\u4e2d\u6587",
    "\u5185\u5bb9",
)

_SUSPICIOUS_CHARS = frozenset(
    "\u9359\u621d\u7af7\u5a34\u4f7a\u25bc\u5b2d\u762f\u5a0c"
    "\u8364\u608a\u93c9\u51ae\u6aba\u7f02\u682b\u721c\u6d94"
    "\u8fa9\u5bb8\u30e5\u53ff\u6924\u572d\u6d30\u7f01\u5fdb"
    "\u7359\u9350\u5d07\u74e5\u7481\u677f\u7e42\u9422\u3126"
    "\u57db\u6d60\u8bf2\u59df\u5bee\u20ac\u950b\u951b\u9286\u9225\u920b"
    "\u9428\u9359\u940e\u95b0\u6b91\u6b22"
)

_SKIP_KEYS = {
    "_score",
    "access_count",
    "checksum",
    "ciphertext",
    "created_at",
    "hash",
    "id",
    "last_reviewed",
    "last_updated",
    "nonce",
    "path",
    "project_folder",
    "related_ids",
    "salt",
    "source_url",
    "timestamp",
    "updated_at",
    "url",
    "version",
}

_TEXT_SUFFIXES = {".json", ".jsonl", ".md", ".txt"}


def _build_markers() -> tuple[str, ...]:
    markers: set[str] = set()
    for term in _COMMON_CJK_TERMS:
        for codec in ("gbk", "cp936", "latin1", "cp1252"):
            try:
                damaged = term.encode("utf-8").decode(codec)
            except UnicodeError:
                continue
            if damaged != term and "?" not in damaged and "\ufffd" not in damaged:
                markers.add(damaged)
    return tuple(sorted(markers, key=len, reverse=True))


_STRONG_MARKERS = _build_markers()


@dataclass(frozen=True)
class TextRepair:
    text: str
    changed: bool
    reason: str = ""
    original: str = ""


@dataclass(frozen=True)
class TextChange:
    path: str
    original: str
    repaired: str
    reason: str


@dataclass(frozen=True)
class EncodingFinding:
    relative_path: Path
    json_path: str
    original: str
    repaired: str
    reason: str
    repairable: bool


@dataclass(frozen=True)
class EncodingScanReport:
    root: Path
    findings: list[EncodingFinding]

    @property
    def repairable_count(self) -> int:
        return sum(1 for f in self.findings if f.repairable)

    @property
    def suspect_count(self) -> int:
        return sum(1 for f in self.findings if not f.repairable)


@dataclass(frozen=True)
class EncodingRepairReport:
    root: Path
    findings: list[EncodingFinding]
    changed_files: list[Path]
    backup_dir: Path | None
    applied: bool

    @property
    def repairable_count(self) -> int:
        return sum(1 for f in self.findings if f.repairable)

    @property
    def suspect_count(self) -> int:
        return sum(1 for f in self.findings if not f.repairable)


def _mojibake_score(text: str) -> int:
    if not text:
        return 0
    score = 0
    for marker in _STRONG_MARKERS:
        score += text.count(marker) * max(4, len(marker))
    score += sum(2 for ch in text if ch in _SUSPICIOUS_CHARS)
    score += text.count("\ufffd") * 3
    return score


def _has_strong_marker(text: str) -> bool:
    return any(marker in text for marker in _STRONG_MARKERS)


def _looks_repairable(text: str) -> bool:
    if not text:
        return False
    if _has_strong_marker(text):
        return True
    suspicious = sum(1 for ch in text if ch in _SUSPICIOUS_CHARS)
    has_loss_marker = "?" in text or "\ufffd" in text
    return has_loss_marker and suspicious >= 2


def _candidate_repairs(text: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    for codec, reason in (
        ("gbk", "utf8_as_gbk"),
        ("cp936", "utf8_as_gbk"),
        ("latin1", "utf8_as_latin1"),
        ("cp1252", "utf8_as_cp1252"),
    ):
        try:
            candidate = text.encode(codec).decode("utf-8")
        except UnicodeError:
            continue
        if candidate == text or candidate in seen:
            continue
        seen.add(candidate)
        candidates.append((candidate, reason))
    return candidates


def repair_text(text: str) -> TextRepair:
    """Repair a single string only when the transformation is high-confidence."""
    if not isinstance(text, str) or not text:
        return TextRepair(text=text, changed=False)
    if text.startswith("enc:v"):
        return TextRepair(text=text, changed=False)
    if not _looks_repairable(text):
        return TextRepair(text=text, changed=False)

    original_score = _mojibake_score(text)
    best: tuple[str, str, int] | None = None
    for candidate, reason in _candidate_repairs(text):
        candidate_score = _mojibake_score(candidate)
        if candidate_score >= original_score:
            continue
        if "\ufffd" in candidate:
            continue
        if best is None or candidate_score < best[2]:
            best = (candidate, reason, candidate_score)

    if best is None:
        return TextRepair(text=text, changed=False)
    return TextRepair(text=best[0], changed=True, reason=best[1], original=text)


def normalize_entry_text(value: Any, path: str = "") -> tuple[Any, list[TextChange]]:
    """Return a copy of *value* with repairable text fields normalized."""
    changes: list[TextChange] = []

    def walk(item: Any, item_path: str, key: str | None = None) -> Any:
        if key in _SKIP_KEYS:
            return item
        if isinstance(item, str):
            repaired = repair_text(item)
            if repaired.changed:
                changes.append(
                    TextChange(
                        path=item_path,
                        original=item,
                        repaired=repaired.text,
                        reason=repaired.reason,
                    )
                )
                return repaired.text
            return item
        if isinstance(item, list):
            return [walk(v, f"{item_path}[{i}]") for i, v in enumerate(item)]
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            for k, v in item.items():
                child_path = f"{item_path}.{k}" if item_path else str(k)
                result[k] = walk(v, child_path, str(k))
            return result
        return item

    return walk(value, path), changes


def _iter_text_values(value: Any, path: str = "", key: str | None = None):
    if key in _SKIP_KEYS:
        return
    if isinstance(value, str):
        yield path, value
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_text_values(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for child_key, item in value.items():
            child_path = f"{path}.{child_key}" if path else str(child_key)
            yield from _iter_text_values(item, child_path, str(child_key))


def _iter_json_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        rel_parts = set(path.relative_to(root).parts)
        if "backups" in rel_parts or path.name.startswith("."):
            continue
        if ".corrupt." in path.name:
            continue
        files.append(path)
    return files


def _scan_text_file(root: Path, path: Path) -> tuple[str | None, list[EncodingFinding]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except Exception:
        return None, []

    findings: list[EncodingFinding] = []
    changed = False
    repaired_lines: list[str] = []
    for index, line in enumerate(lines, 1):
        repaired = repair_text(line)
        if repaired.changed:
            changed = True
            findings.append(
                EncodingFinding(
                    relative_path=path.relative_to(root),
                    json_path=f"line {index}",
                    original=line,
                    repaired=repaired.text,
                    reason=repaired.reason,
                    repairable=True,
                )
            )
            repaired_lines.append(repaired.text)
        else:
            if _looks_repairable(line):
                findings.append(
                    EncodingFinding(
                        relative_path=path.relative_to(root),
                        json_path=f"line {index}",
                        original=line,
                        repaired=line,
                        reason="suspect_unrepairable",
                        repairable=False,
                    )
                )
            repaired_lines.append(line)
    return ("".join(repaired_lines) if changed else None), findings


def _scan_file(root: Path, path: Path) -> tuple[Any | None, list[EncodingFinding]]:
    if path.suffix.lower() != ".json":
        return _scan_text_file(root, path)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, []

    normalized, changes = normalize_entry_text(data)
    changed_paths = {change.path for change in changes}
    findings = [
        EncodingFinding(
            relative_path=path.relative_to(root),
            json_path=change.path,
            original=change.original,
            repaired=change.repaired,
            reason=change.reason,
            repairable=True,
        )
        for change in changes
    ]
    for text_path, text in _iter_text_values(data):
        if text_path in changed_paths:
            continue
        if _looks_repairable(text):
            findings.append(
                EncodingFinding(
                    relative_path=path.relative_to(root),
                    json_path=text_path,
                    original=text,
                    repaired=text,
                    reason="suspect_unrepairable",
                    repairable=False,
                )
            )
    return (normalized if changes else None), findings


def scan_engram_root(root: Path | str) -> EncodingScanReport:
    root_path = Path(root).expanduser().resolve()
    findings: list[EncodingFinding] = []
    for path in _iter_json_files(root_path):
        _, file_findings = _scan_file(root_path, path)
        findings.extend(file_findings)
    return EncodingScanReport(root=root_path, findings=findings)


def repair_engram_root(
    root: Path | str,
    *,
    apply: bool = False,
    backup: bool = True,
) -> EncodingRepairReport:
    root_path = Path(root).expanduser().resolve()
    findings: list[EncodingFinding] = []
    changed_files: list[Path] = []
    backup_dir: Path | None = None

    for path in _iter_json_files(root_path):
        normalized, file_findings = _scan_file(root_path, path)
        if not file_findings:
            continue
        findings.extend(file_findings)
        if not apply or normalized is None:
            continue
        if not any(f.repairable for f in file_findings):
            continue
        if backup and backup_dir is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_dir = root_path / "backups" / f"encoding_repair_{stamp}"
        if backup_dir is not None:
            backup_path = backup_dir / path.relative_to(root_path)
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup_path)
        if isinstance(normalized, str):
            path.write_text(normalized, encoding="utf-8")
        else:
            _write_json(path, normalized)
        changed_files.append(path)

    return EncodingRepairReport(
        root=root_path,
        findings=findings,
        changed_files=changed_files,
        backup_dir=backup_dir,
        applied=apply,
    )
