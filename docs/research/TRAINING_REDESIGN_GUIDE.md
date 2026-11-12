# 模型訓練改版：給本機工作階段的施工指導（2026-11-12）

這份文件是給在使用者電腦（`F:\treepolo-p6`）上工作的 Claude 看的。照順序做，每個工作包做完就提交、推送、停下來向使用者回報，等使用者說繼續再做下一包。

使用者已經同意的方向（不要再問一次）：
1. 已知會讓模型學歪的因素，**在訓練時都要可以設定消除**，作用在被訓練、被拿來算期望失分的那個模型本身，不能只放在對照工具裡。
2. 訓練設定：網頁可從「簡單模式」直接切換成「完整設定模式」，**所有參數都能設定**；整組設定可匯出、匯入成檔案。
3. 提供多種模型（邏輯迴歸、梯度提升樹、混合效應模型、神經網路＋球員嵌入、序列模型），並有**比較模式**：勾選多種方法用同一份資料一起訓練、並排顯示結果；開始前**預估時間**。
4. **拿掉所有「通過／不通過」「偵測到／無可偵測差異」的判定和事先寫死的門檻**。改為顯示效果大小、區間、樣本數，並提供：全部比較都列出、標示比較了幾次與純靠運氣預期有幾個「看起來有效」、換年份重看結果是否還在。由使用者自己判斷。

---

## 0. 工作規則（每個工作包都適用）

