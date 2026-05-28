"use strict";

const $ = (sel) => document.querySelector(sel);
const fmt = (n) => (n == null ? "" : Number(n).toLocaleString());
const heatClass = (v) => {
  if (v <= 0) return "heat-0";
  if (v === 1) return "heat-1";
  if (v === 2) return "heat-2";
  if (v <= 4) return "heat-3";
  if (v <= 7) return "heat-4";
  return "heat-5";
};

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res.json();
}

async function loadStatus() {
  const s = await getJSON("/api/sitca/status");
  $("#statMonths").textContent = s.months.length
    ? `${s.months[0]} → ${s.months[s.months.length - 1]} (${s.months.length})`
    : "尚無資料";
  $("#statCompanies").textContent = fmt(s.company_count);
  $("#statRows").textContent = fmt(s.row_count);
  $("#statStockRows").textContent = fmt(s.stock_row_count);
  if ($("#dataMonth") && s.months.length) {
    $("#dataMonth").textContent = s.months[s.months.length - 1];
  }

  const sel = $("#monthSelect");
  sel.innerHTML = "";
  const allOpt = document.createElement("option");
  allOpt.value = "";
  allOpt.textContent = "全期最大值";
  sel.appendChild(allOpt);
  s.months.forEach((m) => {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = m;
    sel.appendChild(opt);
  });
  if (s.months.length) sel.value = s.months[s.months.length - 1];
  return s;
}

async function loadTop() {
  const month = $("#monthSelect").value;
  const limit = Number($("#topN").value) || 10;
  const data = await getJSON(
    `/api/sitca/top-stocks?month=${encodeURIComponent(month)}&limit=${limit}`
  );
  $("#topTitle").textContent = month
    ? `${month} 月最多基金持有的股票 Top ${limit}（依持有基金數排序）`
    : `半年內最多公司買進的股票 Top ${limit}`;
  const tbody = $("#topTable tbody");
  tbody.innerHTML = "";
  data.items.forEach((it, idx) => {
    const tr = document.createElement("tr");
    const co = month ? it.company_count : it.max_company_count;
    const funds = month ? it.fund_count : "-";
    tr.innerHTML = `
      <td>${idx + 1}</td>
      <td>${it.stock_code || ""}</td>
      <td>${it.stock_name || ""}</td>
      <td>${it.type || ""}</td>
      <td class="num">${co}</td>
      <td class="num">${funds}</td>
      <td class="num">${fmt(Math.round(it.total_amount))}</td>
    `;
    tr.addEventListener("click", () => openDetail(it.stock_id));
    tbody.appendChild(tr);
  });
}

async function loadSync(direction, tableId) {
  const minDelta = Number($("#minDelta").value) || 3;
  const data = await getJSON(
    `/api/sitca/sync?direction=${direction}&min_delta=${minDelta}&limit=50`
  );
  const tbody = document.querySelector(`#${tableId} tbody`);
  tbody.innerHTML = "";
  data.items.forEach((it) => {
    const tr = document.createElement("tr");
    const klass = it.delta >= 0 ? "delta-pos" : "delta-neg";
    const sign = it.delta > 0 ? "+" : "";
    tr.innerHTML = `
      <td>${it.month}</td>
      <td>${it.stock_code || ""}</td>
      <td>${it.stock_name || ""}</td>
      <td class="num">${it.prev_count}</td>
      <td class="num">${it.curr_count}</td>
      <td class="num ${klass}">${sign}${it.delta}</td>
    `;
    tr.addEventListener("click", () => openDetail(it.stock_id));
    tbody.appendChild(tr);
  });
}

async function loadMatrix() {
  const data = await getJSON("/api/sitca/matrix?limit=30");
  const thead = document.querySelector("#matrixTable thead");
  const tbody = document.querySelector("#matrixTable tbody");
  thead.innerHTML = "";
  tbody.innerHTML = "";
  const headerRow = document.createElement("tr");
  headerRow.innerHTML =
    "<th>代號</th><th>名稱</th>" +
    data.months.map((m) => `<th>${m.slice(0, 4)}/${m.slice(4)}</th>`).join("");
  thead.appendChild(headerRow);

  data.rows.forEach((r) => {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${r.stock_code}</td><td>${r.stock_name}</td>` +
      r.counts
        .map((c) => `<td class="cell ${heatClass(c)}">${c || ""}</td>`)
        .join("");
    tr.addEventListener("click", () => openDetail(r.stock_id));
    tbody.appendChild(tr);
  });
}

async function openDetail(stockId) {
  if (!stockId) return;
  const data = await getJSON(`/api/sitca/stock?id=${encodeURIComponent(stockId)}`);
  const panel = $("#detailPanel");
  panel.classList.remove("hidden");
  $("#detailTitle").textContent = `個股詳情：${data.stock_code || ""} ${data.stock_name || ""}`;
  $("#detailSummary").innerHTML = data.monthly_stats
    .map(
      (m) =>
        `<div class="pill">${m.month}<strong>${m.company_count}</strong> 家｜${m.fund_count} 檔基金｜${fmt(Math.round(m.total_amount))}</div>`
    )
    .join("");
  const tbody = $("#detailTable tbody");
  tbody.innerHTML = data.rows
    .map(
      (r) => `
      <tr>
        <td>${r.year_month}</td>
        <td>${r.company_name}</td>
        <td>${r.fund_name}</td>
        <td class="num">${r.rank}</td>
        <td>${r.target_type}</td>
        <td class="num">${r.amount}</td>
        <td class="num">${r.pct_of_nav}</td>
      </tr>`
    )
    .join("");
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function refreshAll() {
  try {
    await loadStatus();
    await Promise.all([
      loadTop(),
      loadSync("buy", "buyTable"),
      loadSync("sell", "sellTable"),
      loadMatrix(),
    ]);
  } catch (err) {
    console.error(err);
    alert(`載入失敗：${err.message}`);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("#refreshBtn").addEventListener("click", refreshAll);
  $("#applyBtn").addEventListener("click", () => {
    loadTop();
    loadSync("buy", "buyTable");
    loadSync("sell", "sellTable");
  });
  $("#closeDetail").addEventListener("click", () => $("#detailPanel").classList.add("hidden"));
  refreshAll();
});
