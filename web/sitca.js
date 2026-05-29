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

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
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

async function loadCompanies() {
  const data = await getJSON("/api/sitca/companies");
  const sel = $("#companySelect");
  sel.innerHTML = "";
  data.companies.forEach((c) => {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = `${c.id} ${c.name}`;
    sel.appendChild(opt);
  });
}

function fillMonthSelects(months, curr, prev) {
  const c = $("#companyCurrMonth");
  const p = $("#companyPrevMonth");
  c.innerHTML = "";
  p.innerHTML = "";
  months.forEach((m) => {
    const oc = document.createElement("option");
    oc.value = m;
    oc.textContent = m;
    c.appendChild(oc);
    const op = document.createElement("option");
    op.value = m;
    op.textContent = m;
    p.appendChild(op);
  });
  if (curr) c.value = curr;
  if (prev) p.value = prev;
}

const signedDelta = (n) => {
  if (n == null || Number.isNaN(n)) return "";
  const v = Math.round(n);
  if (v === 0) return "0";
  return (v > 0 ? "+" : "") + v.toLocaleString();
};

async function loadCompanyChanges() {
  const company = $("#companySelect").value;
  if (!company) return;
  const curr = $("#companyCurrMonth").value;
  const prev = $("#companyPrevMonth").value;
  const url = `/api/sitca/company-changes?company=${encodeURIComponent(company)}` +
    (curr ? `&curr=${curr}` : "") +
    (prev ? `&prev=${prev}` : "");
  const data = await getJSON(url);

  // populate month selects if first call (when fields were empty)
  if (!$("#companyCurrMonth").options.length) {
    fillMonthSelects(data.available_months, data.curr_month, data.prev_month);
  }

  $("#companySummary").innerHTML = `
    <div class="pill">投信<strong>${data.company_id} ${data.company_name}</strong></div>
    <div class="pill">比較<strong>${data.prev_month || "—"} → ${data.curr_month}</strong></div>
    <div class="pill">新增<strong>${data.summary.added_count}</strong> 檔</div>
    <div class="pill">退出<strong>${data.summary.removed_count}</strong> 檔</div>
    <div class="pill">持有<strong>${data.summary.kept_count}</strong> 檔</div>
  `;

  $("#addedBadge").textContent = data.summary.added_count;
  $("#removedBadge").textContent = data.summary.removed_count;
  $("#keptBadge").textContent = data.summary.kept_count;

  const renderSide = (rows, tbodySel) => {
    const tb = document.querySelector(tbodySel);
    tb.innerHTML = rows
      .map(
        (r) => `
        <tr>
          <td>${r.stock_code}</td>
          <td>${r.stock_name}</td>
          <td class="num">${r.fund_count}</td>
          <td class="num">${r.best_rank}</td>
          <td class="num">${fmt(Math.round(r.amount))}</td>
        </tr>`
      )
      .join("");
    tb.querySelectorAll("tr").forEach((tr, i) => {
      tr.addEventListener("click", () => openDetail(rows[i].stock_id));
    });
  };
  renderSide(data.added, "#addedTable tbody");
  renderSide(data.removed, "#removedTable tbody");

  const ktb = document.querySelector("#keptTable tbody");
  ktb.innerHTML = data.kept
    .map((r) => {
      const fd = r.fund_delta;
      const ad = r.amount_delta;
      const fclass = fd > 0 ? "delta-pos" : fd < 0 ? "delta-neg" : "";
      const aclass = ad > 0 ? "delta-pos" : ad < 0 ? "delta-neg" : "";
      return `
        <tr>
          <td>${r.stock_code}</td>
          <td>${r.stock_name}</td>
          <td class="num ${fclass}">${signedDelta(fd)} (${r.prev_fund_count}→${r.fund_count})</td>
          <td class="num ${aclass}">${signedDelta(ad / 1)}</td>
        </tr>`;
    })
    .join("");
  ktb.querySelectorAll("tr").forEach((tr, i) => {
    tr.addEventListener("click", () => openDetail(data.kept[i].stock_id));
  });
}

