# Webb-site CCASS 財技資料抽取工具

這是一個本機可運行、也可部署到 Render Free Web Service 的 Streamlit Web App。工具會在用戶輸入港股股票代號或 Webb-site issue ID 後，即時讀取 Webb-site / Renavon 的公開 CCASS 頁面，抽取 HTML 表格，並輸出可直接貼給 ChatGPT 分析的 Markdown。

本工具只作公開資料整理及研究用途，不構成投資建議。請避免高頻抓取，避免對網站造成負擔。

## 功能

- 支援股票代號，例如 `06080`、`01417`、`01953`
- 支援 Webb-site issue ID，例如 `25298`、`25486`、`29176`
- 嘗試從 `orgdata.asp` 找出 issue ID，不會憑股票代號亂猜
- 抓取 Holdings、Changes、Big Changes、Concentration 四類頁面
- 先用 `requests` / `pandas.read_html`，失敗後自動 fallback 到 Playwright Chromium
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
