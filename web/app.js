const form = document.querySelector("#screenForm");
const statusEl = document.querySelector("#status");
const resultsEl = document.querySelector("#results");
const summaryEl = document.querySelector("#summary");
const modeEl = document.querySelector("#mode");
const universeCountEl = document.querySelector("#universeCount");
const refreshUniverseBtn = document.querySelector("#refreshUniverse");
const clearCriteriaBtn = document.querySelector("#clearCriteria");
const closeCacheCountEl = document.querySelector("#closeCacheCount");
const closeCacheDateEl = document.querySelector("#closeCacheDate");
const closeListEl = document.querySelector("#closeList");
const closeRowsEl = document.querySelector("#closeRows");
const toggleCloseListBtn = document.querySelector("#toggleCloseList");
const refreshCloseStatusBtn = document.querySelector("#refreshCloseStatus");
const klinePanelEl = document.querySelector("#klinePanel");
const klineTitleEl = document.querySelector("#klineTitle");
const klineCanvasEl = document.querySelector("#klineCanvas");
const klineSignalsEl = document.querySelector("#klineSignals");
const closeKlineBtn = document.querySelector("#closeKline");
const lookupEpsBtn = document.querySelector("#lookupEps");
const epsPanelEl = document.querySelector("#epsPanel");
const progressPanelEl = document.querySelector("#progressPanel");
const progressTextEl = document.querySelector("#progressText");
const progressPercentEl = document.querySelector("#progressPercent");
const progressBarEl = document.querySelector("#progressBar");
const progressDetailEl = document.querySelector("#progressDetail");
let currentKline = { points: [], buyPlan: null, symbol: "", name: "", matchedCriteria: [] };
let lastData = null;

const SIGNAL_LABELS = {
  above_ma20: "站上20日線",
  above_ma60: "站上60日線",
  ma20_slope_up: "20日線上彎",
  breakout_20d: "突破20日高",
  near_breakout_20d: "接近/突破20日高",
  volume_expansion: "量能放大",
  strong_volume: "強量突破",
  kd_golden: "KD黃金交叉",
  kd_bullish: "KD多方",
  macd_turn_positive: "MACD翻正",
  macd_rising: "MACD升溫",
  rsi_healthy: "RSI健康強勢",
  not_extended: "乖離可控",
  financial_growth: "營收成長",
  eps_positive: "EPS為正",
  roe_quality: "ROE佳",
  reasonable_valuation: "估值未過高",
};

const fmt = (value, digits = 2) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "NA";
  return Number(value).toLocaleString("zh-TW", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
};

const escapeHtml = (value) =>
  String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");

const setStatus = (text, mode = "") => {
  statusEl.textContent = text;
  statusEl.className = `status ${mode}`.trim();
};

const setModeVisibility = () => {
  const marketMode = modeEl.value === "market";
  document.querySelectorAll(".manual-only").forEach((node) => node.classList.toggle("hidden", marketMode));
  document.querySelectorAll(".market-only").forEach((node) => node.classList.toggle("hidden", !marketMode));
};

const getCriteria = () =>
  [...document.querySelectorAll('input[name="criteria"]:checked')].map((node) => node.value);

const updateUniverse = async () => {
  try {
    const response = await fetch("/api/universe");
    const data = await response.json();
    universeCountEl.textContent = data.count ? `${data.count} 檔` : "尚未建立";
  } catch {
    universeCountEl.textContent = "讀取失敗";
  }
};

