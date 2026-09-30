/* DMX 灯具地址编排台 —— 前端逻辑（原生 JS） */
"use strict";

let state = null;          // /api/state 的完整快照
let lastPreview = null;    // 最近一次可行预览（含 preview_id）

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

/* ---------- 基础工具 ---------- */

async function api(path, opts = {}) {
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = `请求失败（${res.status}）`;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

function toast(msg, isError = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = isError ? "error" : "";
  el.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 4200);
}

async function refresh() {
  state = await api("/api/state");
  renderAll();
}

/* ---------- 渲染 ---------- */

function lineName(id) {
  const l = state.lines.find((x) => x.id === id);
  return l ? l.name : `#${id}`;
}

function renderVersion() {
  $("#version-badge").innerHTML =
    `配置版本 <b>v${state.config_version}</b> · 已发布版本 ` +
    (state.published_version ? `<b>v${state.published_version}</b>` : "（未发布）");
}

function renderModels() {
  const box = $("#model-list");
  if (!state.models.length) {
    box.innerHTML = '<p class="hint">尚未导入任何型号。</p>';
    return;
  }
  box.innerHTML = "";
  for (const m of state.models) {
    const div = document.createElement("div");
    div.className = "card";
    div.innerHTML =
      `<h3>${esc(m.name)}<span><button class="danger" data-del-model="${m.id}">删除</button></span></h3>` +
      m.modes.map((mo) => `<span class="mode-chip">${esc(mo.name)} · ${mo.channels}ch</span>`).join("");
    box.appendChild(div);
  }
}

function renderFixtureForm() {
  const modelSel = $("#fx-model");
  modelSel.innerHTML = '<option value="">— 选择型号 —</option>' +
    state.models.map((m) => `<option value="${m.id}">${esc(m.name)}</option>`).join("");
  renderModeOptions();
}

function renderModeOptions() {
  const mid = Number($("#fx-model").value);
  const model = state.models.find((m) => m.id === mid);
  $("#fx-mode").innerHTML = '<option value="">— 选择模式 —</option>' +
    (model ? model.modes.map((mo) =>
      `<option value="${mo.id}">${esc(mo.name)}（${mo.channels}ch）</option>`).join("") : "");
}

function renderFixtures() {
  const tb = $("#fixture-table tbody");
  tb.innerHTML = "";
  for (const f of state.fixtures) {
    const a = state.assignments[f.id];
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${f.id}</td><td>${esc(f.name)}</td><td>${f.channels}</td>` +
      `<td>${a ? `${esc(lineName(a.line_id))} @ ${a.start}–${a.start + f.channels - 1}` : "—"}</td>` +
      `<td><input type="checkbox" data-lock="${f.id}" ${f.locked ? "checked" : ""} ${a ? "" : "disabled"}></td>` +
      `<td><button class="danger" data-del-fixture="${f.id}">删除</button></td>`;
    tb.appendChild(tr);
  }
}

function renderLines() {
  const box = $("#line-list");
  box.innerHTML = "";
  $("#btn-add-line").disabled = state.lines.length >= state.max_lines;
  for (const l of state.lines) {
    const res = state.reservations.filter((r) => r.line_id === l.id);
    const div = document.createElement("div");
    div.className = "card";
    div.innerHTML =
      `<h3>${esc(l.name)}（512 通道）<span><button class="danger" data-del-line="${l.id}">删除线路</button></span></h3>` +
      `<div class="row">
         <input type="number" min="1" max="512" placeholder="起始" id="res-s-${l.id}">
         <input type="number" min="1" max="512" placeholder="结束" id="res-e-${l.id}">
         <button data-add-res="${l.id}">添加保留区</button>
       </div>` +
      (res.length
        ? `<ul class="plain">` + res.map((r) =>
            `<li>保留 ${r.start}–${r.end} <button class="danger" data-del-res="${r.id}">删除</button></li>`).join("") + `</ul>`
        : '<p class="hint">无保留区</p>');
    box.appendChild(div);
  }
}

function renderPublished() {
  $("#published-info").textContent = state.published_version
    ? `当前发布版本：v${state.published_version}`
    : "尚未发布任何地址表";
  const tb = $("#published-table tbody");
  tb.innerHTML = "";
  const rows = Object.entries(state.assignments)
    .map(([fid, a]) => ({ fid: Number(fid), ...a }))
    .sort((x, y) => x.line_id - y.line_id || x.start - y.start);
  for (const r of rows) {
    const f = state.fixtures.find((x) => x.id === r.fid);
    if (!f) continue;
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${f.id}</td><td>${esc(f.name)}</td><td>${esc(lineName(r.line_id))}</td>` +
      `<td>${r.start}</td><td>${r.start + f.channels - 1}</td><td>${f.channels}</td>`;
    tb.appendChild(tr);
  }
}