- 程式碼與資料在 `F:\treepolo-p6`。Python 用 `D:\soft\Python313`（工作包 0 建立；套件直接裝在裡面，`pip install` 一律加 `--no-cache-dir`）。`TEMP`、`TMP` 指向 `F:\treepolo-p6-tmp`。不寫 C:；D: 只動 `D:\soft\Python313`；`D:\Vibe Coding\` 的舊專案不要碰。
- 分支 `claude/hello-rc9nc6`。開工前 `git pull origin claude/hello-rc9nc6`。推送失敗隔 2、4、8、16 秒重試。不要開 PR。
- 不用啟用環境，直接用完整路徑：`D:\soft\Python313\python.exe -m pip install --no-cache-dir F:\treepolo-p6 --upgrade`，**不要用 `-e`**。安裝前先關掉網頁服務（否則檔案被鎖、會留下 `~reepolo...` 殘留目錄）。
- 跑測試要加 `PYTHONPATH=src`（PowerShell：`$env:PYTHONPATH='src'`），例如 `D:\soft\Python313\python.exe -m pytest -q -p no:cacheprovider tests\test_xxx.py`。完整測試約 20–25 分鐘：`D:\soft\Python313\python.exe -m pytest -q -p no:cacheprovider -m "not integration"`。
- 不要用 `pkill -f` 或任何會比對到自己指令列的方式結束程序；要停自己開的程序，用它的 PID。
- 不要在真實資料上跑研究，除非工作包明確要求（只有「冒煙」等級的確認可以，跑之前先告訴使用者）。工作包 C、E、F 完成時，各在真實資料上跑一次冒煙（RvR、少量資料），確認不會在真實資料上出錯，結果附在回報裡。
- 介面程式不用 `innerHTML`、`localStorage`、`sessionStorage`（`tests/test_research_ui_wiring.py` 會檢查）。新的前端 JS 要加進 `src/treepolo_mlb_data/web_static/index.html` 與 `.github/workflows/ci.yml` 的 `node --check` 清單。
- 改了某個研究方法的輸出或意義，就把該方法的 `version` 加 1（這會讓舊紀錄不再被誤用）。舊的研究方法（連投、前一球那些，`sequencing_methods.py`、`regression_methods.py`、`modeling_methods.py`）**不要改**，它們的結果要能重現。
- 用詞：介面與文件用正體中文，給使用者看的說明要白話，不用「P5／P6」這類階段代號。
- 每個工作包結束：完整測試、`node --check` 所有改到的 JS、提交、推送，然後回報：做了什麼、測試結果（照實寫，失敗就貼失敗）、還沒做的、需要使用者決定的事。

---

## 1. 現在的程式長什麼樣（先讀懂再動手）

| 檔案 | 做什麼 |
|---|---|
| `research/profiles.py` | 球員描述特徵（投手武器庫與比率、打者比率），向聯盟平均收縮；`history=True` 時只用更早的比賽 |
| `research/league_core.py` | 特徵矩陣 `situation_features`、`level_matrix`（L0 情境、L1 加投手描述、L2 加打者描述、L3 兩者）、`fit_predict_level`、`evaluate_group`（依球員分折、留出年份的評估） |
| `research/gbm.py` | 梯度提升樹（xgboost／scikit-learn），`fit_gbm`、`FittedGbm`、袋裝 `bag_fit` |
| `research/league_methods.py` | 研究方法 `league_model`（訓練全聯盟模型、評估、存模型包）、`player_profiles`；`fetch_columns` 從資料庫取欄位；`_gates` 產生通過判定 |
| `research/tune_methods.py` | 研究方法 `param_search`（隨機搜尋參數） |
| `research/anchor.py`、`anchor_methods.py` | 固定效應線性機率模型（投手＋打者 S3、投手×比賽＋打者 S4）、`anchor_consistency`（有 `passes` 判定）、界內球價值 `jbip_table` |
| `research/policy_core.py` | 動態規劃引擎（狀態＝球數、出局、壘包、前一球、連投顆數），政策、邊際價值、`detect()` 判定 |
| `research/policy_build.py` | **`fit_outcome_members` 只訓練 L0 梯度提升樹**（不含任何球員資訊），`outcome_tensor` 把模型對每個情境的預測變成引擎要的陣列 |
| `research/policy_methods.py` | 研究方法 `pitch_policy`：自己訓練 L0 模型 → 引擎 → 情境表、邊際價值、方向圖（有 `not_detected`）、對照一致性（有 `passes`） |
| `research/query_core.py`、`query_methods.py` | 情境查詢（聯盟平均查表；指定球員時用 `league_model` 的 L3 模型包即時算）；`gate_status` 會依閘門結果自動關閉個別化；每列有 `detected`、`evidence` |
| `research/pipeline.py` | 一鍵流程五步：`param_search` → `league_model` → `action_effects` → `pitch_policy` → `pitch_query`；`TIERS` 是快速／標準／冒煙三組固定設定；`summarize` 有 `r1.passed` |
| `web_static/research-pipeline-page.js` | 一鍵研究頁（只有運算量、手別組兩個選項） |
| `web_static/research-query-page.js` | 情境查詢頁 |
| `web_static/research-charts.js` | 圖表模組（`direction` 預設用 `not_detected` 淡化格子） |

**目前最大的設計問題**：被拿來算期望失分的模型（`policy_build.fit_outcome_members`）是 L0，完全不含投手、打者資訊。所以「敢連投的人本來那顆球就強」這類混淆，直接進到情境表裡。投手、打者固定效應只在 `anchor.py` 裡用來比對，不會修正模型。

---

## 2. 工作包總覽

| 包 | 內容 | 需要使用者先同意的事 |
|---|---|---|
| 0 | 改用共用的 Python（`D:\soft` 底下），搬離 F: | 確認原本的 Python 3.13 位置後再進行（見 2.5 第 1 步）；刪舊環境前要問 |
| A | 拿掉所有判定與寫死的門檻，改顯示數字與三種資訊 | 無 |
| B | 統一的「模型」介面；邏輯迴歸與梯度提升樹搬進來；偏差控制開關；參數搜尋改成適用所有模型；拿掉年份限制 | 無 |
| C | 決策引擎與情境查詢改用 B 訓練出來的模型（不再自己訓練 L0） | 無 |
| D | 完整設定模式、設定檔匯入匯出 | 無 |
| E | 比較模式、時間預估 | 無 |
| F | 混合效應、神經網路＋嵌入、序列模型（PyTorch） | **要先問使用者是否同意安裝 PyTorch（CUDA 版約 2–3 GB，裝進 `D:\soft\Python313`，加 `--no-cache-dir`）** |
| G | 文件與收尾 | 無 |

順序照表。0 是環境；A 最小，先做；B、C 是核心；D、E 是介面；F 需要裝新套件。

---

## 2.5 工作包 0：改用共用的 Python（`D:\soft\Python313`）

使用者的決定：
- Python 環境從 F:（外接硬碟）搬走。
- `D:\soft` 的規則是「一個軟體一個資料夾，以軟體命名，裡面的東西只給那個軟體用」。Python 是軟體，套件是 Python 的外掛，所以套件裝在 Python 自己的資料夾裡。
- 採「共用 Python，衝突時才另開」：這個專案和使用者其他專案平常都用同一份 Python；只有某個專案真的需要不同版本的套件時，才替它另開獨立環境（現在不需要）。

步驟：
1. 找出 `F:\treepolo-p6\.venv` 當初用的那份 Python 3.13.7 裝在哪：看 `F:\treepolo-p6\.venv\pyvenv.cfg` 的 `home =` 那行（電腦上同時有 3.14.0，是系統預設，太新，不用）。
   - **如果它已經在 `D:\soft` 底下**：直接用它，不要再裝一份，以下把 `D:\soft\Python313` 換成它的實際位置。
   - **如果它在 C:**（Windows 預設會裝在 `C:\Users\<使用者>\AppData\Local\Programs\Python\Python313`）：不能拿它當共用的 Python，因為之後所有套件（包括約 4–5 GB 的 PyTorch）都會裝進它的資料夾，也就是 C:，而 C: 只剩幾 GB。這種情況才在 `D:\soft\Python313` 裝一份新的，C: 那份不要動、不要刪。
   - 不論哪種情況，先把找到的位置告訴使用者，使用者同意後再進行下一步。
2. （只有需要新裝時）下載 Python 3.13 最新修正版的官方 Windows 安裝程式（64 位元），下載到 `F:\treepolo-p6-tmp`，用安靜安裝：
   `python-3.13.x-amd64.exe /quiet InstallAllUsers=0 TargetDir=D:\soft\Python313 PrependPath=0 Include_launcher=0 Include_test=0 Shortcuts=0 AssociateFiles=0`
   不加進 PATH（避免和系統預設的 3.14 混在一起），一律用完整路徑 `D:\soft\Python313\python.exe`。安裝後刪掉下載的安裝程式。
3. 套件直接裝進這份 Python（不建虛擬環境），加 `--no-cache-dir`，不留下載快取：
   `D:\soft\Python313\python.exe -m pip install --no-cache-dir numpy scipy scikit-learn duckdb requests reportlab pytest xgboost==3.4.1`
   版本以 `F:\treepolo-p6\.venv` 現有的為準：先在舊環境執行 `pip freeze` 存到 `F:\treepolo-p6-tmp\old-freeze.txt`，新環境安裝時對照，主要套件（numpy、scipy、scikit-learn、duckdb、xgboost）版本要一樣。
4. 安裝本專案：`D:\soft\Python313\python.exe -m pip install --no-cache-dir F:\treepolo-p6 --upgrade`（不要用 `-e`）。
5. 確認：`D:\soft\Python313\python.exe -c "import xgboost, numpy; from treepolo_mlb_data.research import gbm; print(xgboost.__version__, gbm.cuda_works())"` 要印出 `3.4.1 True`。
6. 網頁介面改用 `D:\soft\Python313\Scripts\treepolo-mlb.exe` 啟動，確認能開、資料庫路徑仍是 `F:\treepolo-p6\data\statcast.sqlite3`，「研究 → 一鍵研究」能載入。把完整的啟動指令告訴使用者。
7. 跑一次完整測試（`PYTHONPATH=src`，用新的 Python）。
8. 都正常後，**問使用者**是否刪除 `F:\treepolo-p6\.venv`（約 1 GB，裡面還有兩個 `~reepolo` 開頭的安裝殘留）。同意才刪。
9. 更新 `docs/research/P6_ENV.md` 的 Python 段落與 `docs/research/P6_REMOTE.md` 的指令，之後本文件所有指令都用 `D:\soft\Python313\python.exe`。

## 3. 工作包 A：拿掉判定與門檻

### A.1 要拿掉的東西（逐一處理，找完後用 `grep` 確認沒有漏）
- `league_methods.py`：`_gates()` 的 `passed` 欄位與 `min_gain` 設定。區段改名為「球員描述的預測增益」，只列 `side`、`comparison`、`mode`、`delta_logloss`（或 `gain`）、`lo`、`hi`、樣本數。
- `anchor.py`：`anchor_consistency()` 的 `passes`，以及 `z_detect`、`min_effect` 的二分法。改成回傳每一格「模型的效果、對照的效果、對照的區間、兩者差多少、同號與否」的完整列表，加上彙總數字（同號比例、比值的中位數），**不給通過判定**。
- `policy_core.py`：`detect()` 刪除。`tau` 設定（`pitch_policy` 的欄位）刪除。
- `policy_methods.py`：`_marginals` 的 `detected_share`、`_direction` 的 `not_detected`、`Anchor` 區段的 `passes` 刪除。
- `query_core.py`：`SituationTable.rows()` 的 `detected`、`evidence` 刪除；`indistinct_groups` 保留但改名為「區間重疊群組」，說明文字寫清楚「區間重疊，代表資料分不出這幾種球的高低」，這是描述，不是判定。
- `query_methods.py`：`gate_status()` 刪除；`answer()` 不再依閘門結果自動關閉個別化。改成在回應裡附上「球員描述的預測增益」數字（從模型包那筆紀錄讀出），由使用者自己看。
- `pipeline.py`：`summarize()` 的 `r1.passed`、`_le()` 刪除；只列數字（各狀態與得分期望值表的中位數差、最大差、打席起點差）。
- 前端：`research-pipeline-page.js` 的「通過／沒通過」「過／沒過」文字；`research-query-page.js` 的「偵測到」「證據」欄位與「閘門」文字；`research-charts.js` 的 `direction` 預設改用新的欄位（見 A.2），不再依判定淡化，改依「區間是否含 0」只做顏色深淺的描述（圖例寫清楚）。

### A.2 要加上的三種資訊
1. **全部列出**：方向圖、邊際價值、對照比較都輸出所有格子，不篩選。
2. **比較次數與純運氣預期**：凡是一次做很多比較的區段（方向圖、邊際價值摘要、對照比較、查詢回應），在區段的 `extras` 和頁面上顯示：「這裡共比較了 N 個（情境×球種），在 99% 區間下，就算全部其實沒差，純靠運氣也預期約 N×0.01 個區間不含 0；實際有 M 個。」N、M 由程式算，不要寫死。
3. **換年份重看**：`pitch_policy`（與工作包 B 之後的訓練）在「訓練年份 ≥ 2」時，另外用每個年份單獨訓練一份（成員數可較少，設定欄位 `replication_members`，預設 3），對每個格子輸出「各年份單獨估計的值」，頁面並排顯示。不做「同號就算重現」的判定，只並排數字。

### A.3 測試
- 研究結果的判定沒了，測試裡相關的斷言要改成檢查「欄位存在、數字合理（例如區間下限 ≤ 估計 ≤ 上限、N 與 M 算對）」。
- **已知答案測試**（`tests/test_policy_worlds.py`、`tests/test_anchor.py`、`tests/test_pitch_policy_method.py` 裡跟統計表現有關的門檻，例如「偵測到的比例 ≥ 0.6」「同號比例 ≥ 0.9」）：這些是用來檢查「工具在已知答案的假資料上找不找得到答案」。照使用者的方向，**改成只輸出數字、不讓 CI 失敗**：把這類測試標記為 `@pytest.mark.report`，在 `pyproject.toml` 註冊這個標記，讓它們把數字寫到 `docs/research/known_answer_report.json`（每次覆蓋），CI 預設不跑（`-m "not integration and not report"`），另外可手動跑。
- **純程式正確性的測試保留**，例如：動態規劃對窮舉解、信賴區域對線性規劃、邊際價值的封閉式對直接重算、機率加總為 1、狀態轉移、設定驗證。這些是「程式算得對不對」，有唯一正確答案，不是研究判定。
- 目前 GitHub CI 失敗的 3 項（W1 偵測比例、對照一致性兩項）在這個工作包之後都屬於 `report` 類，CI 應恢復綠色。推送後到 GitHub 確認 CI 結果（使用者可在網頁上看，或用 `gh run list --branch claude/hello-rc9nc6` 若有安裝）。

---

## 4. 工作包 B：統一的模型介面與偏差控制

### B.1 新模組 `research/outcome_models/`
建立一個套件，所有「預測每一球結果（八類）」的模型都實作同一個介面：

```python
class OutcomeModel:
    name: str                      # "logistic" | "gbm" | "mixed" | "nn_embed" | "sequence"
    supports: dict                 # 每個偏差控制選項是否支援、用什麼方式（給介面顯示）
    def fit(self, train: Columns, valid: Columns | None, settings: dict, progress) -> None
    def predict_proba(self, rows: Columns, *, effects: str = "population") -> np.ndarray   # [n, 8]
    def to_bytes(self) -> bytes; @staticmethod from_bytes(b) -> "OutcomeModel"
