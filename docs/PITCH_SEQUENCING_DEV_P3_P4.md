# 配球序列研究系統 — P3、P4 開發文件

**狀態：可照做。** 日期：2026-10-06。接續 [`PITCH_SEQUENCING_DEV.md`](PITCH_SEQUENCING_DEV.md)（下稱 DEV，P1、P2 已完成）。「做什麼、為什麼」以 [`PITCH_SEQUENCING_PLAN.md`](PITCH_SEQUENCING_PLAN.md)（下稱規劃文件）為準；本文件只寫「怎麼做」。兩份不一致、或兩份都沒涵蓋的選擇，**停下來問使用者**。

目錄：0 使用規則 · 1 範圍 · 2 已定案的設計決定 · 3 已驗證的事實與陷阱 · 4 參考實作（檔案對照）· 5 任務 · 6 五個方法的設定與輸出 · 7 驗收 · 8 停下來問 · 9 待使用者決定 · 附錄 A 試跑數字 · 附錄 B 測試與預期

---

## 0. 使用規則

- 沿用 DEV 第 0 節（分支、commit 與署名、不開 PR、測試指令、`PYTHONPATH=src`、程式慣例、雙語訊息、不得默默截斷）。
- **基準**：P2 完成時 `334 passed`。本階段新增約 72 個測試，完成後應為 **約 406 passed**（`-m 'not integration'`）。每個任務結束都不得低於前一任務的通過數。
- **這份文件的做法與 P1、P2 不同**：參考實作**已經寫好並在 scratch 環境完整驗證**（單元測試、真實資料 2023–2024 例行賽 143 萬球、兩個後端、可重現性），放在 [`reference_p3_p4/`](reference_p3_p4/)。開發 ＝ 把它複製進 repo、套用小修補、跑測試、在真實資料上驗收、補文件。**不要重寫邏輯**；要改就先讀第 3 節的陷阱。
- 參考實作樹的路徑與 repo 路徑一致：`docs/reference_p3_p4/src/...` → `src/...`；`docs/reference_p3_p4/tests/...` → `tests/...`。`docs/reference_p3_p4/patches/` 內三個依序套用的修補（`01_method_inputs_hook`、`02_register_p3_methods`、`03_register_p4_methods`）是對兩個既有檔（`research/methods.py`、`research/service.py`）的修改，用 `git apply` 套用（三個依序 `git apply --check` 已驗證，套完與驗證版逐字相同）。
- 開發完成、使用者驗收後，`docs/reference_p3_p4/` 整個資料夾刪除（內容已進入 `src/`、`tests/`）。刪除在 T5.1 做。
- 每個任務：複製檔案 → 跑該任務列出的測試 → 全套測試 → commit → push。一次一個任務。

---

## 1. 範圍

**P3（直接數次數系統）**：得分期望值表與結果價值（`run_expectancy`）、情境×配球選擇的結果機率表（`outcome_table`）、連投曲線與「走到同一點再比較」（`streak_curve`）。
**P4（模型與驗證）**：多類別結果模型與序列增益（`outcome_model`）、合成資料已知答案自我檢查（`synthetic_check`）。

**非目標**：網頁表單與圖表（結果用既有「研究紀錄」頁與 CLI 看；圖表排在 P5 的第一步，使用者 2026-10-06 決定）、多球往後看（P6）、研究本身（P5）。球棒追蹤與擊球資料**不是**非目標：作為結果分項放進 `outcome_table`（T3.4b）。

---

## 2. 已定案的設計決定