function renderAll() {
  renderVersion();
  renderModels();
  renderFixtureForm();
  renderFixtures();
  renderLines();
  renderPublished();
}

/* ---------- 编排结果 ---------- */

const SEG_COLORS = ["#2f6fe0", "#2fa46a", "#b0702f", "#8a4fd0", "#c0455e", "#3f9fb0", "#a0a832", "#d06a2f"];

function occupancyBar(lineId, placements) {
  const cap = state.line_capacity;
  const bar = document.createElement("div");
  bar.className = "bar";
  const segs = [];
  for (const r of state.reservations.filter((x) => x.line_id === lineId)) {
    segs.push({ start: r.start, end: r.end, label: "保留", cls: "reserved" });
  }
  let i = 0;
  for (const p of placements.filter((x) => x.line_id === lineId).sort((a, b) => a.start - b.start)) {
    segs.push({ start: p.start, end: p.start + p.channels - 1, label: p.name, color: SEG_COLORS[i++ % SEG_COLORS.length] });
  }
  for (const s of segs) {
    const el = document.createElement("div");
    el.className = "seg" + (s.cls ? " " + s.cls : "");
    el.style.left = ((s.start - 1) / cap * 100) + "%";
    el.style.width = ((s.end - s.start + 1) / cap * 100) + "%";
    if (s.color) el.style.background = s.color;
    el.textContent = s.label;
    el.title = `${s.label}: ${s.start}–${s.end}`;
    bar.appendChild(el);
  }
  const axis = document.createElement("div");
  axis.className = "axis";
  axis.innerHTML = "<span>1</span><span>128</span><span>256</span><span>384</span><span>512</span>";
  const wrap = document.createElement("div");
  wrap.append(bar, axis);
  return wrap;
}

