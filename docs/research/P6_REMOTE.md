# P6 在使用者電腦上怎麼跑（2026-10-15 改版：一鍵）

環境見 `P6_ENV.md`。**不再需要貼一串設定檔**：網頁介面有「一鍵研究」。

## 一次性準備（`F:\treepolo-p6`）
```
git pull origin claude/hello-rc9nc6
.venv\Scripts\activate
pip install F:\treepolo-p6 --upgrade
```

## 跑
1. 啟動網頁介面（和平常一樣：`treepolo-mlb` 的網頁指令）。
2. 左邊選單「研究 → 一鍵研究 Run Everything」。
3. 選運算量（快速約 1 小時；標準約 3 小時；冒煙只確認跑得通）與手別組，按「開始」。
4. 頁面會依序顯示五個步驟：模型參數搜尋 → 訓練全聯盟模型 → 固定效應對照答案 → 決策引擎與情境表 → 查詢工具與驗收。每個步驟顯示進度與耗時。
5. 全部完成後，頁面下方是結果摘要（引擎檢查、每種政策每打席少失幾分、球員描述有沒有用、各球種的差異），並有按鈕開啟「情境查詢」。
6. 中途失敗：頁面會寫是哪一步、為什麼；已完成的步驟會保留，修好後再按「開始」會從失敗的那一步繼續。關掉瀏覽器或重啟介面也一樣（相同設定的結果會沿用）。

## 注意
- 不要同時開多個重運算工作；記憶體 16 GB。長工作不因超過估計時間而中止。
- 調參與訓練只用 2023–2024；2025、2026 為保留球季，目前不使用。
- 模型設定搜尋是對「沒見過的球員」的預測好壞打分，選出一組設定給之後的步驟共用（所有分組同一組設定）。
- 沒有網頁介面時的備用做法：`docs/research/scripts/run_configs.py` 搭配 `docs/research/configs/p6/` 的設定檔（舊方式）。

## 情境查詢
網頁「研究 → 情境查詢」；或命令列：
```
treepolo-mlb research query <查詢紀錄編號> --group RvR --balls 1 --strikes 2 --outs 1 --bases 100 --prev SL --streak 2
treepolo-mlb research query <查詢紀錄編號> --group RvR --find pitcher:Skenes
```
