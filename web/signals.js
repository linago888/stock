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

let watchlistData = null;      // per-stock aggregated (from watchlist-top)
let watchlistRawMeta = null;   // raw meta (for total counts)
let wlActiveLabels = new Set();

const TECH_LABEL_MAP = {
  breakout_zone: { text: "🎯 突破區", cls: "tech-breakout" },
  flying: { text: "🚀 已飛", cls: "tech-flying" },
  weak: { text: "📉 弱勢", cls: "tech-weak" },
  quiet: { text: "😴 平淡", cls: "tech-quiet" },
};

function renderWatchlist() {
  if (!watchlistData) return;
  const tbody = document.querySelector("#watchlistTable tbody");
  const items = watchlistData.items.filter((it) => {
    if (wlActiveLabels.size && !(it.labels_hit || []).some((l) => wlActiveLabels.has(l))) return false;
    return true;
  });
  tbody.innerHTML = items.map((it, idx) => {
    const own = new Set(it.own_labels_hit || []);
    const chips = (it.labels_hit || []).map((l) =>
      `<span class="label-chip${own.has(l) ? '' : ' proxy'}">${l}${own.has(l) ? '' : '*'}</span>`
    ).join(" ");
    const tech = it.tech || {};
    const tl = TECH_LABEL_MAP[tech.tech_signal] || { text: "—", cls: "" };
    const techCell = tech.tech_signal
      ? `<span class="tech-badge ${tl.cls}">${tl.text}</span>`
      : "—";
    const mom = tech.mom_30d_pct;
    const momCell = mom != null
      ? `<span class="${mom > 0 ? 'delta-pos' : mom < 0 ? 'delta-neg' : ''}">${mom > 0 ? '+' : ''}${mom.toFixed(1)}%</span>`
      : "—";
    const subjectList = (it.subjects || []).slice(0, 6).map((s) => {
      const proxyMark = /^代/.test((s.subject || "").trim())
        ? '<span class="proxy-mark" title="代子公司公告">代</span> ' : "";
      const txt = (s.subject || "").substring(0, 70);
      return `<li>${s.date} ${proxyMark}${txt}${(s.subject || "").length > 70 ? "…" : ""}</li>`;
    }).join("");
    const more = (it.subjects || []).length > 6 ? `<li class="hint">…共 ${it.subjects.length} 筆</li>` : "";
    return `
      <tr>
        <td class="num">${idx + 1}</td>
        <td>${it.co_id}</td>
        <td>${it.name}</td>
        <td class="num score-cell">${it.score}</td>
        <td class="num">${it.signal_count}</td>
        <td><div class="label-chips">${chips}</div></td>
        <td>${techCell}</td>
        <td class="num">${momCell}</td>
        <td>${it.latest_date}</td>
        <td><ul class="subject-list">${subjectList}${more}</ul></td>
      </tr>
    `;
  }).join("");
}

async function loadTopRank() {
  const data = await getJSON("/api/signals/watchlist-top?limit=10");
  $("#topRankMeta").textContent = data.items && data.items.length
    ? `${data.total_signals} 筆訊號涵蓋 ${data.stocks_with_signal} 檔股票 · 掃描 ${data.backfill_window?.start} → ${data.backfill_window?.end}`
    : "尚無資料";
  const tbody = document.querySelector("#topRankTable tbody");
  const techLabelMap = {
    breakout_zone: { text: "🎯 突破區", cls: "tech-breakout" },
    flying: { text: "🚀 已飛", cls: "tech-flying" },
    weak: { text: "📉 弱勢", cls: "tech-weak" },
    quiet: { text: "😴 平淡", cls: "tech-quiet" },
  };
  tbody.innerHTML = data.items.map((it, idx) => {
    const own = new Set(it.own_labels_hit || []);
    const chips = it.labels_hit.map((l) =>
      `<span class="label-chip${own.has(l) ? '' : ' proxy'}">${l}${own.has(l) ? '' : '*'}</span>`
    ).join(" ");
    const latest = it.subjects[0]?.subject || "";
    const bd = `訊號 ${it.signal_score} + 多元 ${it.diversity_bonus} + 集中 ${it.concentration_bonus} + 近期 ${it.recency_bonus}${it.tech_bonus ? ` + 技術 ${it.tech_bonus > 0 ? '+' : ''}${it.tech_bonus}` : ""}`;
    const proxyPct = it.proxy_ratio != null ? Math.round(it.proxy_ratio * 100) : 0;
    const tech = it.tech || {};
    const tl = techLabelMap[tech.tech_signal] || { text: "—", cls: "" };
    const techCell = tech.tech_signal
      ? `<span class="tech-badge ${tl.cls}">${tl.text}</span><div class="subj-preview">收 ${tech.last_close} · vs 20 高 ${Math.round((tech.close_vs_20d_high || 0) * 100)}% · 量比 ${tech.volume_vs_20d_avg}×</div>`
      : "—";
    const mom = tech.mom_30d_pct;
    const momCell = mom != null
      ? `<span class="${mom > 0 ? 'delta-pos' : mom < 0 ? 'delta-neg' : ''}">${mom > 0 ? '+' : ''}${mom.toFixed(1)}%</span>`
      : "—";
    return `
      <tr class="top-rank-row">
        <td><b>${idx + 1}</b></td>
        <td>${it.co_id}</td>
        <td>${it.name}</td>
        <td class="num score-cell">${it.score}</td>
        <td class="num">${it.signal_count}${proxyPct >= 50 ? `<div class="proxy-note">代子${proxyPct}%</div>` : ""}</td>
        <td><div class="label-chips">${chips}</div></td>
        <td>${techCell}</td>
        <td class="num">${momCell}</td>
        <td>${it.latest_date}<div class="subj-preview">${latest.substring(0, 40)}${latest.length > 40 ? "…" : ""}</div></td>
        <td class="score-detail">${bd}</td>
      </tr>
    `;
  }).join("");
}

