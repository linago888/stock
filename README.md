# 台灣股票起漲篩選系統

這是一個以日 K 線為主、財務指標為輔的台股候選股篩選工具。它會用 KD、MACD、RSI、均線、突破、量能與波動率建立分數，並把財務成長與品質納入佐證，最後輸出建議買進理由與風險提醒。

> 這不是投資建議或保證獲利模型。請把它當成候選股雷達，實際下單仍需搭配產業消息、籌碼、流動性與個人風險控管。

## 快速使用

啟動網頁介面：

```powershell
& 'C:\Users\ASUS\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' server.py
```

然後開啟：

```text
http://127.0.0.1:8000/
```

## 每日全市場篩選流程

新版邏輯會先建立上市/上櫃股票池，再依每檔股票前一個可取得的日 K 收盤資料計算技術指標，最後輸出準備起漲的候選清單。

股票池來源：

- 上市公司基本資料：`https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv`
- 上櫃公司基本資料：`https://mopsfin.twse.com.tw/opendata/t187ap03_O.csv`

價格資料來源：

- Yahoo Finance chart endpoint，個股代號使用 `.TW` 或 `.TWO`
- 快取位置：`data/market_prices`

命令列執行全市場篩選：

```powershell
& 'C:\Users\ASUS\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' market_screener.py --refresh-universe --range 1y --min-score 55 --top 20 --workers 6
```

測試前 80 檔，避免第一次全市場抓取太久：

```powershell
& 'C:\Users\ASUS\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' market_screener.py --max-symbols 80 --range 6mo --min-score 50 --top 20 --workers 6
```

使用本機 CSV：

```powershell
& 'C:\Users\ASUS\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' stock_picker.py --prices-dir data\prices --financials data\financials.csv --top 10 --min-score 50
```

有網路時從 Yahoo Finance 抓日 K：

```powershell
& 'C:\Users\ASUS\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' stock_picker.py --symbols 2330,2317,2454,2308 --fetch --financials data\financials.csv --top 10 --min-score 55
```

只下載個股每日收盤價，不執行篩選：

```powershell
& 'C:\Users\ASUS\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' stock_picker.py --symbols 2330,2317,2454 --download-dir data\closes --close-only --range 1y
```

下載完整日 K 資料，之後再用本機資料篩選：

```powershell
& 'C:\Users\ASUS\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' stock_picker.py --watchlist data\watchlist.txt --download-dir data\prices --range 1y --sleep 0.5
```

或用清單：

```powershell
& 'C:\Users\ASUS\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' stock_picker.py --watchlist data\watchlist.txt --fetch --financials data\financials.csv
```

## 價格 CSV 格式

每檔股票一個檔案，放在 `data/prices`，檔名可用 `2330.csv` 或 `2330.TW.csv`。欄位支援英文或部分台股中文欄位：

```csv
date,open,high,low,close,volume
2026-01-02,100,103,99,102,12345678
```

## 財務 CSV 格式

`data/financials.csv` 支援以下欄位：

```csv
symbol,name,revenue_yoy,eps_ttm,roe,gross_margin,operating_margin,debt_to_equity,pe,pb
2330,台積電,18.5,45.2,26.1,55.0,42.0,35.0,22.5,5.8
```

財務評分邏輯：

- 營收年增率：確認成長動能。
- EPS TTM：避免只有技術面、獲利卻轉弱的標的。
- ROE、毛利率、營益率：確認獲利品質。
- 負債權益比：避免高槓桿風險。
- 本益比：避免估值過熱但仍允許高成長股自行判斷。

## 起漲判斷邏輯

技術面重點：

- 收盤站上 20 日與 60 日均線。
- 20 日均線斜率轉正。
- 收盤突破或貼近前 20 日高點。
- 成交量高於 20 日均量。
- KD 黃金交叉且尚未過熱。
- MACD 柱狀體翻正或連續擴大。
- RSI 位於 50 到 70 的健康強勢區。
- ATR 與均線乖離用來扣分，避免追高與波動過大。

輸出分數越高代表「技術面起漲跡象」與「財務佐證」越一致。一般可先看 `--min-score 55` 以上，市場很弱時可降到 45 到 50 做觀察名單。