| 決定 | 內容 |
|---|---|
| 結果價值 | `pitch_value` ＝ RE(下一球狀態；半局最後一球為 0) ＋ 該球得分 − RE(本球狀態)，打擊方視角。狀態碼 ＝ 球 ×1000 ＋ 好 ×100 ＋ 出局 ×10 ＋ 壘包（1／2／4 位元），共 288 種。RE ＝ 該狀態之後到半局結束的平均得分，**排除再見半局**（最後一個半局、下半、主隊贏）。RE 以全部列計算（未排除短打與非投球列），結果價值只對未排除的投球 |
| RE 表來源固定 | 由 `re_scope`（預設 2023–2024 例行賽）計算，**不隨分析範圍變**；`re_scope` 的資料指紋進入 run key（`ResearchMethod.method_inputs` 新鉤子），所以建模期資料改變時舊結果不會被誤用 |
| 「同一點」 | 同一組分層（預設：打席內第幾球、好球數、壞球數）內，比較連投第 k 顆與第 1 顆的比率差，各層權重 n_k·n_1/(n_k+n_1)，每層每個 k 至少 30 球；不能比的（沒有同時含 k 與 1 的層）回報「沒有數字」而不是假數字。分層欄位不可用描述前一球的欄位（`prev*`、`history_key`、`streak_pos`），否則 k=1 與 k≥2 永遠不在同一層 |
| 殘餘偏誤與對策 | 不同球種的揮空危險率不同時，基本分層仍有偏誤（合成世界 T2 實測 −0.043，真值 0）。加分層欄位 `prior_same_type_count`（本打席更早的同球種球數）可消除，代價是可比較的球減少（覆蓋率 99.5% → 33%）。兩者並列顯示，覆蓋率永遠一併輸出 |
| 標準誤 | 比例用 Wilson；平均價值用**分群穩健標準誤**（預設以**投手**分群（使用者 2026-10-06 決定；可改打席／打者／比賽）），以兩層彙總的和計算；連投差預設解析式 SE，可選以投手（等）分群的 Poisson bootstrap |
| 左右手 | **P5 與之後的正式結果一律四組分開，不鏡像、不合併**（使用者 2026-10-06 決定；原因：鏡像後座標方向正確，但左右打者的行為與判決不同，同一格的結果不同）。`same_opposite` 保留為預設關閉的選項，不使用。基礎永遠四組分開。`same_opposite` 是衍生檢視：左投手資料鏡像（`location_frame=mirrored`），**在 Python 把四組的和相加**（分群單位為打席或投手時才成立，否則拒絕），並輸出對稱性檢查（RvR vs LvL、RvL vs LvR 逐格 z 檢定） |
| 用途護欄 `purpose` | `tuning`（預設）：範圍不得含 2025、2026；`final_test`：範圍只能是 2025／2026（模型：訓練球季不得含、留出球季必須是保留球季，且 `split=holdout_years`）；`extra_study`：任意範圍（模型只能 `grouped_kfold`，因為沒有乾淨檢驗集）。`re_scope` 除 `extra_study` 外不得含 2025、2026。`purpose` 在 config 內，所以進入 run key 且在紀錄中可見 |
| 模型 | 每個左右手組各自擬合；多類別邏輯迴歸與 HistGradientBoosting；預設 8 類（含全壘打獨立一類）；切分為 `holdout_years`（預設訓練 2023、留出 2024）或依比賽分組的 `grouped_kfold`；特徵組 `base`（無前球資訊）對 `full`（加序列特徵），**序列增益 ＝ 留出資料上 logloss(base) − logloss(full)，附依比賽重抽的 Poisson bootstrap 區間**（正值＝序列資訊有用）|
| 不做 | 收縮、反事實、兩階段殘差（規劃文件 4、2.3）；模型存檔（P6 再決定，現只在 holdout 時輸出邏輯迴歸係數）；網頁表單 |

---

## 3. 已驗證的事實與陷阱（動手前必讀）

