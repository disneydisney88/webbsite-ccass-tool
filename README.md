# Webb-site CCASS Extractor

香港股票 CCASS 持股、異動、集中度、股價、公司事件及管理層資料整理工具。專案提供 Streamlit UI、FastAPI、Custom GPT Action 及 MCP，輸出研究用 Markdown、CSV、Excel 與 JSON。

本工具只讀公開資料，不登入、不破解 CAPTCHA、不繞過 Cloudflare，亦不構成投資建議。每次只抓用戶指定股票；批量 snapshot 亦有節流及每日去重。

## Current Architecture

| Component | Purpose |
| --- | --- |
| Streamlit `app.py` | 人手查詢、全頁 table、Copy for ChatGPT、下載檔案 |
| FastAPI `api.py` | `/api/stock`、screening、snapshot、backup、Custom GPT Action |
| MCP `/mcp` | 與 REST 共用同一 source router 的研究工具 |
| Webb-compatible mirror | orgdata、Holdings、Changes、Big Changes、Concentration、Webb 價格 |
| HKEX SDW + SQLite | 官方 CCASS 每日 snapshot 與本地歷史差分 |
| Yahoo Finance | Webb price 不可用時的 OHLC/volume 後備來源 |

所有原有 mirror fetcher/parser 均保留。系統沒有任何 Cloudflare bypass。

## Date Semantics

這是分析前最重要的規則：

- Holdings、Big Changes、Concentration 的來源日期是 CCASS 持股日，即結算日。
- Changes participant rows 使用頁面明列的 `Trading date`；Changes 頁面的標題日期範圍及 `d=` query 是結算日期。
- `implied_trade_date` 以 XHKG 港股交易日曆倒推兩個交易 session，已計週末與港股假期。
- 每個 normalized row 均有 `ccass_date`、`implied_trade_date`、`implied_settlement_date`、`date_basis`。
- API metadata 有 `data_as_of_trading_date` 及 `date_basis_by_section`。

完整證據、08529 falsification tests 與跨來源警告見 [docs/DATE_SEMANTICS.md](docs/DATE_SEMANTICS.md)。Webb-site 與其他資料源可能使用不同日期基準，未統一 trade/settlement basis 前不可直接 join。

## Source Router

設定環境變數：

```text
CCASS_SOURCE_MODE=auto
CCASS_MIRROR_BASE_URL=https://webb-database.com
```

可用模式：

- `auto`：預設。嘗試可用 mirror 頁，失敗時保留 local snapshot／Yahoo 等可用 section，並 fail loud。
- `mirror`：強制走原有 mirror fetcher/parser。mirror 回 403 或 challenge 時只回明確錯誤，不作繞過。
- `sdw` 或 `local_db`：只讀已累積的 HKEX SDW SQLite snapshots，不代表即時 live SDW scrape。

0xmd 恢復後可只改：

```text
CCASS_MIRROR_BASE_URL=https://webbsite.0xmd.com
```

REST、MCP 與 Streamlit 現在共用 `utils/source_router.py`。`fetch_summary` 會保留 URL、final URL、HTTP status、content type、body head、error type、error message 與 fallback method。

## Inputs And Outputs

可輸入五位港股代號或 Webb-site issue ID。Stock code 不會被直接當成 issue ID；成功解析後會快取映射。

Streamlit 顯示：

1. Fetch Summary
2. Company / orgdata
3. Holdings
4. Changes
5. Big Changes
6. Concentration
7. Price History
8. Raw Table Previews
9. Copy for ChatGPT
10. Downloads

Combined CSV 是自我描述格式，每個 section 先有 fetch-status row，再有 data rows。檔案首四行是日期語意 comment。用 pandas 讀取時：

```python
import pandas as pd

df = pd.read_csv("03301_all_ccass_data.csv", comment="#")
```

CSV 包含 `fetched_time`、`row_meaning`、source URL、fetch status、日期基準與錯誤原因。Excel 以多個 sheets 分隔 metadata、fetch summary 及各資料 section。

## Local Run

Linux / macOS：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
streamlit run app.py
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
streamlit run app.py
```

API：

```bash
python api.py
```

開啟 `http://127.0.0.1:8000/health`、`/openapi.json` 或 `/api/stock?code=03301`。詳細合約見 [API_README.md](API_README.md)。

## Streamlit Cloud

Main file 是 `app.py`，Python dependencies 由 `requirements.txt` 安裝。Playwright 是 lazy/optional：只有 mirror browser fallback 真正需要時才啟動；browser 或 system library 不存在只會顯示 warning，不會令整個 app crash。