async function refreshAll() {
  try {
    await loadStatus();
    await loadCompanies();
    await Promise.all([
      loadTop(),
      loadSync("buy", "buyTable"),
      loadSync("sell", "sellTable"),
      loadMatrix(),
      loadCompanyChanges(),
    ]);
  } catch (err) {
    console.error(err);
    alert(`載入失敗：${err.message}`);
  }
}

function fmtDuration(sec) {
  if (sec == null) return "—";
  sec = Math.max(0, Math.round(sec));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 0 ? `${m} 分 ${s} 秒` : `${s} 秒`;
}

async function pollScrape(jobId) {
  const panel = $("#scrapeProgress");
  const bar = $("#progressBar");
  const pctEl = $("#progressPercent");
  const statusEl = $("#progressStatus");
  const detailEl = $("#progressDetail");
  const logEl = $("#progressLog");
  panel.classList.remove("hidden");
  bar.style.width = "0%";
  pctEl.textContent = "0%";
  statusEl.className = "";
  statusEl.textContent = "啟動中…";

  while (true) {
    const s = await getJSON(`/api/sitca/scrape-status?id=${jobId}`);
    const total = s.expected_total || 216;
    const done = s.csv_count || 0;
    const pct = total > 0 ? Math.min(100, (done / total) * 100) : 0;
    bar.style.width = pct.toFixed(1) + "%";
    pctEl.textContent = pct.toFixed(0) + "%";

    if (s.status === "done") {
      statusEl.textContent = "✓ 爬取完成";
      statusEl.className = "progress-status-done";
    } else if (s.status === "error") {
      statusEl.textContent = "✗ 發生錯誤";
      statusEl.className = "progress-status-error";
    } else {
      statusEl.textContent = "爬取中…";
      statusEl.className = "";
    }

    const baselineNote = s.baseline ? `（其中 ${s.baseline} 為先前已抓）` : "";
    const eta = s.eta_sec != null && s.status === "running"
      ? `，剩餘約 ${fmtDuration(s.eta_sec)}`
      : "";
    const elapsed = s.elapsed_sec != null
      ? `，已耗時 ${fmtDuration(s.elapsed_sec)}`
      : "";
    detailEl.textContent = `已處理 ${done} / ${total}${baselineNote}${elapsed}${eta}`;
    logEl.textContent = (s.log_tail || []).join("\n");

    if (s.status === "done" || s.status === "error") break;
    await new Promise((r) => setTimeout(r, 2000));
  }
  await refreshAll();
}

async function startScrape() {
  if (!confirm("開始爬取最近 6 個月的基金持股資料？此動作會發送約 216 個 HTTP 請求，耗時約 3-5 分鐘。")) return;
  try {
    const job = await postJSON("/api/sitca/scrape", { months: "" });
    pollScrape(job.job_id);
  } catch (err) {
    alert(`啟動爬蟲失敗：${err.message}`);
  }
}

async function startScrapeLatest() {
  if (!confirm("自動偵測 SITCA 最新月份並強制重抓（覆寫該月份 36 家投信 CSV，~1 分鐘）？")) return;
  try {
    const job = await postJSON("/api/sitca/scrape", {
      months: "auto",
      force: true,
    });
    pollScrape(job.job_id);
  } catch (err) {
    alert(`啟動爬蟲失敗：${err.message}`);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("#refreshBtn").addEventListener("click", refreshAll);
  $("#scrapeBtn").addEventListener("click", startScrape);
  $("#scrapeLatestBtn").addEventListener("click", startScrapeLatest);
  $("#applyBtn").addEventListener("click", () => {
    loadTop();
    loadSync("buy", "buyTable");
    loadSync("sell", "sellTable");
  });
  $("#closeDetail").addEventListener("click", () => $("#detailPanel").classList.add("hidden"));
  $("#companyLoadBtn").addEventListener("click", loadCompanyChanges);
  $("#companySelect").addEventListener("change", loadCompanyChanges);
  refreshAll();
});