| # | 事實／陷阱 | 對策（已在參考實作） |
|---|---|---|
| 1 | **SQLite 會把不存在的欄位名（雙引號）當成字串常數**，不報錯；DuckDB 才報錯。打錯欄位在 SQLite 路徑會得到**錯的數字** | `analysis/lint.py` 的 `lint_columns`：執行前逐節點檢查每個欄位是否由上游提供；`research/runner.py` 的 `run_checked` 一律先 lint |
| 2 | `AnalysisEngine` 在 DuckDB 出任何錯誤時**靜默退回 SQLite**（慢 100 倍，且掩蓋 1） | `run_checked`：`analysis_backend=="duckdb"` 而結果來自 SQLite → `RuntimeError`；所有測試斷言 `backend=="duckdb"` |
| 3 | DuckDB 鏡像只在 `data_revision` 改變時重建；測試重建資料庫卻沿用舊 `.duckdb` 會讀到舊資料 | `create_pitches_db` 重建時刪除同名 `.duckdb` |
| 4 | `inning` 是 TEXT，不可做算術；DuckDB 的 `/` 是浮點除法 | 狀態碼只用乘加；`bases` 另存欄位 |
| 5 | 再見半局：最後一個半局＋下半＋主隊最終比分較高。已播完的雨停比賽可能誤判（罕見，已接受） | `run_context` 的 `is_walkoff_half`；其 `pitch_value` 為 NULL，並顯示 `n_value` |
| 6 | 結果 JSON 不得含 NaN；浮點加總順序（DuckDB 多執行緒）使最後幾位不同 → 「可重現」旗標會誤判 | `sections.clean`：浮點四捨五入（計數類 10 位、模型類 8 位）、NaN→None；已實測 `rerun` 得 `reproduced=True` |
| 7 | `webapp` 匯入不得載入 numpy／sklearn／duckdb（既有測試 `test_lazy_startup_imports`） | 方法模組頂層不得 `import numpy`／`sklearn`；一律函式內匯入（曾因 `synthetic` 預設值踩到並已修正） |
| 8 | 在打席內打亂球種的安慰劑，**有真實連投效應時不以 0 為中心**（因為打亂後的連投與原本的連投相關）。正確讀法是「觀察值相對安慰劑帶」，不是相對 0 | 輸出 `observed`、`placebo_mean/lo/hi` 與置換 p 值；測試在 T0（觀察值在帶內）與 T1（觀察值在帶外）都驗證 |
| 9 | 全套測試中有 3 個既有測試（`test_research_ui_wiring`、兩個 `test_stage4d_wiring`）需要 repo 內其他資料夾；在只複製 `src/tests` 的環境會失敗，**在完整 repo 內通過** | 只在完整 repo 內判斷全套測試 |
| 10 | `numpy` 逐列索引時重複套遮罩會變成 O(n²)（曾讓測試跑 10 分鐘以上） | `oracle_cells` 先遮罩一次再 `np.unique` |

**試跑中看到、但不是研究結論的現象**（附錄 A 有數字；P5 計畫要處理，請在驗收回報中原樣轉告使用者）：
(a) 未控制投手與打者時，同一點比較得到「連投後揮空率**上升**」，而且打亂球種的安慰劑給出同樣或更大的正值——表示這個數字主要來自打席組成的選擇效應。(b) 同側／異側鏡像的對稱性檢查顯示許多格子明顯不對稱（例如 RvR vs LvL 的壞球率，43% 的格子 |z|>1.96），合併檢視只可當輔助。(c) HistGradientBoosting 加入序列特徵後留出集 logloss 變差，邏輯迴歸則有微小進步（+0.0006）；序列資訊在豐富的逐球特徵之上增益很小。

---

## 4. 參考實作（檔案對照）

新增（複製）：

| 參考實作 | 內容 |
|---|---|
| `src/treepolo_mlb_data/analysis/run_values.py` | 狀態碼、`run_context`、`re_table_node`、`lookup_tree`（平衡 CASE 樹）、`pitch_value_field` |
| `analysis/lint.py` | `lint_columns` |
| `analysis/pitch_table.py` | `build_pitch_table`、`apply_filters`、`pitch_table_columns`、`rate_terms`、`streak_cells_node`、`outcome_cells_node`、欄位選項常數 |
| `research/stats.py` | `wilson_interval`、`cluster_mean_se`、`mean_interval`、`two_proportion_z`、`z_value` |
| `research/streak.py` | `same_point_curve`、`cluster_bootstrap_same_point` |
| `research/placebo.py` | `placebo_same_point` |
| `research/guards.py`、`runner.py`、`re_source.py`、`sections.py` | 用途護欄、`run_checked`、RE 表（含程序內快取與 `method_inputs`）、結果節輔助 |
| `research/sequencing_methods.py` | `run_expectancy`、`outcome_table`、`streak_curve` |
| `research/modeling.py`、`modeling_methods.py` | 模型函式庫；`outcome_model`、`synthetic_check` |
| `research/synthetic.py` | 全欄位 `pitches` 假資料建構（`make_row` 等）、合成世界、已知答案自我檢查 `run_checks` |
| `tests/` 11 個檔 | `pitch_fixtures`、`run_fixtures`、`state_fixtures`（輔助）與 `test_run_values`、`test_research_stats`、`test_lint_columns`、`test_pitch_table`、`test_synthetic_worlds`、`test_synthetic_checks`、`test_sequencing_methods`、`test_modeling` |

修改既有檔（`patches/` 三個修補）：`research/methods.py` 新增 `ResearchMethod.method_inputs()`（預設 `{}`）；`research/service.py` 的 `make_run_key(..., inputs=None)`（`inputs` 為空時 key 與原來完全相同，**既有 run key 不變**）、`run()` 呼叫 `method_inputs` 並把結果存入 `data_fingerprint["method_inputs"]`，並匯入兩個新方法模組。

