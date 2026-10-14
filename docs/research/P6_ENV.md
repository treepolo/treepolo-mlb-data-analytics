# P6 運算環境紀錄（2026-10-14）

兩個工作階段不共享記憶，只靠 repo 與這份紀錄。**使用者決定（2026-10-14）：程式在雲端工作階段撰寫與測試，重運算在使用者電腦跑。**

## 使用者電腦（本機工作階段）
- Windows 11；Intel Core i5-9400F（6 核心 6 執行緒，約 3.9 GHz）；16 GB 記憶體；GeForce GTX 1650（4 GB 顯存，驅動 591.44、CUDA 13.1；桌面程式平時占用約 3 GB，跑長作業前請關閉 Chrome、Steam 等）。
- 磁碟：C: NVMe SSD（**只剩約 7.6 GB，不要寫入**）、D: SATA HDD（604 GB 可用，舊專案與原始資料，**不要修改**）、F: USB 外接 SSD（Micron X9 Pro，exFAT，約 931 GB 可用，**工作位置**；作業期間不要拔除）。
- 工作位置：`F:\treepolo-p6`（`git clone --branch claude/hello-rc9nc6 --single-branch`，獨立複製，不碰 D: 的 `.git`）；暫存 `F:\treepolo-p6-tmp`（`TEMP`、`TMP`、pip 快取都指到這裡，只在該 shell 內設定）。
- 已加 `git config --global --add safe.directory F:/treepolo-p6`（exFAT 不記錄擁有者，寫入 C: 的 `.gitconfig`，約 1 KB）。
- Python：`F:\treepolo-p6\.venv`，Python 3.13.7；xgboost 3.4.1、numpy 2.5.4、duckdb 1.5.6、scipy、scikit-learn、requests、reportlab；專案以 `pip install F:\treepolo-p6`（非 `-e`）安裝。系統預設的 Python 3.14.0 **不用**（太新）。
- `config.json` 在 `F:\treepolo-p6`，`data_dir` 為 `F:/treepolo-p6/data`；**必須是無 BOM 的 UTF-8**（專案的 JSON 讀取器不接受 BOM，用 PowerShell 寫檔要小心）。
- 執行指令：在 `F:\treepolo-p6` 啟用 `.venv`，CLI 為 `treepolo-mlb ...`；研究設定檔用 `docs/research/scripts/run_configs.py LOGFILE CONFIG.json`（設定檔須含 `kind`、`study` 鍵）。

## 資料
- 本機資料原本由使用者下載到 `D:\Vibe Coding\treepoloMLB數據分析器\data\`（2015-03-03 起）。F: 上是**複製後的副本**（`statcast.sqlite3` 7.7 GB 含 WAL、`statcast.duckdb` 2.0 GB），並以 `backfill --start 2026-08-24 --end 2026-10-05 --resume` 補到 2026-10-05（新增 142,465 列），`verify` 正常、`analytics-sync` 完成。`raw\` 沒有複製（所以 `rebuild` 在 F: 不能用）。
- **與雲端資料的核對（全部通過）**：2023、2024、2025、2026 各年例行賽列數 720,684、711,899、712,528、716,792（SQLite 與 DuckDB 一致）；2023-03-01 起所有類型合計 3,358,444，與雲端 3,358,443 **只差 2026 春訓（`S`）1 列**（本機 133,886、雲端 133,885），不影響任何例行賽分析。本機另有 2015–2022 與 2023-02-24 至 02-28 春訓 13,847 列（雲端沒有），P6 的範圍設定不會讀到它們（範圍固定 2023–2026 例行賽）。
- **`run_key` 不會與雲端相同**：資料指紋包含各球季的最後寫入時間（`_ingested_at`），不同次下載必然不同。**判斷資料是否一致，用結果內容比對**：P5 的 A1 得分期望值表在本機重現，288 個狀態、RE(0-0、壘空、無人出局)＝0.4988448512、內容雜湊 `5687c348b33e431b1c3b79c0193e18eb348daf07ae672c92e803c042dc1ea7f3` 與雲端完全一致（腳本 `re_hash.py`，見 P6 工作階段紀錄）。
- 本機的研究資料庫（`F:\treepolo-p6\data\research_runs.sqlite3`）從零開始；要在本機使用 P5 結果時，匯入 `docs/research/bundles/` 的研究檔。

## 實測：GPU 與 CPU 速度（XGBoost 3.4.1，8 類、70 特徵、100 棵樹、深度 6，`tree_method=hist`）
| 列數 | 使用者電腦 GPU（1650） | 使用者電腦 CPU（6 執行緒） | scikit-learn HGB | Colab T4（15 GB 顯存） |
|---|---|---|---|---|
| 20 萬 | 9.4 秒 | 17.2 秒 | 16.2 秒 | 3.8 秒 |
| 60 萬 | 17.0 秒（顯存峰值 3,636 MiB／4,096） | 43.3 秒 | — | 7.6 秒 |
| 110 萬 | 未測 | 未測 | — | 12.3 秒 |
- 結論：**1650 約比自己的 CPU 快 2–3 倍；Colab T4 約比 1650 快 2.2–2.5 倍**。Colab 帳號（Google AI Pro）只能選 T4 與 TPU，A100、H100、L4、G4 為灰色（需要 Ultra）。**重運算以使用者電腦為主，Colab 為備案**（不值得為 2 倍速度搬資料與處理斷線）。
