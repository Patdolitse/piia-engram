(function () {
  "use strict";

  var memory = [];
  var playbooks = [];        // active playbooks for the Playbooks view (read-only v1)
  var selected = new Set();  // ids checked for bulk archive in the 记忆 view

  function csrf() { return sessionStorage.getItem("engram_dock_csrf") || ""; }

  function api(path) {
    return fetch(path, { headers: { "Accept": "application/json" }, credentials: "same-origin" })
      .then(function (r) { return r.json(); });
  }

  // Writes carry the CSRF synchronizer token (the browser adds the same-origin
  // Origin header). Returns {status, body}.
  function post(path, body) {
    return fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Engram-CSRF": csrf() },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); });
  }

  function kindLabel(k) { return k === "lesson" ? "经验" : k === "decision" ? "决策" : k; }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function showView(name) {
    document.querySelectorAll("#nav a").forEach(function (a) {
      a.classList.toggle("active", a.dataset.view === name);
    });
    document.querySelectorAll(".view").forEach(function (s) {
      s.classList.toggle("active", s.dataset.view === name);
    });
    if (name === "trash") loadTrash();          // refresh archived list each time it's opened
    if (name === "playbooks") loadPlaybooks();  // load playbooks on first/each open
  }

  function renderMemory() {
    var q = (document.getElementById("memory-search").value || "").toLowerCase();
    var kind = document.getElementById("memory-kind").value;
    var rows = memory.filter(function (m) {
      if (kind !== "all" && m.kind !== kind) return false;
      if (q && (m.title || "").toLowerCase().indexOf(q) < 0 &&
        (m.copy || "").toLowerCase().indexOf(q) < 0) return false;
      return true;
    });
    var tb = document.querySelector("#memory-table tbody");
    tb.innerHTML = "";
    if (rows.length === 0) {
      tb.innerHTML = "<tr><td colspan='4' class='muted'>没有匹配的记忆</td></tr>";
      updateBulkBar();
      return;
    }
    rows.forEach(function (m) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td class='col-check'><input type='checkbox' class='row-check' data-id='" +
          escapeHtml(m.id) + "'" + (selected.has(m.id) ? " checked" : "") + "></td>" +
        "<td><span class='tag tag-" + m.kind + "'>" + kindLabel(m.kind) + "</span></td>" +
        "<td class='cell-title'>" + escapeHtml(m.title) + "</td>" +
        "<td>" + (m.tier ? "<span class='tier'>" + escapeHtml(m.tier) + "</span>" : "") + "</td>";
      var cb = tr.querySelector(".row-check");
      cb.onclick = function (e) { e.stopPropagation(); };  // don't open the detail panel
      cb.onchange = function () {
        if (cb.checked) selected.add(m.id); else selected.delete(m.id);
        updateBulkBar();
      };
      tr.onclick = function () { showDetail(m.id); };
      tb.appendChild(tr);
    });
    updateBulkBar();
  }

  // Bulk archive: the toolbar bar shows the count; one confirm archives them all.
  function updateBulkBar() {
    var bar = document.getElementById("memory-bulk");
    if (bar) {
      bar.hidden = selected.size === 0;
      var c = document.getElementById("bulk-count");
      if (c) c.textContent = "已选 " + selected.size + " 条";
    }
    var all = document.getElementById("memory-select-all");
    if (all) {
      var checks = document.querySelectorAll("#memory-table .row-check");
      all.checked = checks.length > 0 &&
        Array.prototype.every.call(checks, function (x) { return x.checked; });
    }
  }

  function onSelectAll() {
    var all = document.getElementById("memory-select-all");
    var checks = document.querySelectorAll("#memory-table .row-check");
    Array.prototype.forEach.call(checks, function (x) {
      x.checked = all.checked;
      var id = x.getAttribute("data-id");
      if (all.checked) selected.add(id); else selected.delete(id);
    });
    updateBulkBar();
  }

  function bulkArchive() {
    var ids = Array.from(selected);  // selected is a Set, not array-like
    if (ids.length === 0) return;
    if (!window.confirm("批量归档选中的 " + ids.length + " 条记忆？可在回收站恢复，不会删除。")) return;
    // sequential (not parallel) so concurrent writes don't contend on the store files
    ids.reduce(function (p, id) {
      return p.then(function () { return post("/api/dock-archive", { id: id }); });
    }, Promise.resolve()).then(function () {
      selected.clear();
      document.getElementById("detail").innerHTML =
        "<p class='muted'>已批量归档（可在回收站恢复）。</p>";
      return loadMemory();
    }).catch(function () { return loadMemory(); });
  }

  // 回收站: archived entries with one-click restore.
  function loadTrash() {
    var box = document.getElementById("trash-list");
    if (!box) return;
    box.innerHTML = "<div class='muted'>正在加载…</div>";
    api("/api/dock-archived").then(function (res) {
      if (!res || !res.ok) { box.innerHTML = "<div class='err'>读取回收站失败。</div>"; return; }
      var rows = res.results || [];
      if (rows.length === 0) { box.innerHTML = "<p class='muted'>回收站是空的。</p>"; return; }
      box.innerHTML = "";
      rows.forEach(function (m) {
        var row = document.createElement("div");
        row.className = "trash-row";
        row.innerHTML =
          "<span class='trash-meta'><span class='tag tag-" + m.kind + "'>" +
            kindLabel(m.kind) + "</span> " + escapeHtml(m.title) + "</span>" +
          "<button class='btn btn-primary trash-restore'>恢复</button>";
        row.querySelector(".trash-restore").onclick = function () { restoreItem(m.id); };
        box.appendChild(row);
      });
    }).catch(function () { box.innerHTML = "<div class='err'>读取回收站失败（网络）。</div>"; });
  }

  function restoreItem(id) {
    post("/api/dock-restore", { id: id }).then(function (res) {
      if (res.status === 200 && res.body.ok) {
        loadTrash();    // it's gone from the trash
        loadMemory();   // …and back in the active set
      } else {
        window.alert("恢复失败：" + (res.body.error || res.status));
      }
    }).catch(function () { window.alert("恢复失败（网络）"); });
  }

  // Playbooks (view-only v1): a separate per-id subsystem; read + display only.
  function scopeLabel(t) { return t === "project" ? "项目" : t === "shared" ? "共享" : "全局"; }

  function loadPlaybooks() {
    var tb = document.querySelector("#pb-table tbody");
    return api("/api/dock-playbooks").then(function (res) {
      playbooks = (res && res.results) || [];
      renderPlaybooks();
    }).catch(function () {
      if (tb) tb.innerHTML = "<tr><td colspan='4' class='muted'>读取 Playbook 失败</td></tr>";
    });
  }

  function renderPlaybooks() {
    var q = (document.getElementById("pb-search").value || "").toLowerCase();
    var rows = playbooks.filter(function (p) {
      if (!q) return true;
      return (p.title || "").toLowerCase().indexOf(q) >= 0 ||
        (p.description || "").toLowerCase().indexOf(q) >= 0 ||
        (p.domain || "").toLowerCase().indexOf(q) >= 0;
    });
    var tb = document.querySelector("#pb-table tbody");
    tb.innerHTML = "";
    if (rows.length === 0) {
      tb.innerHTML = "<tr><td colspan='4' class='muted'>还没有 Playbook（多用一会儿 Engram 会自动沉淀）</td></tr>";
      return;
    }
    rows.forEach(function (p) {
      var ver = (p.version == null ? 1 : p.version);
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td class='cell-title'>" + escapeHtml(p.title) + "</td>" +
        "<td>" + (p.domain ? "<span class='tier'>" + escapeHtml(p.domain) + "</span>" : "") + "</td>" +
        "<td><span class='tier'>" + scopeLabel(p.scope_type) +
          (p.project_count ? "·" + p.project_count : "") + "</span></td>" +
        "<td><span class='tier'>v" + escapeHtml(String(ver)) + "</span></td>";
      tr.onclick = function () { showPlaybook(p.id); };
      tb.appendChild(tr);
    });
  }

  function pbList(label, items) {
    if (!items || items.length === 0) return "";
    var lis = items.map(function (x) { return "<li>" + escapeHtml(x) + "</li>"; }).join("");
    return "<label>" + label + "</label><ul class='pb-ul'>" + lis + "</ul>";
  }

  function showPlaybook(id) {
    var p = playbooks.filter(function (x) { return x.id === id; })[0];
    var d = document.getElementById("pb-detail");
    if (!p) { d.innerHTML = "<p class='muted'>点一条 Playbook 查看完整步骤</p>"; return; }
    var ver = (p.version == null ? 1 : p.version);
    var html = "<h2>" + escapeHtml(p.title) + "</h2>" +
      "<div class='meta'>" + scopeLabel(p.scope_type) + " · v" + escapeHtml(String(ver)) +
      (p.domain ? " · " + escapeHtml(p.domain) : "") + "</div>";
    if (p.description) html += "<label>描述</label><div class='field'>" + escapeHtml(p.description) + "</div>";
    if (p.outcome) html += "<label>预期结果</label><div class='field'>" + escapeHtml(p.outcome) + "</div>";
    if (p.steps && p.steps.length) {
      var steps = p.steps.map(function (s) {
        var t = escapeHtml(s.action || "");
        if (s.detail) t += "<span class='pb-step-detail'>" + escapeHtml(s.detail) + "</span>";
        return "<li>" + t + "</li>";
      }).join("");
      html += "<label>步骤</label><ol class='pb-ol'>" + steps + "</ol>";
    }
    html += pbList("坑 / 注意", p.pitfalls);
    html += pbList("前置条件", p.preconditions);
    html += pbList("触发词", p.triggers);
    d.innerHTML = html;
  }

  function fieldLabels(kind) {
    return kind === "lesson"
      ? [["summary", "经验", 2], ["detail", "细节", 5]]
      : [["question", "问题", 2], ["choice", "选择", 2], ["reasoning", "理由", 4]];
  }

  function showDetail(id) {
    var m = memory.filter(function (x) { return x.id === id; })[0];
    var d = document.getElementById("detail");
    if (!m) { d.innerHTML = "<p class='muted'>点一条记忆查看详情</p>"; return; }
    var fields = m.fields || {};
    var html = "<div class='meta'>" + kindLabel(m.kind) + (m.tier ? " · " + escapeHtml(m.tier) : "") + "</div>";
    fieldLabels(m.kind).forEach(function (f) {
      html += "<label>" + f[1] + "</label><textarea data-field='" + f[0] + "' rows='" + f[2] + "'>" +
        escapeHtml(fields[f[0]] || "") + "</textarea>";
    });
    html += "<div class='detail-actions'>" +
      "<button id='save-btn' class='btn btn-primary'>保存</button>" +
      "<button id='archive-btn' class='btn btn-danger'>归档</button></div>" +
      "<div id='detail-msg' class='detail-msg'></div>";
    d.innerHTML = html;
    document.getElementById("save-btn").onclick = function () { saveDetail(id); };
    document.getElementById("archive-btn").onclick = function () { archiveItem(id); };
  }

  function detailMsg(text, ok) {
    var el = document.getElementById("detail-msg");
    if (el) { el.textContent = text; el.className = "detail-msg " + (ok ? "ok" : "err"); }
  }

  function saveDetail(id) {
    var updates = {};
    document.querySelectorAll("#detail textarea[data-field]").forEach(function (t) {
      updates[t.dataset.field] = t.value;
    });
    post("/api/dock-update", { id: id, updates: updates }).then(function (res) {
      if (res.status === 200 && res.body.ok) {
        loadMemory().then(function () { showDetail(id); detailMsg("已保存", true); });
      } else {
        detailMsg("保存失败：" + (res.body.error || res.status), false);
      }
    }).catch(function () { detailMsg("保存失败（网络）", false); });
  }

  function archiveItem(id) {
    if (!window.confirm("归档这条记忆？可在回收站恢复，不会删除。")) return;
    post("/api/dock-archive", { id: id }).then(function (res) {
      if (res.status === 200 && res.body.ok) {
        loadMemory().then(function () {
          document.getElementById("detail").innerHTML = "<p class='muted'>已归档（可在回收站恢复）。</p>";
        });
      } else {
        detailMsg("归档失败：" + (res.body.error || res.status), false);
      }
    }).catch(function () { detailMsg("归档失败（网络）", false); });
  }

  function cardHtml(label, n) {
    return "<div class='card'><div class='n'>" + n + "</div><div class='l'>" + label + "</div></div>";
  }

  function loadMemory() {
    return api("/api/dock-memory").then(function (res) {
      memory = (res && res.results) || [];
      var lessons = memory.filter(function (m) { return m.kind === "lesson"; }).length;
      var decisions = memory.filter(function (m) { return m.kind === "decision"; }).length;
      document.getElementById("overview-cards").innerHTML =
        cardHtml("经验", lessons) + cardHtml("决策", decisions) + cardHtml("活跃记忆", memory.length);
      renderMemory();
    }).catch(function () {
      document.getElementById("overview-cards").innerHTML =
        "<div class='card'><div class='l'>读取记忆失败，请重新运行 engram serve --ui</div></div>";
    });
  }

  // 接续 (the soul): one-click cross-tool context to paste into the AI tool you're using.
  function loadResume() {
    var out = document.getElementById("resume-out");
    out.hidden = false;
    out.innerHTML = "<div class='muted'>正在生成…</div>";
    api("/api/dock-resume").then(function (res) {
      if (res && res.ok) {
        var md = res.markdown || "（暂无可接续的上下文——多用一会儿 Engram 再来。）";
        out.innerHTML =
          "<div class='resume-actions'><button id='copy-btn' class='btn btn-primary'>复制到剪贴板</button>" +
          "<span id='copy-msg' class='copy-msg'></span></div><pre class='resume-md'></pre>";
        out.querySelector(".resume-md").textContent = md;
        document.getElementById("copy-btn").onclick = function () { copyResume(md); };
      } else {
        out.innerHTML = "<div class='err'>生成失败：" + escapeHtml((res && res.error) || "") + "</div>";
      }
    }).catch(function () { out.innerHTML = "<div class='err'>生成失败（网络）</div>"; });
  }

  function copyResume(md) {
    var msg = document.getElementById("copy-msg");
    function done(ok) { if (msg) { msg.textContent = ok ? "✓ 已复制，去粘贴吧" : "复制失败，请手动选中复制"; } }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(md).then(function () { done(true); }, function () { done(false); });
    } else { done(false); }
  }

  document.querySelectorAll("#nav a").forEach(function (a) {
    a.onclick = function () { showView(a.dataset.view); };
  });
  var rb = document.getElementById("resume-btn");
  if (rb) rb.onclick = loadResume;
  document.getElementById("memory-search").oninput = renderMemory;
  document.getElementById("memory-kind").onchange = renderMemory;
  var selAll = document.getElementById("memory-select-all");
  if (selAll) selAll.onchange = onSelectAll;
  var bulkBtn = document.getElementById("bulk-archive-btn");
  if (bulkBtn) bulkBtn.onclick = bulkArchive;
  var pbSearch = document.getElementById("pb-search");
  if (pbSearch) pbSearch.oninput = renderPlaybooks;

  loadMemory();
})();