---

## 5. 任務

### T3.1 分析層：`run_values`、`lint`、`pitch_table` ＋ 假資料輔助

複製：`analysis/run_values.py`、`analysis/lint.py`、`analysis/pitch_table.py`、`research/synthetic.py`；`tests/pitch_fixtures.py`、`run_fixtures.py`、`state_fixtures.py`、`test_run_values.py`、`test_lint_columns.py`、`test_pitch_table.py`。
（`synthetic.py` 的 `run_checks` 與世界函式此時只被延後匯入，不影響。）
測試：`pytest tests/test_run_values.py tests/test_lint_columns.py tests/test_pitch_table.py`（應 7＋2＋9 個通過）。重點：附錄 B 的手算值（狀態碼、RE、`pitch_value`、再見半局旗標）在兩個後端都一致；`pitch_table_columns` 宣告的欄位名與實際輸出一致。
Commit：`Add run values, column lint and pitch table builders (T3.1)`。

### T3.2 統計與連投估計：`stats`、`streak`、`placebo`

複製 `research/stats.py`、`streak.py`、`placebo.py`、`tests/test_research_stats.py`。
測試：`pytest tests/test_research_stats.py`（Wilson 已知值 5/10→[0.2366, 0.7634]；分群 SE 與暴力計算相同；同一點已知答案：A、B 兩層，估計值 ＝ (−0.10×100/3)/(100/3+75)，覆蓋率 150/350；無可比層→`None`；bootstrap SE 與解析 SE 比值在 0.75–1.33）。
Commit：`Add proportion/cluster statistics and same-point streak estimators (T3.2)`。

### T3.3 服務鉤子與研究輔助

套用 `patches/01_method_inputs_hook.patch`（`git apply`）。複製 `research/guards.py`、`runner.py`、`re_source.py`、`sections.py`。
既有的 P1 測試必須全部仍通過（`inputs` 為空時 key 不變）。
Commit：`Add method_inputs hook, purpose guards and checked query runner (T3.3)`。

### T3.4 P3 三個方法

複製 `research/sequencing_methods.py`、`research/stats.py` 之外已有的檔案不需重複；複製 `tests/test_sequencing_methods.py`；套用 `patches/02_register_p3_methods.patch`。
測試：`pytest tests/test_sequencing_methods.py`（22 個）。涵蓋：已知答案（每個狀態 RE＝1.0、SE＝1.0）、重用與等價 `re_scope` 寫法共用 key、**範圍外的 RE 資料改變就不重用**、用途護欄、非法設定、`max_cells` 報錯不截斷、RE 範圍缺狀態報錯、SQLite 專用設定可跑、DuckDB 靜默退回即報錯、`streak_curve` 經服務執行（含 bootstrap、安慰劑、`rerun` 得 `reproduced=True`）。
Commit：`Add run_expectancy, outcome_table and streak_curve research methods (T3.4)`。

### T3.4b 結果分項：擊球資料與球棒追蹤（**無參考實作，依下列規格自行寫並加測試**）

使用者 2026-10-06 決定要放。兩組欄位，各自標示覆蓋率，**缺值不填補、不計入**：
- 擊球資料：`launch_speed`、`launch_angle`（界外與界內球有；擦棒沒有）。
- 球棒追蹤：`bat_speed`、`swing_length`、`attack_angle`、`attack_direction`、`swing_path_tilt`、`intercept_ball_minus_batter_pos_x_inches`、`intercept_ball_minus_batter_pos_y_inches`、`miss_distance`（只有揮空有）。2023-07-14 之前整段缺，是結構性缺失。

做法：`outcome_table` 新增設定 `swing_metrics`（`str_list`，預設 `[]`，只能是上列欄位，否則 `ConfigError`）。對每個欄位，`outcome_cells_node` 的兩層彙總各加 `count(欄位)`（有值球數）與 `sum(欄位)`；Cells 節多 `<欄位>_n`、`<欄位>_mean` 兩欄（`n`＝0 時 mean 為 `None`）。欄位不要鏡像（`MIRROR_RULES` 皆為 none）。`extras` 記錄各欄位整體有值比例。需要把欄位加進 `build_pitch_table` 的 `extra_columns`。
測試（`tests/test_swing_metrics.py`）：用 `make_row` 造 4 球（兩球有 `launch_angle`、一球 NULL、一球另一欄位有值），核對 `_n`、`_mean`、NULL 不計入、未知欄位報錯、兩個後端一致。
Commit：`Add swing and batted-ball breakdowns to outcome_table (T3.4b)`。