function renderPlanResult(pv) {
  const box = $("#plan-result");
  box.innerHTML = "";
  if (!pv.feasible) {
    box.innerHTML = `<h2>无解：冲突约束</h2><ul class="plain">` +
      pv.conflicts.map((c) => `<li class="conflict">⚠ ${esc(c)}</li>`).join("") + `</ul>`;
    return;
  }
  const head = document.createElement("p");
  head.innerHTML =
    `<span class="ok">求解成功${pv.exact ? "（最优）" : "（限时内可行解）"}</span> · ` +
    `迁移 <b>${pv.moved_count}</b> 台 · 启用线路 <b>${pv.used_lines}</b> 条 · 基于配置版本 v${pv.config_version}`;
  box.appendChild(head);

  // 线路占用图
  const h1 = document.createElement("h2");
  h1.textContent = "线路占用预览";
  box.appendChild(h1);
  const placements = Object.entries(pv.placements).map(([fid, p]) => {
    const f = state.fixtures.find((x) => x.id === Number(fid));
    return { ...p, name: f ? f.name : `#${fid}`, channels: f ? f.channels : 0 };
  });
  for (const l of state.lines) {
    const used = placements.filter((p) => p.line_id === l.id)
      .reduce((s, p) => s + p.channels, 0);
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `<h3>${esc(l.name)}<span class="tag">${used}/512 通道</span></h3>`;
    card.appendChild(occupancyBar(l.id, placements));
    box.appendChild(card);
  }

  // 迁移清单
  const h2 = document.createElement("h2");
  h2.textContent = `迁移清单（${pv.migrations.length} 台）`;
  box.appendChild(h2);
  if (pv.migrations.length) {
    const tbl = document.createElement("table");
    tbl.innerHTML = "<thead><tr><th>灯具</th><th>原地址</th><th>新地址</th></tr></thead>";
    const tb = document.createElement("tbody");
    for (const m of pv.migrations) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td>${esc(m.name)}</td>` +
        `<td>${esc(lineName(m.from.line_id))} @ ${m.from.start}</td>` +
        `<td>${esc(lineName(m.to.line_id))} @ ${m.to.start}</td>`;
      tb.appendChild(tr);
    }
    tbl.appendChild(tb);
    box.appendChild(tbl);
  } else {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = "无灯具迁移。";
    box.appendChild(p);
  }
}

/* ---------- 事件 ---------- */

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function bindTabs() {
  $$("nav button").forEach((b) =>
    b.addEventListener("click", () => {
      $$("nav button").forEach((x) => x.classList.toggle("active", x === b));
      $$("main > section").forEach((s) => s.classList.add("hidden"));
      $("#tab-" + b.dataset.tab).classList.remove("hidden");
    }));
}

function bindLibrary() {
  $("#library-file").addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const rd = new FileReader();
    rd.onload = () => { $("#library-json").value = rd.result; };
    rd.readAsText(file);
  });
  $("#btn-import").addEventListener("click", async () => {
    try {
      const data = JSON.parse($("#library-json").value);
      await api("/api/models", { method: "POST", body: data });
      $("#library-json").value = "";
      lastPreview = null;
      await refresh();
      toast("型录导入成功");
    } catch (err) { toast(err.message, true); }
  });
}

function bindFixtures() {
  $("#fx-model").addEventListener("change", renderModeOptions);
  $("#btn-add-fixture").addEventListener("click", async () => {
    try {
      const name = $("#fx-name").value.trim();
      const modeId = $("#fx-mode").value;
      if (!name) throw new Error("请填写灯具名称");
      if (!modeId) throw new Error("请选择型号与模式");
      const modelId = $("#fx-model").value;
      await api("/api/fixtures", {
        method: "POST",
        body: { name, model_id: Number(modelId), mode_id: Number(modeId) },
      });
      $("#fx-name").value = "";
      lastPreview = null;
      await refresh();
      toast("灯具已添加");
    } catch (err) { toast(err.message, true); }
  });
  $("#fixture-table").addEventListener("click", async (e) => {
    const del = e.target.dataset.delFixture;
    if (del) {
      try {
        await api(`/api/fixtures/${del}`, { method: "DELETE" });
        lastPreview = null;
        await refresh();
      } catch (err) { toast(err.message, true); }
    }
  });
  $("#fixture-table").addEventListener("change", async (e) => {
    const id = e.target.dataset.lock;
    if (!id) return;
    try {
      await api(`/api/fixtures/${id}`, { method: "PATCH", body: { locked: e.target.checked } });
      lastPreview = null;
      await refresh();
      toast(e.target.checked ? "已锁定当前线路与起始地址" : "已解除锁定");
    } catch (err) {
      toast(err.message, true);
      await refresh();
    }
  });
}

function bindLines() {
  $("#btn-add-line").addEventListener("click", async () => {
    try {
      const name = $("#line-name").value.trim() || `Universe ${state.lines.length + 1}`;
      await api("/api/lines", { method: "POST", body: { name } });
      $("#line-name").value = "";
      lastPreview = null;
      await refresh();
    } catch (err) { toast(err.message, true); }
  });
  $("#line-list").addEventListener("click", async (e) => {
    const d = e.target.dataset;
    try {
      if (d.delLine) {
        await api(`/api/lines/${d.delLine}`, { method: "DELETE" });
      } else if (d.delRes) {
        await api(`/api/reservations/${d.delRes}`, { method: "DELETE" });
      } else if (d.addRes) {
        const start = Number($(`#res-s-${d.addRes}`).value);
        const end = Number($(`#res-e-${d.addRes}`).value);
        if (!start || !end) throw new Error("请填写保留区起止地址");
        await api(`/api/lines/${d.addRes}/reservations`, { method: "POST", body: { start, end } });
      } else return;
      lastPreview = null;
      await refresh();
    } catch (err) { toast(err.message, true); }
  });
  $("#model-list").addEventListener("click", async (e) => {
    const id = e.target.dataset.delModel;
    if (!id) return;
    try {
      await api(`/api/models/${id}`, { method: "DELETE" });
      lastPreview = null;
      await refresh();
    } catch (err) { toast(err.message, true); }
  });
}

function bindPlan() {
  $("#btn-preview").addEventListener("click", async () => {
    const btn = $("#btn-preview");
    btn.disabled = true;
    $("#plan-status").textContent = "求解中…";
    try {
      const pv = await api("/api/preview", { method: "POST" });
      lastPreview = pv.feasible ? pv : null;
      $("#btn-publish").disabled = !pv.feasible;
      $("#plan-status").textContent = "";
      renderPlanResult(pv);
    } catch (err) {
      $("#plan-status").textContent = "";
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });
  $("#btn-publish").addEventListener("click", async () => {
    if (!lastPreview) return;
    try {
      await api("/api/publish", { method: "POST", body: { preview_id: lastPreview.preview_id } });
      toast("发布成功");
      lastPreview = null;
      $("#btn-publish").disabled = true;
      $("#plan-result").innerHTML = "";
      await refresh();
    } catch (err) {
      toast(err.message, true);
      $("#btn-publish").disabled = true;
      lastPreview = null;
    }
  });
}

/* ---------- 启动 ---------- */

(async function main() {
  bindTabs();
  bindLibrary();
  bindFixtures();
  bindLines();
  bindPlan();
  try {
    await refresh();
  } catch (err) {
    toast("初始化失败：" + err.message, true);
  }
})();
