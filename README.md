# Webb-site CCASS 財技資料抽取工具

這是一個本機可運行、也可部署到 Render Free Web Service 的 Streamlit Web App。工具會在用戶輸入港股股票代號或 Webb-site issue ID 後，即時讀取 Webb-site / Renavon 的公開 CCASS 頁面，抽取 HTML 表格，並輸出可直接貼給 ChatGPT 分析的 Markdown。

本工具只作公開資料整理及研究用途，不構成投資建議。請避免高頻抓取，避免對網站造成負擔。

## 功能

- 支援股票代號，例如 `06080`、`01417`、`01953`
- 支援 Webb-site issue ID，例如 `25298`、`25486`、`29176`
- 嘗試從 `orgdata.asp` 找出 issue ID，不會憑股票代號亂猜
- 抓取 Holdings、Changes、Big Changes、Concentration 四類頁面
- 先用 `requests` / `pandas.read_html`，失敗後自動 fallback 到 Playwright Chromium
- 2026-07 emergency source router: `CCASS_SOURCE_MODE=auto|mirror|sdw`
- 顯示抓取狀態、原始文字預覽、表格
- 產生 ChatGPT Markdown 報告
- 支援下載 Markdown、CSV、Excel、JSON

## 專案結構

```text
webbsite_ccass_tool/
app.py
requirements.txt
Dockerfile
README.md
utils/
  __init__.py
  fetcher.py
  parser.py
  report.py
  exporters.py
samples/
  sample_output_06080.md
```

## 本機運行

Linux / macOS:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
streamlit run app.py
```

### Streamlit Cloud capability boundary

The repository intentionally has no `packages.txt`. On Streamlit Cloud the
Playwright Chromium binary may be present, but it cannot start because required
system libraries such as `libglib-2.0.so.0` are unavailable (`exitCode=127`).
This is a fixed deployment limitation, not an intermittent browser failure.

Consequently, the requests-only Webb pages remain available on Streamlit Cloud:

- Company / orgdata
- Big Changes
- Concentration
- Price History

Holdings and daily Changes require a working browser and are not available from
the Streamlit Cloud deployment. Their rows must remain visibly skipped/partial;
they must not be presented as a complete result. For broker-level Holdings or
daily Changes, run Streamlit locally in `mirror` mode or use the Render API,
where the Docker image installs the Chromium runtime libraries.

Do not reintroduce `packages.txt` without a separate deployment experiment and
explicit approval. Two earlier attempts were removed:

- `15a2441` added the larger Playwright apt dependency path and caused the
  Streamlit dependency build to fail when package names were incompatible.
- `c9d0c74` reduced the file to only `libglib2.0-0`; it was subsequently removed
  by `84a6d10` because the Streamlit package-build dependency remained unsafe.

`Dockerfile.api` is based on `python:3.11-slim` (Debian 12 at the time of this
decision), while Streamlit Community Cloud uses Debian 11. Apt package names and
availability differ between those environments (including audio-library package
variants such as `libasound2` / `libasound2t64`), so the Docker apt list must not
be copied wholesale into Streamlit Cloud. One invalid package can fail the whole
app build and remove access to the otherwise working requests-only sections.

## CCASS Source Mode

Default:

```text
CCASS_SOURCE_MODE=auto
```

- `auto`: use the hybrid local DB + direct Webb-site requests path. The route performs one persisted mirror probe per day. The probe records whether Holdings or Changes require a browser; those sections then go directly through normal Playwright rendering for the rest of that day. If a probe cannot complete, auto mode uses requests first and only invokes the browser for an actual `JS_CHALLENGE`. Timeout, HTTP error, and empty-page failures do not trigger browser fallback. Set `CCASS_BROWSER_FALLBACK=off` to disable it explicitly.
- `mirror`: force the original Webb-site mirror fetcher/parser.
- `sdw`: use HKEX SDW plus local snapshots only.

No Cloudflare bypass, CAPTCHA solver, stealth browser, or paid-wall circumvention is implemented.

### Webb-site Mirror Base URL

The compatible `webb-database.com` mirror is the default while 0xmd is
temporarily unavailable:

```text
CCASS_MIRROR_BASE_URL=https://webb-database.com
```

To switch back to the original 0xmd mirror after it becomes available again,
without deleting the SDW snapshot/backup path, set:

```text
CCASS_MIRROR_BASE_URL=https://webbsite.0xmd.com
```

You can set it as an environment variable or in `ccass_source_config.json`:

```json
{
  "CCASS_SOURCE_MODE": "mirror",
  "CCASS_MIRROR_BASE_URL": "https://webb-database.com"
}
```

`hpu.asp` may work through plain requests on that mirror, while some CCASS pages first return a small JavaScript cookie reload page. The app does not manually bypass this; it only uses normal Playwright page execution for the two browser-required CCASS sections. If Playwright is unavailable, the app preserves the original challenge result and falls back to SDW/local DB instead of crashing. The daily probe is persisted in the local snapshot DB and records which sections need browser rendering.

The Streamlit Cloud browser boundary above applies even when lazy installation
successfully downloads Chromium: the binary still exits with code 127 when its
system shared libraries are absent. The app can continue with requests/local
data, but that is a partial result rather than successful Holdings/Changes
degradation.

Set `CCASS_DEBUG_DUMP=true` only for a controlled diagnosis. It writes raw
Holdings/Changes HTML and metadata under `debug/`; the default is off and the
directory is ignored by Git.

The default Streamlit path keeps direct requests-only pages available for Big
Changes, Concentration and Price History. Local snapshots may supplement a stock
when they exist. On Streamlit Cloud, live Holdings/Changes cannot launch the
browser described by the auto route and therefore remain skipped/partial; local
Streamlit and the Render API can use that browser path.

Full mirror Holdings/Changes is optional. To enable the slower Render browser
path, add these App Secrets (not GitHub Actions secrets):
`CCASS_API_TOKEN` with the same value as Render's `API_TOKEN`, plus
`CCASS_RENDER_FULL=true`. Optional:
`CCASS_RENDER_API_URL=https://webbsite-ccass-api.onrender.com`.