const renderCloseRows = (rows) => {
  if (!rows.length) {
    closeRowsEl.innerHTML = `<div class="close-empty">尚未蒐集任何收盤價快取</div>`;
    return;
  }
  closeRowsEl.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>代號</th>
          <th>名稱</th>
          <th>市場</th>
          <th>日期</th>
          <th>收盤價</th>
          <th>K線筆數</th>
        </tr>
      </thead>
      <tbody>
        ${rows
          .map(
            (row) => `
            <tr class="clickable-row" data-symbol="${escapeHtml(row.symbol)}" data-name="${escapeHtml(row.name || "")}">
              <td>${escapeHtml(row.symbol)}</td>
              <td>${escapeHtml(row.name || "-")}</td>
              <td>${escapeHtml(row.market || "-")}</td>
              <td>${escapeHtml(row.date || "-")}</td>
              <td>${fmt(row.close, 2)}</td>
              <td>${escapeHtml(row.rows ?? 0)}</td>
            </tr>
          `
          )
          .join("")}
      </tbody>
    </table>
  `;
  closeRowsEl.querySelectorAll(".clickable-row").forEach((row) => {
    row.addEventListener("click", () => showKline(row.dataset.symbol, row.dataset.name));
  });
};

const updateCloseStatus = async () => {
  try {
    const response = await fetch("/api/price-status?limit=300");
    const data = await response.json();
    closeCacheCountEl.textContent = `${data.cached_count}/${data.total_universe} 檔`;
    closeCacheDateEl.textContent = `最新日期 ${data.latest_date || "-"}`;
    renderCloseRows(data.rows || []);
  } catch {
    closeCacheCountEl.textContent = "讀取失敗";
    closeCacheDateEl.textContent = "最新日期 -";
  }
};

const updateSummary = (data) => {
  const highest = data.all_results.length ? data.all_results[0].matched_count : null;
  summaryEl.innerHTML = `
    <div><span>分析檔數</span><strong>${data.meta.symbols_scored}/${data.meta.symbols_requested}</strong></div>
    <div><span>達標檔數</span><strong>${data.results.length}</strong></div>
    <div><span>最高命中</span><strong>${highest === null ? "-" : highest}</strong></div>
  `;
};

const updateProgress = (job) => {
  const percent = Number(job.percent || 0);
  progressPanelEl.classList.remove("hidden");
  progressTextEl.textContent = job.status === "done" ? "處理完成" : "處理中";
  progressPercentEl.textContent = `${fmt(percent, 1)}%`;
  progressBarEl.style.width = `${Math.max(0, Math.min(100, percent))}%`;
  progressDetailEl.textContent = `已處理 ${job.completed || 0} / ${job.total || 0}，成功 ${job.scored || 0}，錯誤 ${job.errors || 0}`;
};

const waitForJob = async (jobId) => {
  while (true) {
    const response = await fetch(`/api/job-status?id=${encodeURIComponent(jobId)}`);
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || "讀取處理進度失敗");
    updateProgress(job);
    if (job.status === "done") return job.result;
    if (job.status === "error") throw new Error(job.error || "背景處理失敗");
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
};

const metricChips = (metrics) => {
  const rows = [
    ["MA20", metrics.ma20],
    ["MA60", metrics.ma60],
    ["K", metrics.k],
    ["D", metrics.d],
    ["MACD Hist", metrics.macd_hist],
    ["RSI", metrics.rsi14],
    ["量比", metrics.vol_ratio],
    ["ATR%", metrics.atr_pct],
  ];
  return rows.map(([label, value]) => `<span class="chip">${label} ${fmt(value, label === "量比" ? 1 : 2)}</span>`).join("");
};

const signalChips = (matchedCriteria) => {
  const active = matchedCriteria || [];
  if (!active.length) return `<span class="chip">尚無命中的 K 線指標</span>`;
  return active
    .slice(0, 12)
    .map((key) => `<span class="chip signal">${escapeHtml(SIGNAL_LABELS[key] || key)}</span>`)
    .join("");
};

const signalIconClass = (key) => {
  if (key.includes("ma")) return "ma";
  if (key.includes("breakout")) return "breakout";
  if (key.includes("volume")) return "volume";
  if (key.includes("kd")) return "kd";
  if (key.includes("macd")) return "macd";
  if (key.includes("rsi")) return "rsi";
  if (key.includes("extended")) return "risk";
  return "base";
};

const signalIcons = (matchedCriteria) => {
  const active = matchedCriteria || [];
  if (!active.length) return "";
  return active
    .slice(0, 13)
    .map((key) => {
      const label = SIGNAL_LABELS[key] || key;
      return `<span class="signal-icon signal-icon-${signalIconClass(key)}" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}"></span>`;
    })
    .join("");
};

