"""Domain equivalence table for LLM-produced domains."""

from __future__ import annotations


DOMAIN_ALIASES: dict[str, set[str]] = {
    "python": {"python", "py", "cpython"},
    "javascript": {"javascript", "js", "node", "nodejs", "node.js"},
    "database": {"database", "db", "postgresql", "postgres", "sql"},
    "testing": {"testing", "test", "tests", "pytest", "qa"},
    "frontend": {"frontend", "fe", "ui", "web", "browser"},
    "backend": {"backend", "be", "api", "service", "server"},
    "mcp": {"mcp", "tool", "tools", "fastmcp"},
    "product": {"product", "growth", "churn", "subscription"},
    "project_context": {"project_context", "project", "context", "asset", "path", "graph"},
    "devops": {"devops", "ci", "cd", "github actions", "pipeline"},
    "architecture": {"architecture", "arch", "design", "storage"},
    "release": {"release", "packaging", "publish", "distribution"},
    "general": {"general", "misc", "other"},
}


def normalize_domain(domain: str | None) -> str:
    return " ".join(str(domain or "").strip().lower().replace("-", "_").split())


def domain_matches(expected: str | None, actual: str | None) -> bool:
    expected_norm = normalize_domain(expected)
    actual_norm = normalize_domain(actual)
    if not expected_norm:
        return not actual_norm or actual_norm == "null"
    aliases = DOMAIN_ALIASES.get(expected_norm, {expected_norm})
    return actual_norm == expected_norm or actual_norm in aliases
