"""Cold-start playbook trigger matching — "playbook finds you".

``get_user_context(user_prompt=...)`` passes the user's first prompt here so
stored playbooks whose ``triggers`` keywords appear in the prompt can surface
in the cold-start context. This inverts the retrieval burden: instead of the
AI having to remember to browse ``get_playbooks``, a relevant playbook
announces itself on the first message.

Design constraints:

- **Pure functions, no I/O.** Candidates are passed in by the caller (the MCP
  layer reads them with ``_update_access=False`` so surfacing never bumps
  usage stats). This keeps matching unit-testable and reusable from CLI /
  hooks later.
- **Precision over recall.** The section competes for first-screen attention,
  so a match must be anchored by at least one explicit trigger-keyword hit;
  title and domain hits only improve ranking, they never qualify a candidate
  on their own.
- **Surfacing is a pointer, not the playbook.** Only title + id + matched
  triggers are rendered; the AI is told to call ``get_playbook`` (whose
  response carries the passive-reference ``usage_policy``) for the steps.
"""

from __future__ import annotations

import re
from typing import Any

# CJK unified ideographs (+ compat) — used to pick substring vs word-boundary
# matching per term. CJK text has no word delimiters, so substring containment
# is correct there; ASCII terms need boundaries ("git" must not hit "digital").
_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")

# Ciphertext marker for corpus-encrypted fields that failed to decrypt
# (see crypto.py). Such candidates are skipped: never surface ciphertext.
_ENC_PREFIX = "enc:"

_TRIGGER_WEIGHT = 3
_TITLE_WEIGHT = 1
_DOMAIN_WEIGHT = 1
_MAX_TITLE_HITS = 2
_MIN_TERM_LEN = 2  # single-character terms match almost anything — noise
_TITLE_DISPLAY_MAX = 80


def _contains_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def _term_in_prompt(term: str, prompt: str, prompt_lower: str) -> bool:
    """True when *term* occurs in *prompt* (CJK: substring; ASCII: word-bounded)."""
    term = term.strip()
    if len(term) < _MIN_TERM_LEN:
        return False
    if _contains_cjk(term):
        return term in prompt
    pattern = (
        r"(?<![0-9A-Za-z_])" + re.escape(term.lower()) + r"(?![0-9A-Za-z_])"
    )
    return re.search(pattern, prompt_lower) is not None


def _title_tokens(title: str) -> list[str]:
    """Split a title into matchable tokens: ASCII words (≥3 chars) + CJK runs (≥2)."""
    tokens: list[str] = list(re.findall(r"[0-9A-Za-z_]{3,}", title))
    tokens.extend(re.findall(r"[\u3400-\u9fff\uf900-\ufaff]{2,}", title))
    return tokens


def _display_title(title: str) -> str:
    """Collapse whitespace/newlines (no heading spoofing) and cap length."""
    flat = " ".join(title.split())
    if len(flat) > _TITLE_DISPLAY_MAX:
        flat = flat[:_TITLE_DISPLAY_MAX].rstrip() + "…"
    return flat


def match_playbooks(
    prompt: str,
    playbooks: list[dict[str, Any]],
    limit: int = 2,
) -> list[dict[str, Any]]:
    """Match *prompt* against playbook trigger keywords; best candidates first.

    Args:
        prompt: The user's current prompt (free text, zh/en mixed OK).
        playbooks: Candidate playbook dicts (``title`` / ``triggers`` /
            ``domain`` / ``steps`` / ``last_reviewed`` are consulted).
        limit: Maximum matches to return.

    Returns:
        List of ``{playbook_id, title, matched_triggers, steps_count, score}``
        sorted by score desc, then ``last_reviewed`` desc. Empty when nothing
        clears the precision bar (≥1 trigger hit).
    """
    prompt = (prompt or "").strip()
    if not prompt or limit <= 0:
        return []
    prompt_lower = prompt.lower()

    scored: list[tuple[int, str, dict[str, Any]]] = []
    for pb in playbooks or []:
        if not isinstance(pb, dict):
            continue
        title = str(pb.get("title") or "")
        if not title or title.startswith(_ENC_PREFIX):
            continue

        triggers = [str(t) for t in (pb.get("triggers") or []) if t]
        hits = [
            t for t in triggers
            if not t.startswith(_ENC_PREFIX)
            and _term_in_prompt(t, prompt, prompt_lower)
        ]
        if not hits:
            continue  # precision anchor: no trigger hit, no surfacing

        score = len(hits) * _TRIGGER_WEIGHT
        title_hits = 0
        for token in _title_tokens(title):
            if _term_in_prompt(token, prompt, prompt_lower):
                title_hits += 1
                if title_hits >= _MAX_TITLE_HITS:
                    break
        score += title_hits * _TITLE_WEIGHT
        for d in str(pb.get("domain") or "").split(","):
            if d.strip() and _term_in_prompt(d.strip(), prompt, prompt_lower):
                score += _DOMAIN_WEIGHT
                break

        steps = pb.get("steps")
        scored.append((
            score,
            str(pb.get("last_reviewed") or ""),
            {
                "playbook_id": str(pb.get("id") or ""),
                "title": _display_title(title),
                "matched_triggers": hits,
                "steps_count": len(steps) if isinstance(steps, list) else 0,
                "score": score,
            },
        ))

    # (score, last_reviewed) tuple with reverse=True gives score desc and,
    # within equal scores, the most recently reviewed playbook first.
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [entry for _, _, entry in scored[:limit]]


def render_matched_section(
    matches: list[dict[str, Any]], lang: str = "zh",
) -> str:
    """Render matches as a markdown section appended to the cold-start context.

    Returns an empty string when there are no matches. The section is a
    pointer list — full steps stay behind ``get_playbook`` so the
    passive-reference ``usage_policy`` always travels with them.
    """
    if not matches:
        return ""
    if lang == "zh":
        lines = [
            "",
            "",
            "## 相关 Playbook（与当前提问匹配）",
            "以下已存操作手册的触发词命中了当前提问。"
            "用 get_playbook(playbook_id) 查看完整步骤；"
            "Playbook 是被动参考——先与用户确认方案，再逐步执行。",
        ]
    else:
        lines = [
            "",
            "",
            "## Matched Playbooks (for the current prompt)",
            "Stored playbooks whose trigger keywords match this prompt. "
            "Call get_playbook(playbook_id) for the full steps; "
            "playbooks are passive references — confirm the plan with the "
            "user before executing step by step.",
        ]
    for m in matches:
        hits = ", ".join(str(t) for t in m.get("matched_triggers", [])[:5])
        steps_count = m.get("steps_count") or 0
        steps_note = ""
        if steps_count:
            steps_note = (
                f" · {steps_count} 步" if lang == "zh"
                else f" · {steps_count} steps"
            )
        hits_label = " · 命中: " if lang == "zh" else " · hits: "
        lines.append(
            f"- {m.get('title', '')} "
            f"(playbook_id: {m.get('playbook_id', '')}"
            f"{hits_label}{hits}{steps_note})"
        )
    return "\n".join(lines)
