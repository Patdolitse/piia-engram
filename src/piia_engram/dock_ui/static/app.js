(function () {
  "use strict";

  var memory = [];

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
      tb.innerHTML = "<tr><td colspan='3' class='muted'>没有匹配的记忆</td></tr>";
      return;
    }
    rows.forEach(function (m) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td><span class='tag tag-" + m.kind + "'>" + kindLabel(m.kind) + "</span></td>" +
        "<td>" + escapeHtml(m.title) + "</td>" +
        "<td>" + (m.tier ? "<span class='tier'>" + escapeHtml(m.tier) + "</span>" : "") + "</td>";
      tr.onclick = function () { showDetail(m.id); };
      tb.appendChild(tr);
    });
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

  loadMemory();
})();
