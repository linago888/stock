"use strict";

const $ = (sel) => document.querySelector(sel);
const fmt = (n) => (n == null ? "-" : Number(n).toLocaleString());
const pctFmt = (v) => (v == null ? "-" : (v * 100).toFixed(0) + "%");
const signedPP = (v) => {
  if (v == null) return "-";
  const pp = v * 100;
  const sign = pp > 0 ? "+" : "";
  return `${sign}${pp.toFixed(0)}pp`;
};

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

async function loadStatus() {
  const s = await getJSON("/api/signals/status");
  $("#sigSurgeCount").textContent = fmt(s.surge_event_count);
  $("#sigPocCount").textContent = fmt(s.poc_count);
  $("#sigCtlCount").textContent = fmt(s.ctl_count);
  $("#sigPocHit").textContent = `${s.poc_hit_rate.hit}/${s.poc_hit_rate.total} (${s.poc_hit_rate.pct}%)`;
  $("#sigCtlHit").textContent = `${s.ctl_hit_rate.hit}/${s.ctl_hit_rate.total} (${s.ctl_hit_rate.pct}%)`;
  $("#sigDateRange").textContent = s.date_range.min && s.date_range.max
    ? `${s.date_range.min} ~ ${s.date_range.max}`
    : "-";
}

async function loadComparison() {
  const data = await getJSON("/api/signals/comparison");
  const tbody = document.querySelector("#compTable tbody");
  tbody.innerHTML = "";
  const maxAbs = Math.max(...data.rows.map((r) => Math.abs(r.delta)), 0.001);
  data.rows.forEach((r, idx) => {
    const arrow = r.delta > 0.1 ? "🔺" : r.delta < -0.05 ? "🔻" : "·";
    const liftStr = r.lift == null ? "∞" : `${r.lift.toFixed(2)}×`;
    const barWidth = Math.round((Math.abs(r.delta) / maxAbs) * 160);
    const barCls = r.delta < 0 ? "delta-bar neg" : "delta-bar";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${arrow}</td>
      <td>${r.label}</td>
      <td class="num">${r.poc_stocks}/${r.poc_total} (${pctFmt(r.poc_cov)})</td>
      <td class="num">${r.ctl_stocks}/${r.ctl_total} (${pctFmt(r.ctl_cov)})</td>
      <td class="num ${r.delta > 0 ? "delta-pos" : r.delta < 0 ? "delta-neg" : ""}">${signedPP(r.delta)}</td>
      <td class="num">${liftStr}</td>
      <td class="delta-cell"><span class="${barCls}" style="width:${barWidth}px"></span></td>
    `;
    tbody.appendChild(tr);
  });
}

async function loadWhitelist() {
  const data = await getJSON("/api/signals/whitelist?min_delta=0.2&min_lift=2&min_stocks=3");
  const panel = $("#whitelistPanel");
  if (!data.items.length) {
    panel.innerHTML = `<div class="hint">尚無訊號通過 (Δ ≥ ${data.min_delta * 100}pp, lift ≥ ${data.min_lift}×, ≥ ${data.min_stocks} 檔)</div>`;
    return;
  }
  panel.innerHTML = data.items.map((r) => `
    <div class="whitelist-item">
      <div class="wl-head">
        <span class="wl-title">🔺 ${r.label}</span>
        <span class="wl-meta">飆漲組 ${r.poc_stocks}/${r.poc_total} · 對照組 ${r.ctl_stocks}/${r.ctl_total} · Δ ${signedPP(r.delta)} · lift ${r.lift == null ? "∞" : r.lift.toFixed(2) + "×"}</span>
      </div>
      <div class="wl-kws">${r.keywords.map((k) => `<span class="wl-kw">${k}</span>`).join("")}</div>
    </div>
  `).join("");
}

let currentGroup = "poc";

async function loadStocks(group) {
  currentGroup = group;
  document.querySelectorAll(".group-toggle button").forEach((b) => {
    b.classList.toggle("primary", b.dataset.group === group);
    b.classList.toggle("secondary", b.dataset.group !== group);
  });
  const data = await getJSON(`/api/signals/stocks?group=${group}`);
  const tbody = document.querySelector("#stocksTable tbody");
  tbody.innerHTML = "";
  data.items.forEach((it) => {
    const ret = it.return_pct == null ? "-" : `${it.return_pct > 0 ? "+" : ""}${it.return_pct.toFixed(1)}%`;
    const vol = it.vol_ratio == null ? "-" : `${it.vol_ratio.toFixed(1)}×`;
    const chips = (it.labels || []).map((l) => `<span class="label-chip">${l}</span>`).join(" ");
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${it.symbol}</td>
      <td>${it.name}</td>
      <td>${it.T0}</td>
      <td class="num">${ret}</td>
      <td class="num">${vol}</td>
      <td class="num">${it.ann_count}</td>
      <td><div class="label-chips">${chips}</div></td>
    `;
    tr.addEventListener("click", () => openAnnouncements(it.symbol, it.name, it.T0));
    tbody.appendChild(tr);
  });
}

async function openAnnouncements(symbol, name, t0) {
  const data = await getJSON(`/api/signals/announcements?symbol=${encodeURIComponent(symbol)}&group=${currentGroup}`);
  const panel = $("#annPanel");
  panel.classList.remove("hidden");
  $("#annTitle").textContent = `${symbol} ${name} — T0 ${t0} · T-30 天重訊 ${data.count} 筆`;
  const tbody = document.querySelector("#annTable tbody");
  tbody.innerHTML = data.items.map((a) => `
    <tr>
      <td>T-${a.days_before}</td>
      <td>${a.date}</td>
      <td><div class="label-chips">${(a.labels || []).map((l) => `<span class="label-chip">${l}</span>`).join(" ")}</div></td>
      <td>${a.subject}</td>
    </tr>
  `).join("");
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function refreshAll() {
  try {
    await loadStatus();
    await Promise.all([loadComparison(), loadWhitelist(), loadStocks("poc")]);
  } catch (err) {
    console.error(err);
    alert(`載入失敗：${err.message}`);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".group-toggle button").forEach((b) => {
    b.addEventListener("click", () => loadStocks(b.dataset.group));
  });
  $("#annClose").addEventListener("click", () => $("#annPanel").classList.add("hidden"));
  refreshAll();
});