const drawChart = (canvas, points) => {
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);

  const width = rect.width;
  const height = rect.height;
  const pad = 18;
  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = "#dbe3dd";
  ctx.lineWidth = 1;
  for (let i = 0; i < 4; i += 1) {
    const y = pad + ((height - pad * 2) * i) / 3;
    ctx.beginPath();
    ctx.moveTo(pad, y);
    ctx.lineTo(width - pad, y);
    ctx.stroke();
  }

  const values = points.flatMap((point) => [point.close, point.ma20, point.ma60]).filter((value) => value !== null);
  if (!values.length) return;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const spread = max - min || 1;
  const xFor = (index) => pad + ((width - pad * 2) * index) / Math.max(1, points.length - 1);
  const yFor = (value) => height - pad - ((value - min) / spread) * (height - pad * 2);

  const line = (key, color, widthPx) => {
    ctx.strokeStyle = color;
    ctx.lineWidth = widthPx;
    ctx.beginPath();
    let started = false;
    points.forEach((point, index) => {
      const value = point[key];
      if (value === null) return;
      const x = xFor(index);
      const y = yFor(value);
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();
  };

  line("ma60", "#b7791f", 1.5);
  line("ma20", "#2459a6", 1.5);
  line("close", "#15724a", 2.4);
};

const klineScale = (canvas, points, buyPlan = null) => {
  const rect = canvas.getBoundingClientRect();
  const pad = { top: 22, right: 74, bottom: 24, left: 42 };
  const visible = points.filter((point) => point.open !== null && point.high !== null && point.low !== null && point.close !== null);
  const prices = visible
    .flatMap((point) => [point.high, point.low, point.ma20, point.ma60])
    .concat(buyPlan ? [buyPlan.entry_low, buyPlan.entry_high, buyPlan.stop_loss, buyPlan.target_1] : [])
    .filter((value) => value !== null && value !== undefined && !Number.isNaN(Number(value)));
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const padding = (max - min || 1) * 0.08;
  const low = min - padding;
  const high = max + padding;
  const plotWidth = rect.width - pad.left - pad.right;
  const innerHeight = rect.height - pad.top - pad.bottom;
  const gap = 18;
  const priceHeight = Math.max(170, innerHeight * 0.58);
  const indicatorHeight = Math.max(72, (innerHeight - priceHeight - gap * 2) / 2);
  const panels = {
    price: { top: pad.top, height: priceHeight },
    kd: { top: pad.top + priceHeight + gap, height: indicatorHeight },
    macd: { top: pad.top + priceHeight + gap + indicatorHeight + gap, height: indicatorHeight },
  };
  const slot = plotWidth / Math.max(1, visible.length);
  const macdValues = visible
    .flatMap((point) => [point.macd, point.macd_signal, point.macd_hist])
    .filter((value) => value !== null && value !== undefined && !Number.isNaN(Number(value)));
  const macdMaxAbs = Math.max(0.01, ...macdValues.map((value) => Math.abs(Number(value)))) * 1.18;
  return {
    rect,
    pad,
    panels,
    visible,
    low,
    high,
    plotWidth,
    plotHeight: priceHeight,
    slot,
    xFor: (index) => pad.left + slot * index + slot / 2,
    yFor: (value) => panels.price.top + ((high - value) / (high - low || 1)) * panels.price.height,
    yForKD: (value) => panels.kd.top + ((100 - value) / 100) * panels.kd.height,
    yForMacd: (value) => panels.macd.top + ((macdMaxAbs - value) / (macdMaxAbs * 2)) * panels.macd.height,
    macdMaxAbs,
    priceForY: (y) => high - ((y - panels.price.top) / panels.price.height) * (high - low || 1),
    indexForX: (x) => Math.max(0, Math.min(visible.length - 1, Math.round((x - pad.left - slot / 2) / slot))),
  };
};

const drawPriceLabel = (ctx, text, x, y, color) => {
  ctx.font = "12px Microsoft JhengHei, Arial";
  const width = ctx.measureText(text).width + 10;
  ctx.fillStyle = color;
  ctx.fillRect(x, y - 9, width, 18);
  ctx.fillStyle = "#fff";
  ctx.fillText(text, x + 5, y + 4);
};

const drawKline = (canvas, points, buyPlan = null, hoverIndex = null) => {
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);

  const width = rect.width;
  const height = rect.height;
  ctx.clearRect(0, 0, width, height);

  const scale = klineScale(canvas, points, buyPlan);
  const { pad, panels, visible, low, high, slot, xFor, yFor, yForKD, yForMacd, macdMaxAbs, priceForY } = scale;
  if (!visible.length) return;
  const candleWidth = Math.max(3, Math.min(11, slot * 0.58));

  ctx.strokeStyle = "#dbe3dd";
  ctx.lineWidth = 1;
  for (let i = 0; i < 6; i += 1) {
    const price = high - ((high - low) * i) / 5;
    const y = yFor(price);
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
    ctx.fillStyle = "#66736b";
    ctx.font = "12px Microsoft JhengHei, Arial";
    ctx.fillText(fmt(price, 2), width - pad.right + 8, y + 4);
  }

  visible.forEach((point, index) => {
    const x = xFor(index);
    const up = point.close >= point.open;
    const color = up ? "#b42318" : "#15724a";
    const yOpen = yFor(point.open);
    const yClose = yFor(point.close);
    const yHigh = yFor(point.high);
    const yLow = yFor(point.low);
    const top = Math.min(yOpen, yClose);
    const bodyHeight = Math.max(1, Math.abs(yClose - yOpen));

    ctx.strokeStyle = color;
    ctx.fillStyle = up ? "rgba(180, 35, 24, 0.18)" : "rgba(21, 114, 74, 0.18)";
    ctx.beginPath();
    ctx.moveTo(x, yHigh);
    ctx.lineTo(x, yLow);
    ctx.stroke();
    ctx.fillRect(x - candleWidth / 2, top, candleWidth, bodyHeight);
    ctx.strokeRect(x - candleWidth / 2, top, candleWidth, bodyHeight);
  });

  const drawAverage = (key, color) => {
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    let started = false;
    visible.forEach((point, index) => {
      if (point[key] === null) return;
      const x = xFor(index);
      const y = yFor(point[key]);
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();
  };

  drawAverage("ma20", "#2459a6");
  drawAverage("ma60", "#b7791f");

  const panelBottom = (panel) => panel.top + panel.height;
  const drawPanelFrame = (panel, label) => {
    ctx.strokeStyle = "#dbe3dd";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.left, panel.top);
    ctx.lineTo(width - pad.right, panel.top);
    ctx.moveTo(pad.left, panelBottom(panel));
    ctx.lineTo(width - pad.right, panelBottom(panel));
    ctx.stroke();
    ctx.fillStyle = "#66736b";
    ctx.font = "12px Microsoft JhengHei, Arial";
    ctx.fillText(label, 8, panel.top + 15);
  };

  const drawIndicatorLine = (key, yForValue, color, lineWidth = 1.4) => {
    ctx.strokeStyle = color;
    ctx.lineWidth = lineWidth;
    ctx.beginPath();
    let started = false;
    visible.forEach((point, index) => {
      if (point[key] === null || point[key] === undefined) return;
      const x = xFor(index);
      const y = yForValue(point[key]);
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();
  };

  drawPanelFrame(panels.kd, "KD");
  [20, 50, 80].forEach((value) => {
    const y = yForKD(value);
    ctx.strokeStyle = value === 50 ? "#dbe3dd" : "rgba(219, 227, 221, 0.72)";
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
    ctx.fillStyle = "#66736b";
    ctx.fillText(String(value), width - pad.right + 8, y + 4);
  });
  drawIndicatorLine("k", yForKD, "#2459a6", 1.6);
  drawIndicatorLine("d", yForKD, "#b7791f", 1.6);

  drawPanelFrame(panels.macd, "MACD");
  const zeroY = yForMacd(0);
  ctx.strokeStyle = "#aeb9b1";
  ctx.beginPath();
  ctx.moveTo(pad.left, zeroY);
  ctx.lineTo(width - pad.right, zeroY);
  ctx.stroke();
  visible.forEach((point, index) => {
    if (point.macd_hist === null || point.macd_hist === undefined) return;
    const x = xFor(index);
    const y = yForMacd(point.macd_hist);
    const barWidth = Math.max(2, Math.min(8, slot * 0.48));
    ctx.fillStyle = point.macd_hist >= 0 ? "rgba(180, 35, 24, 0.46)" : "rgba(21, 114, 74, 0.46)";
    ctx.fillRect(x - barWidth / 2, Math.min(y, zeroY), barWidth, Math.max(1, Math.abs(zeroY - y)));
  });
  drawIndicatorLine("macd", yForMacd, "#2459a6", 1.4);
  drawIndicatorLine("macd_signal", yForMacd, "#b7791f", 1.4);
  ctx.fillStyle = "#66736b";
  ctx.fillText(fmt(macdMaxAbs, 2), width - pad.right + 8, panels.macd.top + 4);
  ctx.fillText(fmt(-macdMaxAbs, 2), width - pad.right + 8, panelBottom(panels.macd) + 4);

  if (buyPlan) {
    const entryY1 = yFor(buyPlan.entry_low);
    const entryY2 = yFor(buyPlan.entry_high);
    const bandTop = Math.min(entryY1, entryY2);
    const bandHeight = Math.max(3, Math.abs(entryY2 - entryY1));
    ctx.fillStyle = "rgba(36, 89, 166, 0.12)";
    ctx.fillRect(pad.left, bandTop, width - pad.left - pad.right, bandHeight);
    ctx.strokeStyle = "#2459a6";
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    ctx.moveTo(pad.left, entryY1);
    ctx.lineTo(width - pad.right, entryY1);
    ctx.moveTo(pad.left, entryY2);
    ctx.lineTo(width - pad.right, entryY2);
    ctx.stroke();
    drawPriceLabel(ctx, `買 ${fmt(buyPlan.entry_low, 2)}-${fmt(buyPlan.entry_high, 2)}`, width - pad.right - 118, bandTop - 10, "#2459a6");

    const stopY = yFor(buyPlan.stop_loss);
    ctx.strokeStyle = "#b42318";
    ctx.beginPath();
    ctx.moveTo(pad.left, stopY);
    ctx.lineTo(width - pad.right, stopY);
    ctx.stroke();
    ctx.setLineDash([]);
    drawPriceLabel(ctx, `停 ${fmt(buyPlan.stop_loss, 2)}`, width - pad.right - 72, stopY + 12, "#b42318");
  }

  const latest = visible[visible.length - 1];
  const latestY = yFor(latest.close);
  ctx.strokeStyle = "#17201a";
  ctx.setLineDash([3, 3]);
  ctx.beginPath();
  ctx.moveTo(pad.left, latestY);
  ctx.lineTo(width - pad.right, latestY);
  ctx.stroke();
  ctx.setLineDash([]);
  drawPriceLabel(ctx, `現 ${fmt(latest.close, 2)}`, width - pad.right + 6, latestY, "#17201a");

  ctx.fillStyle = "#66736b";
  ctx.font = "12px Microsoft JhengHei, Arial";
  ctx.fillText(`${visible[0].date} - ${visible[visible.length - 1].date}`, pad.left, height - 8);
  ctx.fillText(`高 ${fmt(high, 2)} / 低 ${fmt(low, 2)}`, pad.left, 15);

  if (hoverIndex !== null && visible[hoverIndex]) {
    const point = visible[hoverIndex];
    const x = xFor(hoverIndex);
    const y = yFor(point.close);
    ctx.strokeStyle = "rgba(23, 32, 26, 0.65)";
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(x, pad.top);
    ctx.lineTo(x, height - pad.bottom);
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
    ctx.setLineDash([]);

    const priceAtMouse = priceForY(y);
    drawPriceLabel(ctx, fmt(priceAtMouse, 2), width - pad.right + 6, y, "#2459a6");
    const tooltip = `${point.date}  開 ${fmt(point.open, 2)} 高 ${fmt(point.high, 2)} 低 ${fmt(point.low, 2)} 收 ${fmt(point.close, 2)}  K ${fmt(point.k, 1)} D ${fmt(point.d, 1)}  MACD ${fmt(point.macd, 2)} / ${fmt(point.macd_signal, 2)}`;
    const tooltipWidth = Math.min(width - 20, ctx.measureText(tooltip).width + 14);
    const tipX = Math.min(Math.max(10, x - tooltipWidth / 2), width - tooltipWidth - 10);
    ctx.fillStyle = "rgba(23, 32, 26, 0.92)";
    ctx.fillRect(tipX, pad.top + 8, tooltipWidth, 26);
    ctx.fillStyle = "#fff";
    ctx.fillText(tooltip, tipX + 7, pad.top + 26);
  }
};

const renderEpsPanel = (data) => {
  const searchLinks = data.search_urls || {};
  epsPanelEl.classList.remove("hidden");
  epsPanelEl.innerHTML = `
    <div class="eps-grid">
      <div><span>今年預估 EPS</span><strong>${data.estimated_eps === null ? "待網路確認" : fmt(data.estimated_eps, 2)}</strong></div>
      <div><span>來源 EPS</span><strong>${fmt(data.trailing_eps, 2)}</strong></div>
      <div><span>券商目標價中位數</span><strong>${fmt(data.target_price_median, 2)}</strong></div>
      <div><span>資料日期</span><strong>${escapeHtml(data.rating_date || "-")}</strong></div>
    </div>
    <p>${escapeHtml(data.note || "")}</p>
    <div class="eps-links">
      <a href="${escapeHtml(data.source_url)}" target="_blank" rel="noopener">鉅亨資料頁</a>
      <a href="${escapeHtml(searchLinks.google || "#")}" target="_blank" rel="noopener">Google 搜尋今年預估 EPS</a>
      <a href="${escapeHtml(searchLinks.bing || "#")}" target="_blank" rel="noopener">Bing 搜尋今年預估 EPS</a>
    </div>
  `;
};

const lookupEps = async (symbol) => {
  if (!symbol) return;
  epsPanelEl.classList.remove("hidden");
  epsPanelEl.innerHTML = `<p>正在網路搜尋 ${escapeHtml(symbol)} 今年預估 EPS...</p>`;
  const response = await fetch(`/api/eps-estimate?symbol=${encodeURIComponent(symbol)}`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "EPS 查詢失敗");
  renderEpsPanel(data);
};

const showKline = async (symbol, name = "", existingChart = null, existingBuyPlan = null, matchedCriteria = []) => {
  if (!symbol) return;
  klinePanelEl.classList.remove("hidden");
  klineTitleEl.textContent = `${symbol}${name ? ` ${name}` : ""}`;
  epsPanelEl.classList.add("hidden");
  epsPanelEl.innerHTML = "";
  let chart = existingChart;
  let buyPlan = existingBuyPlan;
  if (!chart || !chart.length || chart[0].open === undefined) {
    const response = await fetch(`/api/price-chart?symbol=${encodeURIComponent(symbol)}&limit=120`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "讀取 K 線失敗");
    chart = data.chart;
    buyPlan = data.buy_plan;
    symbol = data.symbol;
    name = data.name || name;
    klineTitleEl.textContent = `${data.symbol}${data.name ? ` ${data.name}` : ""}`;
  }
  currentKline = { points: chart, buyPlan, symbol, name, matchedCriteria };
  klineSignalsEl.innerHTML = signalIcons(matchedCriteria);
  klineSignalsEl.classList.toggle("hidden", !matchedCriteria.length);
  drawKline(klineCanvasEl, chart, buyPlan);
  klinePanelEl.scrollIntoView({ behavior: "smooth", block: "start" });
};

const renderErrors = (errors) => {
  if (!errors || !errors.length) return "";
  return `
    <div class="errors">
      <strong>部分股票無法分析</strong>
      <ul>${errors.map((error) => `<li>${escapeHtml(error.symbol ? `${error.symbol}: ` : "")}${escapeHtml(error.message)}</li>`).join("")}</ul>
    </div>
  `;
};

const renderResults = (data) => {
  lastData = data;
  updateSummary(data);
  if (!data.results.length) {
    resultsEl.className = "results empty";
    resultsEl.innerHTML = `
      <div class="empty-state">
        <h3>沒有股票符合條件</h3>
        <p>可以降低至少符合指標數，或增加勾選的 K 線指標。系統不再用分數當入選條件。</p>
      </div>
      ${renderErrors(data.errors)}
    `;
    return;
  }

  resultsEl.className = "results";
  resultsEl.innerHTML = data.results
    .map(
      (item, index) => `
      <article class="result">
        <div class="result-head clickable-result" data-symbol="${escapeHtml(item.symbol)}" data-name="${escapeHtml(item.name || "")}">
          <div>
            <div class="symbol">${index + 1}. ${escapeHtml(item.symbol)}</div>
            <div class="name">${escapeHtml(item.name || "未提供財務名稱")}</div>
          </div>
          <div class="metric score"><span>命中指標</span><strong>${item.matched_count ?? 0}</strong></div>
          <div class="metric"><span>收盤價</span><strong>${fmt(item.price, 2)}</strong></div>
          <div class="metric"><span>參考停損</span><strong>${fmt(item.stop_loss, 2)}</strong></div>
        </div>
        <div class="result-body">
          <div class="chart-stack">
            <canvas class="chart" data-symbol="${escapeHtml(item.symbol)}"></canvas>
            <div class="signal-strip">${signalIcons(item.matched_criteria)}</div>
          </div>
          <div class="detail-grid">
            <div class="buy-plan">
              <h3>建議買進點位</h3>
              <div class="buy-grid">
                <div><span>型態</span><strong>${escapeHtml(item.buy_plan?.type || "-")}</strong></div>
                <div><span>買進區間</span><strong>${fmt(item.buy_plan?.entry_low, 2)} - ${fmt(item.buy_plan?.entry_high, 2)}</strong></div>
                <div><span>停損</span><strong>${fmt(item.buy_plan?.stop_loss, 2)}</strong></div>
                <div><span>目標</span><strong>${fmt(item.buy_plan?.target_1, 2)} / ${fmt(item.buy_plan?.target_2, 2)}</strong></div>
              </div>
              <p>${escapeHtml(item.buy_plan?.reason || "")}</p>
              <button class="secondary eps-button" type="button" data-symbol="${escapeHtml(item.symbol)}" data-name="${escapeHtml(item.name || "")}">查今年預估 EPS</button>
            </div>
            <div class="list">
              <h3>建議買進理由</h3>
              <ul>${item.reasons.slice(0, 8).map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>
            </div>
            <div class="list warnings">
              <h3>風險提醒</h3>
              <ul>${(item.warnings.length ? item.warnings : ["目前沒有主要扣分項"]).slice(0, 5).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>
            </div>
            <div class="indicators">
              <h3>符合條件</h3>
              <div class="chips hidden">${signalChips(item.matched_criteria)}</div>
              <h3>指標摘要</h3>
              <div class="chips hidden">${metricChips(item.metrics)}</div>
            </div>
          </div>
        </div>
      </article>
    `
    )
    .join("") + renderErrors(data.errors);

  document.querySelectorAll("canvas.chart").forEach((canvas) => {
    const item = data.results.find((row) => row.symbol === canvas.dataset.symbol);
    drawChart(canvas, item.chart);
  });
  document.querySelectorAll(".clickable-result").forEach((node) => {
    node.addEventListener("click", () => {
      const item = data.results.find((row) => row.symbol === node.dataset.symbol);
      showKline(node.dataset.symbol, node.dataset.name, item?.chart || null, item?.buy_plan || null, item?.matched_criteria || []);
    });
  });
  document.querySelectorAll(".eps-button").forEach((node) => {
    node.addEventListener("click", async () => {
      const item = data.results.find((row) => row.symbol === node.dataset.symbol);
      await showKline(node.dataset.symbol, node.dataset.name, item?.chart || null, item?.buy_plan || null, item?.matched_criteria || []);
      lookupEps(node.dataset.symbol).catch((error) => {
        epsPanelEl.classList.remove("hidden");
        epsPanelEl.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
      });
    });
  });
};

const runScreen = async (event) => {
  event.preventDefault();
  const button = form.querySelector("button.primary");
  const marketMode = modeEl.value === "market";
  button.disabled = true;
  setStatus(marketMode ? "全市場掃描中" : "抓取資料中", "busy");
  progressPanelEl.classList.toggle("hidden", !marketMode);
  if (marketMode) {
    updateProgress({ status: "running", completed: 0, total: 0, scored: 0, errors: 0, percent: 0 });
  }
  resultsEl.className = "results empty";
  resultsEl.innerHTML = `<div class="empty-state"><h3>正在計算</h3><p>依前一個可取得收盤日 K 線更新指標，並套用勾選條件。</p></div>`;

  const payload = {
    symbols: document.querySelector("#symbols").value,
    range: document.querySelector("#range").value,
    min_matches: Number(document.querySelector("#minMatches").value),
    top: Number(document.querySelector("#top").value),
    refresh_prices: document.querySelector("#refreshPrices").checked,
    workers: Number(document.querySelector("#workers").value),
    criteria: getCriteria(),
  };

  try {
    const response = await fetch(marketMode ? "/api/screen-market-job" : "/api/screen", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "篩選失敗");
    const result = marketMode ? await waitForJob(data.job_id) : data;
    renderResults(result);
    setStatus(`完成，耗時 ${result.meta.elapsed_seconds ?? "-"} 秒`);
    updateUniverse();
    updateCloseStatus();
  } catch (error) {
    setStatus("發生錯誤", "error");
    resultsEl.innerHTML = `<div class="empty-state"><h3>篩選失敗</h3><p>${escapeHtml(error.message)}</p></div>`;
  } finally {
    button.disabled = false;
  }
};

refreshUniverseBtn.addEventListener("click", async () => {
  refreshUniverseBtn.disabled = true;
  setStatus("更新股票池中", "busy");
  try {
    const response = await fetch("/api/refresh-universe", { method: "POST", body: "{}" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "更新股票池失敗");
    universeCountEl.textContent = `${data.count} 檔`;
    setStatus("股票池已更新");
  } catch (error) {
    setStatus("股票池更新失敗", "error");
  } finally {
    refreshUniverseBtn.disabled = false;
  }
});

clearCriteriaBtn.addEventListener("click", () => {
  document.querySelectorAll('input[name="criteria"]').forEach((node) => {
    node.checked = false;
  });
});

toggleCloseListBtn.addEventListener("click", () => {
  closeListEl.classList.toggle("hidden");
  toggleCloseListBtn.textContent = closeListEl.classList.contains("hidden") ? "查看明細" : "收合明細";
});

refreshCloseStatusBtn.addEventListener("click", updateCloseStatus);
closeKlineBtn.addEventListener("click", () => klinePanelEl.classList.add("hidden"));
lookupEpsBtn.addEventListener("click", () => {
  lookupEps(currentKline.symbol).catch((error) => {
    epsPanelEl.classList.remove("hidden");
    epsPanelEl.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
  });
});
klineCanvasEl.addEventListener("mousemove", (event) => {
  if (!currentKline.points.length) return;
  const rect = klineCanvasEl.getBoundingClientRect();
  const scale = klineScale(klineCanvasEl, currentKline.points, currentKline.buyPlan);
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  if (x < scale.pad.left || x > rect.width - scale.pad.right || y < scale.pad.top || y > rect.height - scale.pad.bottom) {
    drawKline(klineCanvasEl, currentKline.points, currentKline.buyPlan);
    return;
  }
  drawKline(klineCanvasEl, currentKline.points, currentKline.buyPlan, scale.indexForX(x));
});
klineCanvasEl.addEventListener("mouseleave", () => {
  if (currentKline.points.length) drawKline(klineCanvasEl, currentKline.points, currentKline.buyPlan);
});

form.addEventListener("submit", runScreen);
form.querySelector("button.primary").addEventListener("click", runScreen);
modeEl.addEventListener("change", setModeVisibility);
window.addEventListener("resize", () => {
  if (!lastData) return;
  document.querySelectorAll("canvas.chart").forEach((canvas) => {
    const item = lastData.results.find((row) => row.symbol === canvas.dataset.symbol);
    if (item) drawChart(canvas, item.chart);
  });
  if (!klinePanelEl.classList.contains("hidden")) {
    const title = klineTitleEl.textContent.split(" ")[0];
    const item = lastData.results.find((row) => row.symbol === title);
    if (item) {
      drawKline(klineCanvasEl, item.chart, item.buy_plan);
    }
  }
});

setModeVisibility();
updateUniverse();
updateCloseStatus();
window.stockAppReady = true;