```

- `rows` 一律是欄位字典（同 `fetch_columns` 的格式），模型自己決定怎麼轉成特徵；這樣決策引擎和查詢不用知道模型內部。
- `effects`：
  - `"population"`：聯盟平均球員（效應設為 0 或對聯盟分布平均，見 B.3）；
  - `"specific"`：用 `rows` 裡的 `pitcher`、`batter` 欄位的個別效應（沒見過的球員＝聯盟平均）。
- 八類結果的順序固定為 `policy_core.OUTCOMES`。
- 這個工作包先做 `logistic`、`gbm` 兩種；把現在 `league_core.level_matrix`／`gbm.fit_gbm` 的邏輯搬進 `gbm` 實作，不要複製兩份。

### B.2 偏差控制設定（所有模型共用同一組名稱）

| 設定 | 選項 | 意思 |
|---|---|---|
| `control_pitcher` | `none`／`descriptors`／`effect`／`within` | 不控制／用描述特徵（現在的 L1）／每位投手一個效應（資料少往聯盟平均收縮）／只在同一投手自己的球之間比較 |
| `control_pitcher_pitch` | `none`／`effect`／`within` | **每位投手的每一種球各一個效應**（他的滑球本身強不強）。使用者舉的例子「敢連投的人本來那顆球就強」，要擋的其實是這一層，不只是投手整體好壞。`within`＝只在同一投手同一球種的球之間比較（連投效應就是這樣估的）。資料少的組合往「該投手整體」再往聯盟收縮 |
| `control_batter_pitch` | `none`／`effect` | 每位打者對每一種球各一個效應（他是不是特別怕滑球） |
| `control_day` | `none`／`pitcher_game_effect` | 不控制／每位投手每場比賽一個效應（擋當天狀態）；預測時用「平常狀態」（效應 0） |
| `control_batter` | `none`／`descriptors`／`effect` | 同投手 |
| `control_survival` | `none`／`batter_and_count` | 依球數＋打者效應做條件，讓比較發生在同一打者、同一球數之間（需要 `control_batter=effect`，否則設定驗證要報錯並說明） |
| `control_location` | `exclude`／`include_and_average` | 不放進壘位置／訓練時放 `plate_x`、`plate_z`，預測時對「該投手（或聯盟）該球種在該球數的歷史位置分布」抽樣平均（抽樣數設定 `location_samples`，預設 50） |
| `control_count_situation` | 固定開啟 | 球數、出局、壘包、前一球、連投顆數一律是輸入 |
| `control_day_pitch` | `none`／`effect` | 投手×比賽×球種（今天的滑球特別好）。資料極薄，只能強收縮；介面註明「資料很少，效果有限」 |

- 預測沒見過的球員（或沒見過的組合）時，效應一律＝收縮的目標（該投手整體，或聯盟平均）；`within` 的模型無法對沒見過的投手做個別預測，查詢時要明說。
- 每個開關對應的效應數量要在擬合摘要裡列出（投手幾位、投手×球種幾組、投手×比賽幾組），以及收縮強度（估出來或設定的）。
- 稀疏效應欄位可能很多（投手×比賽約數萬組）：一律用稀疏矩陣，不要建成密集矩陣；記憶體只有 16 GB。

### B.3 各模型怎麼實作這些控制
- **邏輯迴歸**（`scikit-learn` 多類別，稀疏設計矩陣）：
  - `effect`：投手（打者、投手×比賽）各一組 one-hot 欄位，每組各自的 L2 懲罰強度（設定 `effect_penalty_pitcher` 等），等同常態隨機效應的懲罰概似；實作上可把欄位乘上 `1/sqrt(懲罰)` 的比例再用單一 `C`，或用 `statsmodels`／自寫 L-BFGS。
  - `within`：每個結果類別的指標先減去該投手的平均（固定效應的去平均法，見 `fixed_effects.demean`），其他欄位同樣去平均，再訓練。預測時加回聯盟平均。
  - `population` 預測：效應欄位全 0。
- **梯度提升樹**（xgboost）：用「偏移量」做法。
  1. 先訓練一個只有效應（投手／打者／投手×比賽）＋情境＋球種的懲罰多類別邏輯迴歸，取每一列的效應部分的 logit 偏移。
  2. 訓練 xgboost 時把偏移放進 `DMatrix(base_margin=...)`，樹學的是扣掉球員效應之後剩下的部分。
  3. 可選回合數 `backfit_rounds`（預設 1；>1 時用樹的輸出當偏移，回頭重估效應，再訓練樹）。
  4. 預測時：`population` 的偏移＝0；`specific` 的偏移＝該球員的效應。
  5. `within`：用同樣的偏移做法，偏移改為每位投手的平均 logit。
  6. scikit-learn 的 HistGradientBoosting 不支援 `base_margin`：這時把 `effect`、`within`、`pitcher_game_effect` 標成不支援，設定驗證時報錯說明。
- `descriptors` 一律沿用 `profiles.py`（history 模式），所有模型都能用。

### B.4 新研究方法 `outcome_model_train`
- 取代 `league_model` 在一鍵流程中的角色（`league_model` 保留，舊紀錄可讀）。
- 設定欄位：模型種類、B.2 全部偏差控制、該模型的所有超參數（樹深、葉節點、學習率、L2、棵數上限、早停輪數、懲罰強度……）、袋裝成員數、年份（訓練年、評估年）、手別組、交叉驗證折數與依據（投手／打者）、隨機種子、`max_rows`。
- 輸出區段（只有數字，沒有判定）：
  - 擬合摘要：每個手別組、每種評估方式的對數損失、Brier、各類結果分開的對數損失、只猜整體比例的對數損失、訓練時間、實際用了幾棵樹或幾輪（**要能看出有沒有被上限截斷**：加一欄「是否碰到上限」）；
  - 學習曲線：訓練與驗證的損失對回合數（每 10 回合一點）；
  - 校準表：每類結果分 10 箱的預測平均與實際比例；
  - 常識對照：模型預測的「各球種揮空率、全壘打率、界內率」（`population`）對比實際觀察值，按手別組×球種列出；
  - 球種效果：在 `population` 下，每個（球數階段 × 球種）相對一般配球的各類結果機率差（沿用 `anchor.model_contrasts` 的定義），附袋裝區間；
  - 偏差控制的實際做法：每個開關在這個模型上用了什麼方式；
  - 換年份重看：訓練年份 ≥ 2 時，每個年份單獨訓練一份（`replication_members`），球種效果與預測增益並排列出；
  - 球員描述（或效應）的預測增益：對沒見過的球員、沒用過的年份，加了球員資訊比不加好多少（數字與區間，取代原本的閘門）。
- 長時間訓練要回報進度（`ctx.progress`），並支援一鍵流程的「停止」。
- 存模型包（附件），內容足夠讓決策引擎與查詢重建模型、做 `population` 與 `specific` 預測。

### B.5 參數搜尋改成適用所有模型
- `param_search` 改成：設定裡選模型種類，搜尋格子依模型種類不同（梯度提升樹：樹深、葉節點、學習率、L2；邏輯迴歸與混合效應：各效應的懲罰強度；神經網路：學習率、隱藏層、嵌入維度、丟棄率…），格子本身也是可在完整設定裡改的欄位。
- 打分方式設定：依投手分折、依打者分折、依年份，可複選；輸出每組設定的分數，選最低者給後續步驟（不叫「通過」）。
- 一鍵流程裡參數搜尋可以關掉（直接用完整設定裡填的參數）。

### B.6 年份限制：拿掉（使用者已決定）
現在的程式規定建模只能用 2023–2024、2025 與 2026 保留給最後檢驗，違反就報錯（`research/guards.py` 的 `check_purpose`、`check_modeling_scope`，以及各方法裡的 `purpose` 欄位與相關檢查）。**新的訓練、比較、決策引擎、查詢、一鍵流程都不再套用這條規則**：任何年份組合都可以選。舊的研究方法（連投、前一球那些）不要改。介面上只顯示「目前選的訓練年份、評估年份」，不加警告。

### B.7 測試（假資料）
- 用 `research/policy_worlds.py` 的 W4（投手好壞混淆）、W5（當天狀態混淆），並**新增一個世界**：某些投手的某一種球特別強，而且他們連投那一種球比較多，但連投本身沒有效果（就是使用者舉的例子）。`control_pitcher_pitch=effect`／`within` 時，模型的連投效果應回到 0 附近；只用 `control_pitcher=effect` 時則不會：
  - `control_pitcher=none` 時，模型的滑球效果偏離 0；
  - `effect` 或 `within` 時，回到 0 附近；
  - W5 需要 `control_day=pitcher_game_effect`。
  - 這些是 `report` 類測試：輸出數字，不讓 CI 失敗。
- 程式正確性（CI 會跑）：
  - `population` 與 `specific` 對沒見過的球員給相同預測；
  - 偏移量版 xgboost 在偏移全 0 時等於一般 xgboost；
  - 設定組合不合法時有清楚的錯誤訊息；
  - 存讀模型包前後預測一致。

---

## 5. 工作包 C：決策引擎、情境查詢改用訓練好的模型

- `pitch_policy` 新增必填欄位 `model_run_id`（一筆 `outcome_model_train` 紀錄），**移除**它自己訓練 L0 的程式（`policy_build.fit_outcome_members` 不再被 `pitch_policy` 使用）。版本加 1。
- `policy_build.outcome_tensor` 改成接受 `OutcomeModel`，用 `predict_proba(rows, effects="population")` 填情境表。若模型用了 `control_location=include_and_average`，在這裡做位置抽樣平均。
- 袋裝區間：用模型包裡的各個成員分別預測。
- 查詢的 M1–M3（指定投手、打者）：改用同一個模型的 `effects="specific"`。沒見過的球員自動等於聯盟平均，回應裡標示「此球員沒有資料，等於聯盟平均」。
- 情境表和查詢回應都要附上「這個模型用了哪些偏差控制」，頁面上顯示，讓使用者知道數字是在什麼假設下算的。
- 一鍵流程 `pipeline.py` 的步驟改成：參數搜尋（可關）→ `outcome_model_train` → `action_effects`（保留作為額外對照，可關）→ `pitch_policy` → `pitch_query`。
- 測試：W1（滑球真的較好）在 `control_pitcher=effect` 下，情境表的滑球優勢仍為負（有利投手）；W4 在 `control_pitcher=effect` 下，情境表的滑球優勢接近 0（`report` 類）。程式正確性：引擎拿到的機率陣列加總為 1、和模型直接預測一致。

---

## 6. 工作包 D：完整設定模式與設定檔

- **設定的單一來源**：每個研究方法的 `fields`（`ConfigField`，`/api/research/methods` 已能列出名稱、型別、預設、選項、中英說明）。前端用這份清單**自動產生表單**，不要手寫每個欄位，這樣新增設定不必改前端。
- 一鍵研究頁上方加切換：「簡單模式」｜「完整設定模式」。
  - 簡單模式：維持現在的運算量、手別組。
  - 完整設定模式：按流程步驟分頁（參數搜尋、訓練模型、對照、決策引擎、查詢），每頁列出該步驟研究方法的**全部**欄位；有預設值、有中英說明、選項類用下拉選單、列表類用多選、JSON 類用文字框並即時驗證。
  - 從簡單模式切到完整模式時，表單帶入目前簡單模式對應的完整設定，可以接著改。
- 設定檔格式（JSON，UTF-8，**不要寫 BOM**）：
  ```json
  {"format": 1, "created_at": "...", "groups": ["RvR"], "years": [2023, 2024],
   "steps": {"search": {...}, "train": {...}, "anchor": {...}, "policy": {...}, "query": {...}},
   "enabled": {"search": true, "anchor": true}}
  ```
- 匯出：產生上面的 JSON，用瀏覽器下載（`Blob` + `<a download>`，不用 `localStorage`）。
- 匯入：上傳 JSON → 呼叫新的 API `POST /api/research/pipeline/validate`，後端用各方法的 `normalize_config` 與 `validate` 逐步驗證，回傳每個欄位的錯誤 → 表單標出錯誤欄位。驗證通過才可以開始。
- `pipeline.py`：`start()` 接受完整設定（`steps` 結構），不只 `tier`；`TIERS` 變成「簡單模式的預設組合」。
- 測試：匯出再匯入得到相同設定；錯誤欄位的訊息正確；Chromium 測試（參考 `tests/test_pipeline_page_browser.py` 的寫法）切換模式、改一個欄位、匯出、匯入。

---

## 7. 工作包 E：比較模式與時間預估

- 新研究方法 `model_compare`：設定裡勾選多種模型（以及各自的設定，可以從完整設定帶入），用**同一份資料、同一組切分、同一個種子**訓練每一種，輸出並排表：
  - 每種模型的留出年份對數損失、Brier、各類結果損失、校準摘要、訓練時間；
  - 各模型的球種效果（同 B.4 的定義）並排，以及每兩種模型之間的差；
  - 每種模型用了哪些偏差控制。
  - 沒有「哪個最好」的判定，只排序顯示（使用者可選排序欄位）。
- 介面：一鍵研究頁加「比較模式」分頁，勾選方法 → 先按「預估時間」→ 顯示預估 → 再按開始。
- **時間預估**（`POST /api/research/pipeline/estimate`）：
  1. 對每種勾選的方法，在約 2% 的資料上用相同設定實際跑一小段（梯度提升樹 20 棵、神經網路 1 輪、邏輯迴歸完整跑），量出時間；
  2. 依方法的成本規則外推到全量：
     - 梯度提升樹 ∝ 列數 × 棵數 × 袋裝數 ×（交叉驗證折數＋1）；
     - 神經網路 ∝ 列數 × 輪數；
     - 混合效應依迭代數；
  3. 回傳每種方法、每個手別組的預估，並寫明「粗估，實際可能是 0.5 到 2 倍」；
  4. 試跑本身的時間也顯示出來。
- 執行中：每個步驟顯示已用時間，以及用當前進度推算的剩餘時間。
- 測試：預估 API 回傳合理結構；比較模式在假資料上兩種方法並排輸出。

---

## 8. 工作包 F：混合效應、神經網路、序列模型

**開始前先問使用者是否同意安裝 PyTorch**（深度學習套件，神經網路與序列模型需要它，混合效應模型也用它在 GPU 上估計；CUDA 版約 2–3 GB，裝進 `D:\soft\Python313`，加 `--no-cache-dir`）。同意後再裝，並在 `pyproject.toml` 加成選用依賴（例如 `[torch]`），沒裝時這三種模型在介面上顯示「需要安裝 PyTorch」，不能選。

三種模型都實作 B.1 的介面與 B.2 的偏差控制：
- **混合效應（`mixed`）**：多類別邏輯迴歸，投手、打者（、投手×比賽）各一組隨機效應向量（每類一維），常態先驗，變異數由資料估計（經驗貝氏；可用 PyTorch 交替最佳化，或把變異數當超參數在驗證集上選）。這是最直接處理「投手好壞混淆」的模型。
- **神經網路＋嵌入（`nn_embed`）**：投手、打者各一個嵌入向量（維度可設定），加上情境與球種，接幾層全連接到八類輸出；嵌入有 L2 懲罰（等同向聯盟平均收縮）；`within`／`pitcher_game_effect` 用額外的偏移嵌入實作；`population` 預測時嵌入用 0 向量。
- **序列模型（`sequence`）**：輸入同一打席之前的所有投球（球種、結果、球數），用 GRU（預設）或 Transformer（可選）編碼，再接情境、球種、嵌入到八類輸出。決策引擎目前的狀態只有前一球與連投顆數，所以這種模型的預測在引擎裡要「對該狀態下歷史上出現過的前序序列」抽樣平均，或只在查詢頁提供；做之前把兩種做法的差別寫給使用者選。
- 共同設定：批次大小、學習率、輪數、早停、隱藏層大小、嵌入維度、丟棄率、權重衰減、裝置（cpu／cuda）。
- 記憶體：你的 GTX 1650 只有 4 GB 顯存，桌面程式平時佔約 3 GB。預設批次要小，顯存不足時自動降批次並在進度訊息說明。
- 測試：每種模型在假資料 W4 上的球種效果（`control_pitcher=effect`）、存讀一致、`population`／`specific` 一致性。

---

## 9. 工作包 G：文件與收尾

- 更新 `docs/research/P6_REMOTE.md`（白話寫：簡單模式、完整模式、比較模式、設定檔怎麼用）。
- 在 `docs/research/P6_LOG.md` 每個工作包加一列（日期、做了什麼、測試結果、和這份指導不同的地方）。
- 和這份指導做法不同的地方，一律寫進紀錄並在回報時告訴使用者，不要默默改。

---

## 10. 回報格式（每個工作包結束時）

1. 做了什麼（白話，三到五點）。
2. 測試：CI 會跑的測試結果；`report` 類測試的數字（附 `known_answer_report.json` 重點）；Chromium 測試結果。失敗照實貼。
3. 提交編號與推送結果；GitHub CI 是否為綠色。
4. 和指導不同的地方、做不到的地方。
5. 需要使用者決定的事（如果有）。