Streamlit Cloud 的 apt `packages.txt` 曾因 Playwright system dependencies 名稱不相容而令 requirements installation 全部失敗，因此目前不以大批 apt packages 作硬依賴。mirror 可用而 requests 已抽到 table 時也不需要 browser。

## Render API

Docker deployment 使用 `Dockerfile.api`／`render.yaml`。主要環境變數：

```text
API_TOKEN=<random secret>
CCASS_SOURCE_MODE=auto
CCASS_MIRROR_BASE_URL=https://webb-database.com
CCASS_SNAPSHOT_DB=data/ccass_snapshots.db
```

Render Free cold start 可令首次 request 較慢。`/health` 是 process-only health check，不會打 upstream；`/health?upstreams=true` 只供手動診斷。

## Snapshot Watchlist And Backup

`data/watchlist.csv` 欄位為 `code,name,group`，支援 `caiji`、`lshape` 或 `caiji;lshape`。

每日 UTC 13:30，即香港 21:30，`.github/workflows/daily_snapshot.yml` 會：

1. 喚醒 Render。
2. 分組觸發 `/api/snapshot_all`。
3. 將 SQLite 匯出到 `data/backups/ccass_snapshots_latest.db`。
4. 星期日保留日期版本，最多八個 weekly backups。

GitHub repository 要設定 secret `CCASS_API_TOKEN`，值與 Render `API_TOKEN` 相同。Token 不可寫入 code 或 workflow。

Render Free filesystem 是 ephemeral。啟動時若 working DB 不存在而 repo backup 存在，系統會自動還原；metadata 的 `db_restored_from_backup` 可供核實。當 DB 接近 50 MB，應轉 persistent disk、Turso 或 managed Postgres，避免把持續增長的 binary DB 長期放在 Git。

## Price History

Webb `hpu.asp` 可用時優先採用，因為它有實際 turnover。否則 Yahoo Finance 提供 OHLC 與 volume：

- `price_source=yahoo`
- `turnover_est=volume * close`
- `vwap_est=turnover_est / volume`

Yahoo 是非 HKEX 官方接口，可能改版；估算 turnover 並非真實成交額，相關 warning 必須保留。

## Known Limitations

- Webb mirror 的 Holdings／Changes 可獨立失敗，Big Changes／Concentration 仍可能成功。單一 section 失敗不會中止整份結果。
- `LOCAL_SNAPSHOT_EMPTY` 只代表本地 SQLite 沒有該股票 snapshot，不代表該股票沒有 CCASS 資料。
- 本地歷史深度由每日 snapshot 開始累積，不能自動重建 mirror 的完整歷史。
- Streamlit Cloud 未必能啟動 Chromium；requests/local fallback 仍應可用。
- Concentration 百分比超出 0 至 100 會標示 abnormal/stale-base warning，不可當正常值。
- Yahoo turnover/VWAP 是估算值。
- 不可把單一券商減倉直接解讀為派貨，必須對照成交量、同日轉倉與集中度。

## Error Codes

| Code | Meaning | Retry |
| --- | --- | --- |
| `COLD_START` | Hosting cold start 或總 timeout budget 用完 | 可稍後重試 |
| `SOURCE_TIMEOUT` | Upstream request timeout | 可重試一次 |
| `MIRROR_BLOCKED` / `SOURCE_CHALLENGE` | 403 或人類驗證 | 不應高頻重試 |
| `PARSE_ERROR` / `SOURCE_CHANGED` | HTML table 結構不匹配 | 先看 raw diagnostics |
| `LOCAL_SNAPSHOT_EMPTY` | 本地 DB 未有該股票 | 不等於 upstream 無資料 |
| `ISSUE_LOOKUP_FAILED` | 未能解析 Webb issue ID | 核實代號／來源 |
| `TOO_LARGE` | 日期範圍或輸出超出上限 | 縮短範圍 |
| `AUTH_FAILED` | API token 缺失或錯誤 | 不重試，修正 token |

## Tests

```bash
python -m unittest discover -v
```

CI workflow 會用 Python 3.11 安裝完整 requirements 後執行全套 tests；unit tests 不會 live 轟 HKEX、Yahoo 或 mirror。

## Changelog

### 1.14.0

- 明確分開 settlement/trade 日期語意，新增 row-level dual dates。
- 新增 `changes_from/to`、`big_changes_from/to`、`date_input_basis`。
- REST、MCP、Streamlit 統一 source router，快取按 source mode 與 mirror base 分隔。
- fetch diagnostics 新增 content type 及 2,000 字 body head。
- CSV／Markdown 加日期語意 header，部分成功 summary 不再以空白冒充完整資料。