### T3.5 P3 真實資料驗收

見第 7.1 節。把「P3 acceptance」研究專案匯出成研究檔告知使用者。
Commit：（若有微調）`P3 acceptance notes`。

### T4.1 模型函式庫

複製 `research/modeling.py`。其函式單元測試在 `test_modeling.py`（T4.2 一起複製）。

### T4.2 `outcome_model` 與 `synthetic_check`

複製 `research/modeling_methods.py`、`tests/test_modeling.py`、`tests/test_synthetic_worlds.py`、`tests/test_synthetic_checks.py`；套用 `patches/03_register_p4_methods.patch`。
測試：`test_modeling.py`（16 個，約 70 秒）、`test_synthetic_worlds.py`（8 個，約 50 秒）、`test_synthetic_checks.py`（2 個，約 160 秒）。**這三個檔合計約 5 分鐘**，是正常的（合成世界各 30 萬打席）。
Commit：`Add outcome_model and synthetic_check research methods (T4.2)`。

### T4.3 P4 真實資料驗收

見第 7.2 節。

### T5.1 收尾

1. 刪除 `docs/reference_p3_p4/`。
2. README 的 Research runs 那一條補上五個新方法名稱；`research methods` 指令能列出七個方法（`analysis_payload`、`data_profile`、`outcome_model`、`outcome_table`、`run_expectancy`、`streak_curve`、`synthetic_check`）。
3. `docs/PITCH_SEQUENCING_PLAN.md` 第 10 節 P3、P4 列註記完成日期；第 11 節第 3、5 項依使用者在第 9 節的決定更新。
4. `PITCH_SEQUENCING_DEV.md` 第 3 節的 P3、P4 列改為指向本文件。
5. 在本文件末尾新增「附錄 C：P3、P4 完成紀錄」，寫下實作與本文件不同之處與實測耗時。
6. 全套測試通過、commit、push。**停下來向使用者回報**（內容見第 7.3 節）。

---

## 6. 五個方法的設定與輸出

所有方法：`purpose`（預設 `tuning`）；設定欄位的完整清單、型別、預設與中英說明在各類別的 `fields`（`research methods` 指令也會列出）。下表只列要點。結果浮點已四捨五入（見第 3 節 #6）。

**`run_expectancy`**（`requires_scope=True`）：`confidence`、`bunt_policy`、`min_state_n`(30)、`outcome_values`、`savant_comparison`。節：①`Run expectancy by state`（288 列：狀態、n、半局數、re、se、區間、`low_n`）；②`Outcome values by state`（各狀態×結果的 n、平均價值、標準差）；③`Comparison with Savant delta_run_exp`（各 `description` 的我方平均、Savant 平均、差）。`extras` 含相關係數。

**`outcome_table`**：`re_scope`、`use_values`、`bunt_policy`、`memory`(2)、`situation_fields`(`balls`,`strikes`)、`choice_fields`(`pitch_type`)、`hand_views`(`four_groups`)、`location_frame`(`raw`)、`loc_x_edges`、`loc_z_edges`、`filters`、`cluster_by`(`pitcher`)、`min_samples`(100)、`max_cells`(20000)、`symmetry_top`。可用欄位見 `pitch_table.CELL_FIELD_CHOICES`。節：每個檢視一組「Cells」（n、揮棒率、揮空率／揮棒、全壘打率各附 Wilson 區間、平均價值與分群 SE、各類別次數、`low_n`）與「Outcome probabilities」（長表：每格×每類別的次數、機率、區間）；`same_opposite` 另有兩個對稱性檢查節。
`filters`：`[{"field":"pitcher","op":"eq","value":543037}]`，在序列欄位算完之後才篩選（連投以完整打席計算）。