async function loadBrewing() {
  const data = await getJSON("/api/signals/watchlist-top?limit=10&mom_min=5&mom_max=14");
  $("#brewingMeta").textContent = data.items && data.items.length
    ? `${data.items.length} 檔 · 掃描 ${data.backfill_window?.start} → ${data.backfill_window?.end}`
    : "當前無符合條件（動能 5-14% 且未飆漲）";
  const techLabelMap = {
    breakout_zone: { text: "🎯 突破區", cls: "tech-breakout" },
    flying: { text: "🚀 已飛", cls: "tech-flying" },
    weak: { text: "📉 弱勢", cls: "tech-weak" },
    quiet: { text: "😴 平淡", cls: "tech-quiet" },
  };
  const tbody = document.querySelector("#brewingTable tbody");
  tbody.innerHTML = data.items.map((it, idx) => {
    const own = new Set(it.own_labels_hit || []);
    const chips = it.labels_hit.map((l) =>
      `<span class="label-chip${own.has(l) ? '' : ' proxy'}">${l}${own.has(l) ? '' : '*'}</span>`
    ).join(" ");
    const latest = it.subjects[0]?.subject || "";
    const tech = it.tech || {};
    const tl = techLabelMap[tech.tech_signal] || { text: "—", cls: "" };
    const techCell = tech.tech_signal
      ? `<span class="tech-badge ${tl.cls}">${tl.text}</span>`
      : "—";
    const mom = tech.mom_30d_pct;
    const momCell = mom != null
      ? `<span class="${mom > 0 ? 'delta-pos' : mom < 0 ? 'delta-neg' : ''}">${mom > 0 ? '+' : ''}${mom.toFixed(1)}%</span>`
      : "—";
    return `
      <tr class="top-rank-row">
        <td><b>${idx + 1}</b></td>
        <td>${it.co_id}</td>
        <td>${it.name}</td>
        <td class="num score-cell">${it.score}</td>
        <td class="num">${it.signal_count}</td>
        <td><div class="label-chips">${chips}</div></td>
        <td>${techCell}</td>
        <td class="num">${momCell}</td>
        <td>${it.latest_date}<div class="subj-preview">${latest.substring(0, 40)}${latest.length > 40 ? "…" : ""}</div></td>
      </tr>
    `;
  }).join("");
}

async function loadWatchlist() {
  const hideSurged = document.querySelector("#wlHideSurged")?.checked !== false;
  // Aggregated per-stock (all stocks, not just Top 10)
  const url = `/api/signals/watchlist-top?limit=999&exclude_surged=${hideSurged ? 1 : 0}`;
  const data = await getJSON(url);
  watchlistData = data;
  // Raw meta for total counts (fetch once, cached)
  if (!watchlistRawMeta) {
    try { watchlistRawMeta = await getJSON("/api/signals/watchlist"); }
    catch { watchlistRawMeta = null; }
  }
  const raw = watchlistRawMeta || {};
  const rawInfo = raw.matched_count != null
    ? `訊號 ${raw.matched_count} 筆彙整為 ${data.items.length} 檔股票（未飆 ${raw.not_surged_count} / 已飆 ${raw.already_surged_count}）`
    : `${data.items.length} 檔股票`;
  const win = raw.backfill_window;
  const ts = raw.generated_at ? raw.generated_at.slice(0, 16).replace("T", " ") : "";
  $("#watchlistMeta").textContent = data.items.length
    ? `${ts} · ${rawInfo}${win ? ` · 掃描 ${win.start} → ${win.end}` : ""}`
    : "尚未掃描（本機執行 news_signals/scan_watchlist_deep.py）";
  // Chip filters from aggregated labels
  const labelSet = new Set();
  (data.items || []).forEach((it) => (it.labels_hit || []).forEach((l) => labelSet.add(l)));
  const chipEl = $("#wlLabelChips");
  chipEl.innerHTML = "";
  Array.from(labelSet).sort().forEach((lbl) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip-filter" + (wlActiveLabels.has(lbl) ? " on" : "");
    b.textContent = lbl;
    b.addEventListener("click", () => {
      if (wlActiveLabels.has(lbl)) { wlActiveLabels.delete(lbl); b.classList.remove("on"); }
      else { wlActiveLabels.add(lbl); b.classList.add("on"); }
      renderWatchlist();
    });
    chipEl.appendChild(b);
  });
  const hs = document.querySelector("#wlHideSurged");
  if (hs && !hs._wired) {
    hs._wired = true;
    hs.addEventListener("change", loadWatchlist);
  }
  renderWatchlist();
}

async function refreshAll() {
  try {
    await loadStatus();
    await Promise.all([loadTopRank(), loadBrewing(), loadWatchlist(), loadComparison(), loadWhitelist(), loadStocks("poc")]);
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
