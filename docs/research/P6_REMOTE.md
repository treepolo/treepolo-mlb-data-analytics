# P6 遠端執行說明（使用者電腦／Colab）

程式在雲端工作階段撰寫並用合成世界測試；真資料的重運算在使用者電腦跑（環境見 `P6_ENV.md`）。

## 一次性準備（使用者電腦，`F:\treepolo-p6`）
1. `git pull origin claude/hello-rc9nc6`
2. 重新安裝（不要用 `-e`）：`pip install F:\treepolo-p6 --upgrade`；GPU 需要 `pip install xgboost`。

## 跑設定檔
```
cd F:\treepolo-p6
.venv\Scripts\activate
python docs\research\scripts\run_configs.py F:\treepolo-p6-tmp\p6.log docs\research\configs\p6\L_smoke_RvR_gpu.json
```
- `device: "auto"`：有可用 GPU 就用，否則 CPU；明確寫 `"cuda"` 而 GPU 不能用時會直接報錯（不會悄悄換引擎）。
- `L_smoke_RvR_gpu.json`：煙霧測試（RvR、2023 訓練／2024 留出、60 棵樹），確認真資料、GPU 與總流程能跑通；預計數分鐘到十幾分鐘。
- `L_fast_RvR.json`：快速層（約 1 小時量級），給 6.1 開發用。標準層（約 3 小時）與完整層（10–16 小時）只在需要時另開設定。
- 結果在 `data\research_runs.sqlite3`；要帶回雲端分析，用 `treepolo-mlb export --out FILE --study P6`，把檔案放進 `docs/research/bundles/` 後提交推送。

## 注意
- 不要同時開多個重運算工作；記憶體 16 GB，先跑單一分組。
- 長工作不因超過估計時間而中止。
- 調參（tuning）只能用 2023–2024；2025/2026 為保留球季，只在 final_test 使用。
- `min_gain`（G1a 最小增益）真實資料用預設 0.0003。
