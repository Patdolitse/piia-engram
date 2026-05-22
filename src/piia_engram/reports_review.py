"""Engram knowledge review — interactive HTML page for user audit.

Provided as ``ReviewMixin`` so the methods can be composed onto the ``Engram``
class at runtime via ``ReportsMixin``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .storage import _now_iso, _write_json


class ReviewMixin:
    """Knowledge review: interactive HTML page, promote/archive, apply review."""

    def generate_review_page(self, lang: str = "zh") -> str:
        """Generate an interactive HTML page for knowledge review.

        Returns the HTML string with WoW/Diablo rarity colors, star ratings,
        and collapsible items.
        """
        profile = self.get_profile()
        lessons = self.get_lessons(limit=None, _update_access=False)
        decisions = self.get_decisions(limit=None, _update_access=False)

        _esc = lambda s: (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#x27;")

        rarity_order = {"legendary": 0, "epic": 1, "rare": 2, "staging": 3}
        star_map = {"legendary": "★★★", "epic": "★★", "rare": "★", "staging": ""}

        # Classify and sort
        for l_item in lessons:
            l_item["_rarity"] = self.classify_rarity(l_item, "lesson")
        for d_item in decisions:
            d_item["_rarity"] = self.classify_rarity(d_item, "decision")

        lessons.sort(key=lambda x: rarity_order.get(x["_rarity"], 5))
        decisions.sort(key=lambda x: rarity_order.get(x["_rarity"], 5))

        # Group lessons by domain
        domain_groups: dict[str, list[dict]] = {}
        for l_item in lessons:
            dom = l_item.get("domain", "general") or "general"
            domain_groups.setdefault(dom, []).append(l_item)

        zh = lang == "zh"

        # --- Build item HTML ---
        def _item_html(item: dict, itype: str) -> str:
            item_id = item.get("id", "")
            rarity = item.get("_rarity", "staging")
            rtier = self.RARITY_TIERS.get(rarity, self.RARITY_TIERS["staging"])
            color = rtier["color"]
            glow = rtier["glow"]
            lbl = rtier.get(f"label_{lang}", rtier["label_en"])
            stars = star_map.get(rarity, "")
            k_tier = item.get("tier", "verified")
            tier_badge = ""
            if k_tier == "staging":
                tier_badge = (
                    '<span class="tier-badge staging">'
                    + ("暂存" if zh else "Staging")
                    + "</span>"
                )

            if itype == "lesson":
                summary = item.get("summary", "")
                detail = item.get("detail", "")
            else:
                summary = item.get("question", "")
                choice = item.get("choice", "")
                reasoning = item.get("reasoning", "")
                detail = choice
                if reasoning:
                    detail += f"\n\nWhy: {reasoning}"

            ts = (item.get("timestamp") or "")[:10]
            source = _esc(item.get("source_tool", ""))
            domain_val = _esc(item.get("domain", ""))
            eid = _esc(item_id)
            etier = _esc(k_tier)

            return f"""
        <div class="item-card" data-id="{eid}" data-type="{itype}" data-tier="{etier}"
             style="border-left:3px solid {color}; background:{glow}">
          <div class="item-row" onclick="toggleExpand(this)">
            <label class="toggle-box" onclick="event.stopPropagation()">
              <input type="checkbox" checked data-id="{eid}">
              <span class="checkmark"></span>
            </label>
            <span class="stars" style="color:{color}">{stars}</span>
            <span class="rarity-label" style="color:{color}">{lbl}</span>
            {tier_badge}
            <span class="item-summary">{_esc(summary[:120])}</span>
            <span class="expand-arrow">&#9656;</span>
          </div>
          <div class="item-expand">
            {f'<div class="item-detail">{_esc(detail)}</div>' if detail else ''}
            <div class="item-meta">
              <span>{ts}</span>
              {f'<span class="meta-domain">{domain_val}</span>' if domain_val else ''}
              {f'<span class="meta-source">via {source}</span>' if source else ''}
            </div>
          </div>
        </div>"""

        # Lesson cards by domain
        lesson_sections = []
        for domain_name, items in sorted(domain_groups.items()):
            cards = "".join(_item_html(it, "lesson") for it in items)
            rarity_keys = list(self.RARITY_TIERS.keys())
            best_idx = min((rarity_order.get(it["_rarity"], len(rarity_keys) - 1) for it in items), default=len(rarity_keys) - 1)
            best_color = self.RARITY_TIERS[rarity_keys[min(best_idx, len(rarity_keys) - 1)]]["color"]
            lesson_sections.append(
                f'<div class="domain-group">'
                f'<h3 class="domain-title">'
                f'<span class="domain-badge" style="background:{best_color};color:#000">{_esc(domain_name)}</span>'
                f'<span class="domain-count">{len(items)}</span>'
                f'</h3>{cards}</div>'
            )

        decision_cards = "".join(_item_html(d, "decision") for d in decisions)

        # Stats
        total_lessons = len(lessons)
        total_decisions = len(decisions)
        total_domains = len(domain_groups)

        # Rarity distribution
        rarity_counts: dict[str, int] = {}
        for item in lessons + decisions:
            r = item.get("_rarity", "staging")
            rarity_counts[r] = rarity_counts.get(r, 0) + 1

        # Profile summary
        role = profile.get("role", "")
        desc = profile.get("description", "")
        tech_level = profile.get("technical_level", "")
        lang_pref = profile.get("language", "")

        # i18n
        t = {
            "title": "Engram 知识审查" if zh else "Engram Knowledge Review",
            "subtitle": "你的 AI 知识资产 — 审查、保留、归档" if zh else "Your AI knowledge assets — review, keep, archive",
            "profile": "身份画像" if zh else "Identity",
            "lessons": "经验教训" if zh else "Lessons",
            "decisions": "关键决策" if zh else "Decisions",
            "confirm": "确认审查结果" if zh else "Confirm Review",
            "n_lessons": "经验" if zh else "Lessons",
            "n_decisions": "决策" if zh else "Decisions",
            "n_domains": "领域" if zh else "Domains",
            "select_all": "全选" if zh else "Select All",
            "deselect_all": "全不选" if zh else "Deselect All",
            "instructions": "取消勾选 = 归档该条目。点击条目展开详情。确认后生成审查结果文件。" if zh else "Uncheck = archive. Click to expand. Confirm generates review file.",
            "download_done": "审查结果已下载！你也可以复制下方文本粘贴给 AI 工具处理。" if zh else "Review downloaded! Copy text below and paste to AI tool.",
            "legend": "品质图例" if zh else "Quality Legend",
            "show_all": "显示全部" if zh else "Show All",
            "show_staging": "仅显示待审(staging)" if zh else "Staging Only",
        }

        # Rarity legend — only show tiers that have items
        legend_items = ""
        for key, tier in self.RARITY_TIERS.items():
            cnt = rarity_counts.get(key, 0)
            if cnt == 0:
                continue
            lbl = tier.get(f"label_{lang}", tier["label_en"])
            stars = star_map.get(key, "")
            prefix = f"{stars} " if stars else ""
            legend_items += f'<span class="legend-item" style="color:{tier["color"]}">{prefix}{lbl} ({cnt})</span> '

        profile_html = ""
        if role or desc:
            profile_html = f"""
      <div class="section">
        <div class="section-title">{t['profile']}</div>
        <div class="profile-grid">
          <div class="pf"><b>Role:</b> {_esc(role)}</div>
          <div class="pf"><b>Level:</b> {_esc(tech_level)}</div>
          <div class="pf"><b>Language:</b> {_esc(lang_pref)}</div>
          <div class="pf"><b>Description:</b> {_esc(desc)}</div>
        </div>
      </div>"""

        return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{t['title']}</title>
<style>
  :root {{
    --bg: #0a0a0f; --surface: #13141d; --surface2: #1b1d2a;
    --border: #252840; --text: #e4e4e7; --text2: #8b8fa3;
    --accent: #6366f1; --radius: 10px;
  }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.6; min-height: 100vh;
  }}
  .container {{ max-width: 900px; margin: 0 auto; padding: 2rem 1.5rem; }}
  h1 {{ font-size: 1.7rem; background: linear-gradient(135deg, #ff8000, #a335ee);
       -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
  .subtitle {{ color: var(--text2); font-size: .9rem; margin-bottom: 1.5rem; }}

  .stats {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: .75rem; margin-bottom: 1.25rem; }}
  .stat {{ background: var(--surface); border: 1px solid var(--border);
           border-radius: var(--radius); padding: .75rem; text-align: center; }}
  .stat-val {{ font-size: 1.6rem; font-weight: 700; color: #a335ee; }}
  .stat-lbl {{ font-size: .75rem; color: var(--text2); text-transform: uppercase; }}

  .legend {{ background: var(--surface); border: 1px solid var(--border);
             border-radius: var(--radius); padding: .75rem 1rem; margin-bottom: 1.25rem;
             display: flex; flex-wrap: wrap; gap: .75rem; align-items: center; }}
  .legend-title {{ font-size: .8rem; color: var(--text2); margin-right: .5rem; }}
  .legend-item {{ font-size: .8rem; white-space: nowrap; }}
  .filtered-hidden {{ display: none !important; }}

  .instructions {{ background: var(--surface2); padding: .6rem 1rem; border-radius: 8px;
                   color: var(--text2); font-size: .82rem; margin-bottom: 1.25rem; }}

  .section {{ background: var(--surface); border: 1px solid var(--border);
              border-radius: var(--radius); padding: 1.25rem; margin-bottom: 1.25rem; }}
  .section-title {{ font-size: 1.1rem; font-weight: 600; margin-bottom: .75rem; }}

  .profile-grid {{ display: grid; gap: .4rem; }}
  .pf {{ padding: .4rem .6rem; background: var(--surface2); border-radius: 6px; font-size: .85rem; }}

  .domain-group {{ margin-bottom: 1rem; }}
  .domain-title {{ display: flex; align-items: center; gap: .5rem; margin-bottom: .5rem; }}
  .domain-badge {{ padding: 2px 10px; border-radius: 10px; font-size: .78rem; font-weight: 600; }}
  .domain-count {{ background: var(--surface2); color: var(--text2); padding: 2px 8px;
                   border-radius: 10px; font-size: .72rem; }}

  .item-card {{ border-radius: 6px; margin-bottom: 4px; transition: all .15s; overflow: hidden; }}
  .item-card.archived {{ opacity: .3; }}
  .item-row {{ display: flex; align-items: center; gap: .5rem; padding: .5rem .65rem;
               cursor: pointer; user-select: none; }}
  .item-row:hover {{ filter: brightness(1.15); }}
  .stars {{ font-size: .75rem; flex-shrink: 0; letter-spacing: -1px; }}
  .rarity-label {{ font-size: .68rem; font-weight: 600; flex-shrink: 0; min-width: 2rem; }}
  .item-summary {{ font-size: .82rem; flex: 1; overflow: hidden; text-overflow: ellipsis;
                   white-space: nowrap; color: var(--text); }}
  .expand-arrow {{ color: var(--text2); font-size: .75rem; transition: transform .2s; flex-shrink: 0; }}
  .item-card.open .expand-arrow {{ transform: rotate(90deg); }}
  .item-expand {{ display: none; padding: .25rem .65rem .65rem 3rem; }}
  .item-card.open .item-expand {{ display: block; }}
  .item-detail {{ font-size: .78rem; color: var(--text2); white-space: pre-wrap;
                  word-break: break-word; margin-bottom: .25rem; }}
  .item-meta {{ display: flex; gap: .75rem; font-size: .7rem; color: var(--text2); }}
  .meta-domain {{ background: var(--surface2); padding: 1px 6px; border-radius: 4px; }}
  .meta-source {{ font-style: italic; }}

  .tier-badge {{ font-size: .6rem; padding: 1px 5px; border-radius: 3px; font-weight: 600;
                 flex-shrink: 0; text-transform: uppercase; letter-spacing: .5px; }}
  .tier-badge.staging {{ background: rgba(245,158,11,.15); color: #f59e0b; border: 1px solid rgba(245,158,11,.3); }}
  .tier-badge.verified {{ background: rgba(34,197,94,.1); color: #22c55e; border: 1px solid rgba(34,197,94,.2); }}

  .toggle-box {{ position: relative; display: inline-flex; width: 18px; height: 18px;
                 flex-shrink: 0; cursor: pointer; }}
  .toggle-box input {{ opacity: 0; width: 0; height: 0; position: absolute; }}
  .checkmark {{ width: 16px; height: 16px; border: 2px solid var(--text2); border-radius: 3px;
                transition: all .15s; display: flex; align-items: center; justify-content: center; }}
  .checkmark::after {{ content: "\\2713"; font-size: 11px; color: transparent; transition: .15s; }}
  input:checked + .checkmark {{ border-color: #22c55e; background: rgba(34,197,94,.15); }}
  input:checked + .checkmark::after {{ color: #22c55e; }}

  .btn-row {{ display: flex; gap: .5rem; margin-bottom: .75rem; }}
  .btn {{ padding: .4rem 1rem; border: 1px solid var(--border); border-radius: 6px;
          background: var(--surface); color: var(--text); cursor: pointer; font-size: .8rem; }}
  .btn:hover {{ border-color: var(--accent); }}
  .btn-danger {{ border-color: #ef4444; color: #ef4444; }}
  .btn-primary {{ background: linear-gradient(135deg, #ff8000, #a335ee); border: none;
                  color: #fff; font-weight: 600; font-size: .95rem; padding: .65rem 2rem;
                  border-radius: 8px; cursor: pointer; }}
  .btn-primary:hover {{ filter: brightness(1.1); }}

  .result-panel {{ display: none; background: var(--surface); border: 2px solid #22c55e;
                   border-radius: var(--radius); padding: 1.25rem; margin-top: 1.25rem; }}
  .result-panel.show {{ display: block; }}
  .result-panel h3 {{ color: #22c55e; margin-bottom: .5rem; font-size: .95rem; }}
  .result-textarea {{ width: 100%; min-height: 100px; background: var(--bg); color: var(--text);
                      border: 1px solid var(--border); border-radius: 6px; padding: .5rem;
                      font-family: monospace; font-size: .75rem; resize: vertical; }}

  @media (max-width: 640px) {{
    .stats {{ grid-template-columns: 1fr 1fr; }}
    .container {{ padding: 1rem; }}
    .legend {{ gap: .4rem; }}
  }}
</style>
</head>
<body>
<div class="container">
  <h1>{t['title']}</h1>
  <p class="subtitle">{t['subtitle']}</p>

  <div class="stats">
    <div class="stat"><div class="stat-val">{total_lessons}</div><div class="stat-lbl">{t['n_lessons']}</div></div>
    <div class="stat"><div class="stat-val">{total_decisions}</div><div class="stat-lbl">{t['n_decisions']}</div></div>
    <div class="stat"><div class="stat-val">{total_domains}</div><div class="stat-lbl">{t['n_domains']}</div></div>
  </div>

  <div class="legend">
    <span class="legend-title">{t['legend']}:</span>
    {legend_items}
  </div>

  <div class="btn-row">
    <button class="btn" onclick="showAllItems()">{t['show_all']}</button>
    <button class="btn" onclick="showStagingOnly()">{t['show_staging']}</button>
  </div>

  <div class="instructions">{t['instructions']}</div>

  {profile_html}

  <div class="section">
    <div class="section-title">{t['lessons']} ({total_lessons})</div>
    <div class="btn-row">
      <button class="btn" onclick="toggleAll('lesson',true)">{t['select_all']}</button>
      <button class="btn btn-danger" onclick="toggleAll('lesson',false)">{t['deselect_all']}</button>
    </div>
    {"".join(lesson_sections)}
  </div>

  <div class="section">
    <div class="section-title">{t['decisions']} ({total_decisions})</div>
    <div class="btn-row">
      <button class="btn" onclick="toggleAll('decision',true)">{t['select_all']}</button>
      <button class="btn btn-danger" onclick="toggleAll('decision',false)">{t['deselect_all']}</button>
    </div>
    {decision_cards}
  </div>

  <div style="text-align:center;margin:1.5rem 0">
    <button class="btn btn-primary" onclick="confirmReview()">{t['confirm']}</button>
  </div>

  <div class="result-panel" id="resultPanel">
    <h3>{t['download_done']}</h3>
    <textarea class="result-textarea" id="resultText" readonly></textarea>
    <button class="btn" style="margin-top:.4rem" onclick="copyResult()">Copy</button>
  </div>
</div>

<script>
function toggleExpand(row) {{
  row.closest('.item-card').classList.toggle('open');
}}
function toggleAll(type, checked) {{
  document.querySelectorAll(`.item-card[data-type="${{type}}"] input`).forEach(cb => {{
    cb.checked = checked;
    cb.closest('.item-card').classList.toggle('archived', !checked);
  }});
}}
document.querySelectorAll('.item-card input').forEach(cb => {{
  cb.addEventListener('change', function() {{
    this.closest('.item-card').classList.toggle('archived', !this.checked);
  }});
}});
function refreshDomainVisibility() {{
  document.querySelectorAll('.domain-group').forEach(group => {{
    const visible = Array.from(group.querySelectorAll('.item-card'))
      .some(card => !card.classList.contains('filtered-hidden'));
    group.classList.toggle('filtered-hidden', !visible);
  }});
}}
function showAllItems() {{
  document.querySelectorAll('.item-card').forEach(card => {{
    card.classList.remove('filtered-hidden');
  }});
  document.querySelectorAll('.domain-group').forEach(group => {{
    group.classList.remove('filtered-hidden');
  }});
}}
function showStagingOnly() {{
  document.querySelectorAll('.item-card').forEach(card => {{
    card.classList.toggle('filtered-hidden', card.dataset.tier !== 'staging');
  }});
  refreshDomainVisibility();
}}
function confirmReview() {{
  const archive = [];
  const promote = [];
  document.querySelectorAll('.item-card').forEach(card => {{
    const cb = card.querySelector('input');
    const id = card.dataset.id;
    const type = card.dataset.type;
    const tier = card.dataset.tier;
    if (!cb.checked) {{
      archive.push({{ id, type }});
    }} else if (tier === 'staging') {{
      // User kept a staging item = confirm/promote it
      promote.push({{ id, type }});
    }}
  }});
  const review = {{
    action: "engram_review",
    timestamp: new Date().toISOString(),
    archive: archive,
    promote: promote,
    archive_count: archive.length,
    promote_count: promote.length,
  }};
  const blob = new Blob([JSON.stringify(review, null, 2)], {{ type: 'application/json' }});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `engram_review_${{new Date().toISOString().slice(0,10)}}.json`;
  a.click();
  let summary = '';
  if (promote.length) summary += `promote:${{promote.length}} items\\n` + promote.map(i => `promote ${{i.type}} ${{i.id}}`).join('\\n') + '\\n';
  if (archive.length) summary += `archive:${{archive.length}} items\\n` + archive.map(i => `archive ${{i.type}} ${{i.id}}`).join('\\n');
  if (!summary) summary = 'No changes.';
  document.getElementById('resultText').value = summary;
  document.getElementById('resultPanel').classList.add('show');
  document.getElementById('resultPanel').scrollIntoView({{ behavior: 'smooth' }});
}}
function copyResult() {{
  const ta = document.getElementById('resultText');
  ta.select();
  navigator.clipboard.writeText(ta.value);
}}
</script>
</body>
</html>"""

    def export_review_page(self, lang: str = "zh") -> Path:
        """Generate review HTML and save to exports directory.

        Returns the file path.
        """
        html = self.generate_review_page(lang=lang)
        export_dir = self._exports_dir
        export_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        out_path = export_dir / f"review_{date_str}.html"
        out_path.write_text(html, encoding="utf-8")
        return out_path

    def promote_knowledge(self, item_id: str) -> dict:
        """Promote a staging item to verified tier."""
        # Try lessons first, then decisions
        for path, entry_type in [
            (self._knowledge_dir / "lessons.json", "lesson"),
            (self._knowledge_dir / "decisions.json", "decision"),
        ]:
            entries = self._read_entries(path, entry_type)
            for entry in entries:
                if entry.get("id") == item_id:
                    entry["tier"] = "verified"
                    entry["promoted_at"] = _now_iso()
                    entry["promotion_reason"] = "user_confirmed"
                    _write_json(path, entries)
                    return {"status": "promoted", "id": item_id}
        return {"status": "not_found", "id": item_id}

    def apply_review(self, review_data: dict | str) -> dict:
        """Process a knowledge review result — promote confirmed staging items,
        archive unchecked items.

        Args:
            review_data: Either a dict with ``archive``/``promote`` lists, or a
                         multi-line string of ``archive/promote <type> <id>`` commands.
        Returns:
            Summary dict with promoted/archived counts and details.
        """
        items_to_archive: list[dict] = []
        items_to_promote: list[dict] = []

        if isinstance(review_data, str):
            for line in review_data.strip().splitlines():
                parts = line.strip().split()
                if len(parts) >= 3:
                    action = parts[0]
                    item = {"type": parts[1], "id": parts[2]}
                    if action == "archive":
                        items_to_archive.append(item)
                    elif action == "promote":
                        items_to_promote.append(item)
        else:
            items_to_archive = review_data.get("archive", [])
            items_to_promote = review_data.get("promote", [])

        promoted = 0
        archived = 0
        errors: list[str] = []

        for item in items_to_promote:
            item_id = item.get("id", "")
            try:
                result = self.promote_knowledge(item_id)
                if result.get("status") == "promoted":
                    promoted += 1
            except Exception as exc:
                errors.append(f"promote {item_id}: {exc}")

        for item in items_to_archive:
            item_id = item.get("id", "")
            try:
                result = self.archive_knowledge(item_id)
                if result.get("error"):
                    errors.append(f"archive {item_id}: {result['error']}")
                else:
                    archived += 1
            except Exception as exc:
                errors.append(f"archive {item_id}: {exc}")

        return {
            "promoted": promoted,
            "archived": archived,
            "total_requested": len(items_to_archive) + len(items_to_promote),
            "errors": errors,
        }