**`streak_curve`**：`rate`（`whiff_per_swing` 預設；另有 `swing_rate`、`called_strike_per_take`、`foul_per_swing`、`in_play_per_swing`、`hr_per_pitch`、`hr_per_in_play`、`mean_pitch_value`）、`foul_tip_is_whiff`、`pitch_types`（空＝全部）、`min_type_pitches`(5000)、`kmax`(5，代表「5 顆以上」；**只對 k=2、3 下結論**，更高的 k 樣本太薄)、`strata_fields`、`hand_view`(`four_groups`｜`same_opposite`｜`pooled`)、`min_stratum_n`(30)、`se_method`(`analytic`｜`cluster_bootstrap`)、`cluster_by`(`pitcher`)、`bootstrap_reps`、`seed`、`placebo_shuffles`(0＝不做)。節：原始曲線（含存活偏誤，標題已註明）、同一點比較（估計、SE、區間、使用層數、覆蓋率、可選 bootstrap 欄）、可選安慰劑。

**`outcome_model`**：`groups`、`models`、`variants`、四組特徵清單（`base_numeric`、`base_categorical`、`sequence_numeric`、`sequence_categorical`；選項見 `modeling.NUMERIC_FEATURES`／`CATEGORICAL_FEATURES`，`count_state`、`streak_cap` 由程式衍生）、`memory`、`streak_cap`、`class_merge`（結果類別→模型類別，預設 8 類）、`min_class_count`、`split`、`train_years`、`test_years`、`n_folds`、邏輯迴歸與提升樹超參數、`seed`、`bootstrap_reps`、`calibration_bins`、`ev_check`（期望得分檢查，需要 `re_scope`）、`table_consistency`、`consistency_min_n`、`max_rows_per_group`。節：擬合摘要（logloss、Brier、僅用先驗的 logloss、skill、秒數、被丟棄列數）、序列增益（附區間；有 `ev_check` 時另有期望得分 MSE 增益）、各真實類別的增益貢獻、校準、與次數表一致性、期望得分檢查；附件 `logistic_coefficients`（僅 holdout）。

**`synthetic_check`**（`requires_scope=False`）：`groups`、`n_pa`(≥300000)、`seed`。節：每個已知答案檢查一列（世界、檢查、預期、觀察、是否通過）；`extras.all_passed`。**任何一列沒通過都代表管線有錯，先修，再做研究。**

---

## 7. 驗收

先確認環境：`df -h .`、`data/statcast.duckdb` 存在（沒有就 `analytics-sync`）。CLI 寫法見 DEV 0.2。設定檔例（`p3.json` 等）：

```json
{"scope": {"game_years": [2023, 2024], "game_types": ["R"]}}
```

### 7.1 P3（每個都用 `--study "P3 acceptance"`）

| 方法與設定 | 預期（2026-10-06 開發環境實測，DuckDB） |
|---|---|
| `run_expectancy`，上面的設定 | 約 10 秒；288 狀態、最小 n＝37；RE(0-0、無人出局、壘空)＝0.4988（se 0.0037）、滿壘無出局約 2.283、一出局壘空 0.267、兩出局壘空 0.101；與 Savant 各 `description` 平均差的絕對值 ≤ 0.015，相關係數約 0.85，全體平均價值約 −0.0003 |
| `outcome_table`，預設 | 約 7 秒；732 格、機率表 8,784 列；**同設定再跑一次 → `reused: true`** |
| `outcome_table`＋`"hand_views":["four_groups","same_opposite"],"location_frame":"mirrored","choice_fields":["pitch_type","zone"],"min_samples":300` | 約 10 秒；有兩個對稱性檢查節 |
| `streak_curve`＋`"hand_view":"pooled","pitch_types":["SL","FF"]` | 約 3 秒；SL 原始曲線 k=1 0.318；同一點 k=2 約 +0.035、FF 約 +0.018（**僅供對照合理性，不是結論**） |
| `streak_curve`＋`"se_method":"cluster_bootstrap","placebo_shuffles":10,"pitch_types":["SL"],"kmax":4` | 完成；安慰劑節有數字 |

容許誤差：次數與 n 必須完全相同；比率與價值差 ≤ 0.001；耗時超過表列 5 倍 → 停下來問。再各跑一次 `research rerun <id>`，確認 `reproduced: true`。

### 7.2 P4

1. `synthetic_check`（預設）：全部通過，約 160 秒。
2. `outcome_model`，`groups:["RvR"]`、`bootstrap_reps:50`：約 200 秒；擬合摘要預期（RvR、訓練 2023、留出 2024，265,583 列）：邏輯迴歸 base logloss ≈ 1.2285、HGB base ≈ 1.2152、僅用先驗 ≈ 1.6298；序列增益邏輯迴歸 ≈ +0.0006（區間約 [0.0004, 0.0007]）、HGB 為負。**四組全跑**約 12 分鐘：確認完成並匯出研究檔。
3. 護欄：對 `scope` 含 2025 的 `tuning` 設定執行，必須得到 `ConfigError`。