Price History is routed independently: `dbpub/hpu.asp` on the configured
Webb-site mirror is preferred even when Holdings/Changes have fallen back to
SDW. Yahoo Finance is used only when that price page cannot be fetched.

## Snapshot DB

SDW snapshots are stored in `data/ccass_snapshots.db`. Render Free and Streamlit Cloud filesystems may be ephemeral, so download backups regularly from the Streamlit `Download Snapshot DB Backup` button or the API endpoint:

```text
GET /api/snapshots/export?key=<token>
```

Daily watchlist snapshots can be triggered by an external uptime monitor:

```text
GET /api/snapshot_all?key=<token>
GET /api/snapshot_all?group=caiji&key=<token>
GET /api/snapshot_all?group=lshape&key=<token>
```

Edit `data/watchlist.csv` to change the monitored stock codes. The file has `code,name,group`; group is `caiji`, `lshape`, or a semicolon-separated value such as `caiji;lshape`.

## Daily Backup Loop

GitHub Actions runs `Daily CCASS Snapshot Backup` every day at 13:30 UTC / 21:30 Hong Kong time, after HKEX SDW data is normally available. It:

1. Wakes the Render API with `/health`.
2. Calls `/api/snapshot_all?group=caiji` and `/api/snapshot_all?group=lshape`.
3. Fetches Yahoo Finance daily close/volume for each watchlist stock and stores it in SQLite `price_history`.
4. Downloads `/api/snapshots/export`.
5. Commits `data/backups/ccass_snapshots_latest.db`; on Sundays it also keeps a dated weekly DB and retains the latest 8 weekly files.

The backup commit message includes `[skip render]`, which Render supports for skipping auto-deploys, so the nightly DB backup does not create a deploy loop.

One manual setup step is required:

```text
GitHub repo -> Settings -> Secrets and variables -> Actions -> New repository secret
Name: CCASS_API_TOKEN
Value: same value as the Render API_TOKEN
```

Do not commit API tokens to the repo.

## Restore From Backup

On API startup, if `data/ccass_snapshots.db` is missing but `data/backups/ccass_snapshots_latest.db` exists, the app restores the working DB from the latest backup and logs:

```text
Restored snapshot DB from backup (...)
```

API metadata includes `db_restored_from_backup` so you can verify restore behavior through `/api/stock`.

## DB Size Planning

The SQLite DB grows with `stock count x trading days x participant rows`. With 52 stocks, expect roughly a few MB per month depending on participant count. When committed backups approach 50MB, move to external storage or a managed database instead of keeping binary DB history in Git.

## Price History Fallback

Mirror price history (`hpu.asp`) is still kept. When the mirror is available, it remains the preferred source because it includes actual turnover. When the mirror is blocked or the price table fails, the app falls back to Yahoo Finance's chart endpoint over HTTP.

Yahoo Finance is not an official HKEX source and may change behavior. Yahoo fallback rows keep the existing price-history columns and add:

- `price_source`: `yahoo`
- `turnover_est`: estimated turnover calculated as `volume x close`

Reports and API warnings always state: `Turnover is estimated as volume × close, not actual turnover`.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
streamlit run app.py
```

打開 Streamlit 顯示的本機網址，輸入股票代號或 issue ID 後按 `Fetch Webb-site Data`。

## Render 部署

建議用 Docker 部署。

1. 將 `webbsite_ccass_tool` 推到 GitHub repository。
2. 在 Render 建立 `New Web Service`。
3. 選擇該 GitHub repository。
4. Environment 選 `Docker`。
5. Render 會自動使用 `Dockerfile`。
6. 不需要額外 Build command。
7. 不需要額外 Start command，Dockerfile 已設定：

```bash
streamlit run app.py --server.port=${PORT:-8501} --server.address=0.0.0.0
```

可選 Environment variables:

```text
PLAYWRIGHT_HEADLESS=true
REQUEST_TIMEOUT_SECONDS=60
FETCH_DELAY_SECONDS=0.5
```

Render Free Web Service 可能會冷啟動，第一次抓取會較慢。

## 06080 / issue ID 25298 測試說明

1. 本機啟動工具。
2. 在輸入框填入 `06080`，模式選 `Auto detect`。
3. 如果工具從 orgdata 頁成功找到 issue ID，會使用該 ID 抓取。
4. 如未能自動識別，在手動 issue ID 欄填入 `25298`。
5. 按 `Fetch Webb-site Data`。
6. 確認 Holdings 分頁至少顯示表格。
7. 複製 Markdown report 到 ChatGPT 分析。

也可直接輸入 `25298` 並選擇 `Issue ID` 測試。

## 注意

- 不登入、不繞過付費牆、不破解 CAPTCHA。
- 如果某頁失敗，工具會在 Markdown 報告列出 failed URL、error type、error message，並繼續輸出其他成功頁面。
- 如果 Concentration 頁失敗，工具會用 Holdings 頁的 cumulative stake 即時計算 Top 5 / Top 10 作備案。