### 7.3 回報內容

資料量與耗時、每個方法的驗收數字與預期的差異、`synthetic_check` 結果、第 3 節末尾三個試跑現象（原樣轉告）、任何偏離本文件的地方。

---

## 8. 停下來問使用者的情況（除了 DEV 0.5 節）

- 參考實作的測試在乾淨環境失敗，且原因不是第 3 節 #9。
- 真實資料驗收的數字與第 7 節差超過容許誤差。
- `synthetic_check` 任何一項失敗。
- 需要修改規劃文件已定案的定義（類別、排除、RE 的定義）。
- 要新增 Python 套件依賴（目前只用 numpy、scipy、scikit-learn、duckdb，皆已是依賴）。

---

## 9. 使用者已決定（2026-10-06）與尚待討論

已決定：①球棒追蹤與擊球資料作為結果分項放進 P3（T3.4b），模型不使用；②圖表在 P3、P4 開發完後、P5 研究開始前做（P5 第一步）；③四組分開、不鏡像不合併；④標準誤預設以投手分群；⑤連投比較要控制投手（寫進 P5 研究計畫，程式已支援 `strata_fields` 加 `pitcher`）。
尚待討論：無。

---

## 附錄 A：開發環境試跑數字（非研究結論）

範圍 2023–2024 例行賽（1,427,348 球；排除後 1,413,281 球），DuckDB。

- RE：(0-0, 0 out, 空) 0.4988；n 最小 37；Savant 對照各 `description` 平均差（我方−Savant）：ball −0.0021、foul −0.0009、hit_into_play +0.0017、called_strike +0.0010、swinging_strike −0.0004、blocked_ball +0.0149。
- SQLite 路徑同樣的 RE＋價值查詢各約 17 秒（DuckDB 約 1–2 秒）。
- `outcome_table` 預設：四組×(球數×好球數×球種) 732 格，約 7 秒；加 `zone` 後 7,180 格。
- 連投（四組、SL、pitcher 分群 bootstrap）同一點 k=2：LvL +0.038、LvR +0.039、RvL +0.030、RvR +0.033；同設定的安慰劑平均 +0.056、+0.046、+0.043、+0.043（觀察值低於安慰劑）。把投手加進分層（pooled、SL）：k=2 +0.034（覆蓋率 40%），安慰劑 +0.066。
- 對稱性檢查：RvR vs LvL 的 `ball` 格 65 個，43% |z|>1.96。
- `outcome_model` RvR：見 7.2；各階段耗時：取列 10 秒、邏輯迴歸約 17 秒、HGB 約 6–11 秒（每個特徵組）。

## 附錄 B：測試與預期

- 手算 fixture（`tests/run_fixtures.py`）：Top 1 半局七球的狀態碼／下一狀態／本球得分，`RE_TABLE={0:0.5,1000:0.6,1100:0.55,1:0.9,10:0.3,20:0.1}` 時 `pitch_value` ＝ 0.10、−0.05、0.35、1.60、−0.20、−0.20、−0.10；再見半局兩球為 NULL；game 2（最後半局是上半）與 game 3（下半但主隊輸）不是再見半局。
- 288 狀態 fixture（`tests/state_fixtures.py`）：每狀態兩個半局、得分 0 與 2 → 每個 RE＝1.0、n＝2、分群 SE＝1.0、每球價值 ±1.0。
- 合成世界：`T0` 零衰減（原始曲線 k=1 減 k=4 > 0.15；同一點 |估計| < 0.02）、`T1` 真衰減 0.03（k=2 在 [−0.045, −0.020]、k=3 在 [−0.080, −0.040]）、`T2` 零衰減但球種危險率不同（基本分層偏誤 < −0.03，加 `prior_same_type_count` 後 |k=2| < 0.02）、`T3`、`impossible`（只投滑球的投手 → 全部「沒有數字」）、安慰劑（T0 觀察值在帶內、T1 在帶外）、序列增益（M0 零、M1 真衰減 0.04：下界 > 0.0005）。AST 彙總出的格子與 numpy 手寫預言**逐格相同**。
- 容許誤差是以固定種子在 30 萬打席下實測後設定；`n_pa` 不得低於 30 萬。
