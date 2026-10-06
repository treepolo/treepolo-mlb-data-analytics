# 配球序列研究系統 — P5 研究計畫

**狀態：計畫完成；5.0 工具已開發完成（見附錄 E）；5.1 起的研究步驟尚未開始。** 日期：2026-10-06。接續 P3、P4（已完成，見 [`PITCH_SEQUENCING_DEV_P3_P4.md`](PITCH_SEQUENCING_DEV_P3_P4.md) 附錄 C）。「做什麼、為什麼」以 [`PITCH_SEQUENCING_PLAN.md`](PITCH_SEQUENCING_PLAN.md)（下稱規劃文件）為準；施工慣例沿用 [`PITCH_SEQUENCING_DEV.md`](PITCH_SEQUENCING_DEV.md) 第 0 節（下稱 DEV）。

本文件是**預先登記**：研究問題、要跑的分析、判斷準則、停止條件、交付物，都在**看到 2025、2026 的任何結果之前**寫死。之後想改，必須在紀錄中標明「事後修改」，並把受影響的檢驗標為「檢驗集已非乾淨」（規劃文件第 7 節）。

目錄：0 這份計畫要回答什麼 · 1 先讀：開發環境試跑已經告訴我們的事 · 2 分析方法與判斷準則 · 3 階段與步驟 · 4 分析清單（預先登記）· 5 凍結與檢驗規則 · 6 雲端執行、紀錄與交付 · 7 停止條件與停下來問 · 8 時間與資源估計 · 附錄 A 新工具規格 · 附錄 B 驗證過的參考程式 · 附錄 C 試跑數字 · 附錄 D 設定範本

---

## 0. 這份計畫要回答什麼

規劃文件第 1 節的問題，落成可檢驗的陳述（每個都允許答案是「沒有偵測到」）：

| # | 問題 | 主要產出 |
|---|---|---|
| Q1 | **連投**：連續投同一球種第 k 顆時，揮棒率、揮空率（每次揮棒）、called strike（每次不揮棒）、界外、界內球、全壘打機率、平均價值，相對第 1 顆如何變化？控制「誰在投、誰在打、同一場比賽的狀態、球數」之後還在嗎？ | 連投曲線＋調整後差異（附區間與證據等級） |
| Q2 | **序列**：前一球的球種（以及前兩球）如何影響當前球的結果？ | 前一球→下一球結果熱圖、調整後差異 |
| Q3 | **記憶長度**：序列資訊在控制投手、打者之後，記到前幾球就夠了？ | 序列增益對記憶長度曲線 |
| Q4 | **位置**：各球種落在不同位置時結果機率與價值（不是「瞄準」） | 位置熱圖與表 |
| Q5 | **期望值與尾端風險**：各情境下各球種的平均價值與全壘打機率，差異是否超過雜訊 | 期望值對全壘打機率散點圖與表 |
| Q6 | **揮棒品質分項**：擊球與球棒追蹤指標（揮空的 `miss_distance`、界外的仰角、揮棒速度等）如何隨配球選擇而變 | 各格分項平均與有值比例 |
| Q7 | **樣本外**：以上在 2025（定義相同）與 2026（ABS）是否仍成立 | 檢驗報告 |
| Q8 | 納入 2025、2026 的全期間附加研究與只用 2023–2024 的差異 | 差異表（附區間） |

**不回答**：「改投別的球會怎樣」（規劃文件 3.4：只描述歷史上發生了什麼）；多球往後看（P6）；中職。

---

## 1. 先讀：開發環境試跑已經告訴我們的事

以下全部用 **2023–2024 建模期**資料（沒有碰過 2025、2026），數字見附錄 C。它們**不是結論**，但決定了本計畫的設計：

1. **「分層比較」撐不住控制。** 把「同一投手」加進分層後，能比較的球只剩 40%（只控制投手）、4–8%（再加同球種已投球數）、0%（再加打者）。要控制投手、打者、甚至「同一投手同一場比賽」，必須改用**固定效應迴歸**（附錄 A.2、B.1），它用全部資料。
2. **固定效應迴歸通過已知答案測試**（合成世界 T0–T3，附錄 B.1 表）：零衰減回報零（|估計| < 0.004）、真衰減 −0.03／−0.06 回報 −0.025／−0.067，球種危險率不同的世界（分層法會偏誤 −0.043）回報 +0.001／−0.002。
3. **在真實資料上，連投後的揮空率（每次揮棒）不是下降，而是上升**，而且在加入投手、打者、同一場比賽固定效應、前一球結果、位置與球速控制後**幾乎不變**：滑球第 2 顆 +3 到 +4 個百分點，四縫線速球 +2 到 +3 個百分點。這與一般預期的「連投衰減」相反。可能是真的（重複同一球種打亂打者時機）、也可能是我們還沒控制到的選擇（例如捕手叫球、投手當天狀態以外的因素），**P5 要認真對待並用樣本外檢驗**，不預設答案。
4. **「在打席內打亂球種」的安慰劑不能當虛無假設。** 實測：在球種危險率不同的世界（真值為零）它給出 −0.05 的假陽性。改成「連同結果一起打亂」的版本在該世界給出 ≈0，但在有真效應的世界會削弱效應（T1 的 k=2 削弱約一半；球種危險率不同的 T3 在 k=2 甚至反號）。結論：**安慰劑只當診斷（看方法會不會憑空製造效應），不當判斷準則**；判斷準則是已知答案測試通過的固定效應迴歸＋跨季重複＋樣本外確認（第 2 節）。
5. **提升樹（HistGradientBoosting）的序列增益不穩定**（同一組加減序列特徵，logloss 可差 0.1），邏輯迴歸穩定。P5 以邏輯迴歸為主，提升樹只做穩健性檢查並關閉早停、跑多個種子。
6. **鏡像合併的對稱性不成立**：P5 全程四組分開，不鏡像、不合併（使用者決定）。
7. 最小可偵測效應（試跑 SE）：全體合併約 0.002–0.004；單一左右手組、單一球種約 0.005–0.013（LvL 更大）。比這更小的效應，本研究偵測不到——結果要誠實寫成「能排除大於 X 的效應」。

---

## 2. 分析方法與判斷準則

### 2.1 主要估計量

對球種 T、左右手組 g、比率 R、連投顆數 k（k＝2、3；「4 以上」併為一格只描述）：

**δ(T, g, R, k)** ＝ 調整後的「連投第 k 顆」相對「第 1 顆」的 R 差異（單位：機率的百分點；`mean_pitch_value` 的單位是得分）。

R 的清單（全部各自計算，不合成）：`whiff_per_swing`（**主要**）、`swing_rate`、`called_strike_per_take`、`foul_per_swing`、`in_play_per_swing`、`hr_per_pitch`、`mean_pitch_value`。`foul_tip` 預設不算揮空（設定可改，作為穩健性）。

### 2.2 規格階梯（每個估計都要在階梯上各報一次）

| 規格 | 內容 | 工具 |
|---|---|---|
| S0 | 原始曲線（含存活偏誤） | `streak_curve` 原始曲線 |
| S1 | 同一點比較（打席內第幾球 × 球數） | `streak_curve` |
| S2 | 迴歸：球數×打席內球序虛擬變數、同球種已投球數；不含投手／打者效應 | `streak_regression` |
| **S3（主要）** | S2 ＋ **投手固定效應 ＋ 打者固定效應** | `streak_regression` |
| S4 | S3 但把投手固定效應換成**投手×比賽固定效應**（同一投手同一場比賽內比較；對應規劃文件 3.3 的「當天狀態」隱藏因素） | `streak_regression` |
| S5 | S3 ＋ 前一球結果 ＋ 位置（`zone`）與球速控制（檢視效應是否經由執行品質） | `streak_regression` |

位置與球速在 S3、S4 **不控制**（規劃文件 2.3：位置是被研究的選擇，不是背景）；S5 只用來看機制。

### 2.3 「偵測到效應」的預先判斷準則（針對建模期）

一個 (T, g, R, k) 要被標為「建模期偵測到」，必須**同時**滿足：

1. **S3 的 99% 區間（以投手分群）不含 0。**（99% 是為了多重比較：每個 R 約 4 組 × 9 球種 × 2 個 k ＝ 72 個估計，95% 會憑空出現約 3.6 個。報告同時列出「偵測到的個數」與「純偶然預期個數（約 0.7）」。）
2. **S4 與 S3 同號**，且 S4 的大小 ≥ S3 的一半（不是只在跨比賽的比較下才有）。
3. **2023 與 2024 分開各跑一次，兩季同號**，且每季的點估計 ≥ 合併估計的 1/3。
4. **實務大小**：|δ| ≥ 0.01（1 個百分點；`mean_pitch_value` 為 0.002 分／球）。小於此者標為「統計上可偵測但很小」。
5. **覆蓋**：該格合格球數與投手數足夠（`clusters` ≥ 30，否則標「分群數太少，區間不可靠」，不算偵測）。

未達者標為「**未偵測到**」，並寫出 S3 區間（即能排除多大的效應）。**「未偵測到」是合法且需要被完整報告的結論。**

### 2.4 證據等級（寫在每一則見解前面）

| 等級 | 條件 |
|---|---|
| **強** | 建模期偵測到（2.3）**且** 2025 樣本外確認（S3 在 2025 同號，且與建模期估計的差 |z| < 2 或 2025 區間不含 0） |
| **中** | 建模期偵測到，但 2025 樣本不足以確認（區間含 0 且含建模期估計）；或 2025 確認但 2026 不一致（需註明規則改變） |
| **弱** | 只在部分規格或只有一季成立 |
| **未偵測到** | 2.3 未達；附 S3 區間 |
| **被反駁** | 建模期偵測到，但 2025 估計反號且區間不含建模期估計 |

2026 單獨報告，不改變等級，但在見解中並列；好球／壞球判決類（`called_strike_per_take`、壞球機率）在 2026 受 ABS 影響，**不作為模型失準的證據**（規劃文件 7）。

### 2.5 序列（Q2、Q3）的準則

- **前一球效應**（Q2）：用 `streak_regression` 的 `treatment=prev1_pitch_type`（附錄 A.2），參考組＝前一球與當前同球種；其餘同 2.2、2.3。
- **記憶長度**（Q3）：`outcome_model`（邏輯迴歸、加入投手與打者效應，附錄 A.4）對 memory ＝ 0、1、2、3 各跑一次，**序列增益**（base − full）的曲線。選擇規則：**最小的 m，使得 m→m+1 的邊際增益的 95% 區間上界 < 0.0003**（logloss）。m＝0 表示沒有序列資訊在控制投手與打者後仍有用——這個結論合法。
- 提升樹只當穩健性：`hgb_early_stopping=false`、固定棵數、3 個種子；若三個種子的增益符號不一致，報告「不穩定」。

### 2.6 期望值與尾端風險（Q5）、位置（Q4）、分項（Q6）

這些是**描述**，不做「偵測」判斷：每格報告次數、機率、Wilson 區間、平均價值（以投手分群的 SE）、全壘打率與區間，`low_n` 的格子保留不填補。額外規定：

- 同一情境（左右手組×球數）內，兩個球種的平均價值差若 |差| > 2.58 × √(se₁²＋se₂²) 才寫成「有差異」（兩者由相同投手投出，共變異數被忽略，偏保守）。
- 一律註明「歷史上發生的結果，不是改投的結果」「位置是實際進壘點，不是瞄準點」。
- 分項（`swing_metrics`）只描述，附有值比例；2023-07-14 前球棒追蹤整段缺失（結構性缺失，不是隨機缺失）。

### 2.7 其他統計規定

- 標準誤與區間一律**以投手分群**；`clusters` < 30 標示。
- 左右手四組分開：RvR、RvL、LvR、LvL；不提供「全部」合併結果（規劃文件 2.6）。
- 球種：`FF, SI, FC, SL, ST, CH, CU, FS, KC`（凍結清單）；稀有球種不分析。每組每球種合格球數 < 5,000 → 「資料不足」（不填補）。
- 不使用 2025、2026 的任何結果調整設定（第 5 節）。

---

## 3. 階段與步驟

每個步驟結束都：匯出研究檔、更新 `docs/research/P5_LOG.md`（附錄 D 有格式）、commit、push。容器會被回收，**進度只有 push 上去的才算數**。

| 階段 | 內容 | 在哪裡做 |
|---|---|---|
| **5.0 工具** | 五個小開發任務（附錄 A）：①結果頁圖表 ②`streak_regression` ③`run_compare` ④`outcome_model` 升級 ⑤安慰劑「連同結果打亂」模式。**使用者決定圖表放第一步**；開發完成後才開始 5.1 | 任一環境（有程式碼即可），需要使用者說「開始」 |
| **5.1 環境與基準** | 下載 2023–2026 並建立分析鏡像；記錄資料指紋；`synthetic_check` 全過；`data_profile` 對照 | 雲端 |
| **5.2 描述（建模期）** | A1–A4、A9：得分期望值表、結果表、位置、前一球→下一球、期望值與全壘打 | 雲端 |
| **5.3 連投（建模期）** | A5–A6：規格階梯 S0–S5、分季重複 | 雲端 |
| **5.4 序列與記憶長度（建模期）** | A7–A8 | 雲端 |
| **5.5 解讀與凍結** | 依 2.3、2.4 寫出建模期結論與完整設定清單，**凍結**（第 5 節） | 雲端 |
| **5.6 樣本外檢驗** | F1（2025）、F2（2026）：用凍結設定原樣重跑，兩者分開 | 雲端 |
| **5.7 全期間附加研究** | X1：2023–2026 全部資料的另一個研究，並與建模期結果比較（`run_compare`） | 雲端 |
| **5.8 見解與報告** | `docs/research/P5_REPORT.md`；每個研究專案寫入 `insight_markdown`；研究檔匯出；給 P6 的輸入清單 | 雲端 |

---

## 4. 分析清單（預先登記）

範圍預設 `{"game_years":[2023,2024],"game_types":["R"]}`，`purpose=tuning`。共用設定：`confidence: 0.99`（連投偵測用）／`0.95`（描述表）、`cluster_by: pitcher`、`bunt_policy: exclude_plate_appearance`、`pitch_types`＝凍結清單。範本見附錄 D。研究專案名稱欄位為 `study`。

| ID | 研究專案 | 方法與關鍵設定 | 回答 |
|---|---|---|---|
| A0 | `P5-1 baseline` | `synthetic_check`（預設）；`data_profile`（2023–2026 例行賽）；記錄各球季列數與資料指紋 | 環境正確 |
| A1 | `P5-2 describe` | `run_expectancy` | Q5 的價值基礎 |
| A2 | `P5-2 describe` | `outcome_table`：`situation_fields:["balls","strikes"]`、`choice_fields:["pitch_type"]`、`swing_metrics`＝全部 10 個 | Q5、Q6 |
| A3 | `P5-2 describe` | `outcome_table`：`choice_fields:["pitch_type","zone"]`；另一份 `situation_fields:[]`、`choice_fields:["pitch_type","loc_x_bin","loc_z_bin"]` | Q4 |
| A4 | `P5-2 describe` | `outcome_table`：`situation_fields:[]`、`choice_fields:["prev1_pitch_type","pitch_type"]`（memory 1）；再一份 `choice_fields:["history_key","pitch_type"]`（memory 2，`min_samples` 300） | Q2（描述） |
| A5 | `P5-3 streak` | `streak_curve`：每個 R 各兩份，`hand_view:four_groups`、`kmax:4`、`se_method:cluster_bootstrap`（`cluster_by:pitcher`、200 次）；第一份 `strata_fields:["pitch_index_in_pa","balls","strikes"]`（S1），第二份加 `"prior_same_type_count"`（S1x，覆蓋率較低、用來檢查球種危險率差異造成的偏誤） | Q1：S0、S1 |
| A6 | `P5-3 streak` | `streak_regression`：每個 R、規格 S2–S5 各一份；**合併 2023–2024 一份、2023 與 2024 各一份**（共 3 範圍） | Q1：S2–S5、分季重複 |
| A7 | `P5-4 sequence` | `streak_regression`：`treatment:"prev1_pitch_type"`，R＝`whiff_per_swing`、`swing_rate`、`mean_pitch_value`，規格 S3 | Q2 |
| A8 | `P5-4 sequence` | `outcome_model`：`memory` 0/1/2/3 各一份（邏輯迴歸，`entity_features:["pitcher","batter"]`）；提升樹（`hgb_early_stopping:false`、3 個種子）作穩健性；`split:holdout_years`（訓練 2023、留出 2024） | Q3 |
| A9 | `P5-2 describe` | 圖表：A2 與 A3 結果的期望值對全壘打散點圖、位置熱圖、前一球熱圖 | Q5、Q4、Q2 |
| A10 | `P5-3 streak` | 診斷：`streak_curve` 安慰劑（連同結果打亂模式，10 次）對 S1 | 方法是否憑空製造效應（只診斷） |
| F1 | `P5-6 test 2025` | 凍結後：A1、A2、A5(S1)、A6(S3、S4)、A7、A8 在 2025 重跑，`purpose:final_test`（模型：訓練 2023–2024、留出 2025） | Q7 |
| F2 | `P5-6 test 2026` | 同 F1，只換 2026；**與 F1 分開**，不合併 | Q7 |
| X1 | `P5-7 extra study` | 範圍 2023–2026、`purpose:extra_study`：A1、A2、A6(S3)、A8（`grouped_kfold`）；之後用 `run_compare` 對 A 系列做差異表 | Q8 |

估計個數：A6 約 7 個 R × 4 個規格 × 3 個範圍 ＝ 84 次迴歸執行（各以四組 × 9 球種擬合）；詳見第 8 節。

---

## 5. 凍結與檢驗規則

1. **凍結時點**：5.5 完成時。凍結內容寫成 `docs/research/P5_FROZEN.md`：每個 F 系列分析的**完整設定 JSON**（與建模期的差別只有 `scope` 與 `purpose` 與模型的 `test_years`）、資料指紋、程式碼版本（`git rev-parse HEAD`）。commit 後才能開始 5.6。
2. **F1、F2 的設定必須與凍結檔逐位元組相同**（除上述欄位）。用 `run_compare` 的 `config_diff` 節核對（附錄 A.3）；有其他差異 → 停下來。
3. **每個檢驗分析只跑一次**（重跑只為確認可重現）。看到結果後**不得**回頭改設定再跑。若確實需要（例如發現程式錯誤），必須：①寫在 `P5_LOG.md`、②該分析的所有結果標「檢驗集已非乾淨」、③停下來問使用者。
4. 5.6 的 2025 與 2026 **分開報告**；2026 的落差是「樣本外誤差 ＋ 規則與定義改變（座標、好球帶、ABS）」，無法拆開，不歸因於模型本身。
5. 模型檢驗：訓練 2023–2024、分別對 2025、2026 評估；報告 logloss、與建模期留出集的差、各類別校準（好球／壞球類別在 2026 單獨標註）。
6. 全期間附加研究（X1）沒有乾淨檢驗集，只能內部分組交叉驗證；其「準確度」**不得**與 F1、F2 並列比較（規劃文件 7）。

---

## 6. 雲端執行、紀錄與交付

- **環境**：沿用 DEV 0.2、2.3 的 T2.8 做法（`init`、`backfill --start 2023-03-01 --end <今天> --resume`、`analytics-sync`）；回補約 105 分鐘、資料庫約 3.1 GB＋鏡像 0.8 GB；網路政策每次新 session 要重測；可用硬碟 < 8 GB → 停下來問。**記錄回補完成當下各球季與賽事類型的列數與資料指紋，並關閉自動更新（`auto_update_enabled:false`），整個研究期間不再同步資料**，否則同設定的結果會因資料改變而失效。
- **容器會被回收**：每個步驟結束就 `research export` 匯出該研究專案成研究檔，放 `docs/research/bundles/<study>.treepolo-research.zip` 並 push（預期每個 < 20 MB；超過 → 停下來問）。**原始 Savant 資料不進 repo。**
- **進度紀錄** `docs/research/P5_LOG.md`：每步一列（日期、步驟 ID、研究紀錄 run 的 `run_key` 前 12 碼、耗時、一句話結果、是否偏離計畫）。新 session 從這裡接續；接續時需要重新下載資料，但**不需要重跑已完成的步驟**（結果在研究檔裡，直接 `research import` 即可查看）。
- **見解**：每個研究專案的 `insight_markdown` 以固定格式寫：「結論一句話／證據等級（2.4）／依據（run 編號、規格、數字、區間）／限制」；也可附「建議設定與理由」。
- **最終報告** `docs/research/P5_REPORT.md`：①摘要表（每個 Q 一列：結論、證據等級）②各 Q 詳述（含圖，用 Playwright 截圖存 `docs/research/figures/`）③「未偵測到」清單（含可排除的效應大小）④2025／2026 檢驗結果⑤全期間附加研究差異⑥限制（隱藏因素、固定效應迴歸為線性機率模型、2026 定義改變、最小可偵測效應）⑦**給 P6 的輸入**：是否存在連投與序列效應、效應形狀（k 上限）、選定的記憶長度、可信的價值表、四組各自資料量與信賴程度、建議的模型。
- 全部結論必須帶「只代表歷史資料」。

---

## 7. 停止條件與停下來問

沿用 DEV 0.5。另外：

1. `synthetic_check` 或 `streak_regression` 的已知答案測試任一項失敗 → 停，修好再繼續。
2. 資料列數與 2023–2024 例行賽的列數（1,432,583 球，其中 5,235 球屬再見半局）差超過 1% → 停下來問（可能資料版本不同）。
3. 任何步驟耗時超過第 8 節估計的 5 倍 → 停下來問。
4. 規格階梯出現符號翻轉（S1、S3、S4 不同號）的 (T, g, R, k) 超過全部估計的 20% → 停下來問（可能是方法問題，不是效應）。
5. 發現必須修改凍結設定（第 5 節 3）。
6. 需要新增 Python 套件依賴。
7. 要把任何資料或結果送出容器（除了 push 到指定分支）。

---

## 8. 時間與資源估計（開發環境實測外推；不是保證）

| 項目 | 估計 |
|---|---|
| 回補與鏡像 | 約 105 分鐘＋71 秒 |
| 5.0 工具開發 | 約 1 個工作階段（圖表佔一半） |
| A1–A4（各一份） | 各 < 1 分鐘（DuckDB） |
| A5（含 bootstrap） | 每份約 30–60 秒，共約 14 份（7 個 R × 2） |
| A6 單次 `streak_regression` | S2、S3：秒級；S4（投手×比賽固定效應）：約 40–120 秒／球種／組；全部約 1–2 小時 |
| A8 `outcome_model` | 邏輯迴歸四組約 4 分鐘；加投手與打者稀疏效應預估 2–5 倍 |
| F、X 系列 | 約等於 A 系列重跑 |
| 記憶體 | 逐球列取回 Python 的上限 200 萬列（`max_rows`）；實測 22 萬列迴歸 < 3 GB |

---

## 附錄 A：新工具規格（5.0）

通則：沿用 DEV 0.1、0.3；新方法放 `research/` 並登錄；每個方法有已知答案測試與 `reproduced: true` 測試；**測試必須斷言 `backend == "duckdb"`**；方法模組頂層不得匯入 numpy／scipy／sklearn（見 P3/P4 文件第 3 節 #7）；數值結果用 `sections.make_section` 取整。每個工具一個 commit。

### A.1 結果頁圖表（前端）

位置：`web_static/research-runs-page.js`（及新增 `research-charts.js`，行內 SVG、不引入圖表函式庫；新檔加進 `ci.yml` 的 `node --check`）。在「研究紀錄」頁結果區新增「圖表」分頁：選一個結果節（需取回該節全部列，以 `limit=1000` 分頁迴圈取回，上限 200,000 列，超過顯示提示），選圖型與欄位對應，渲染 SVG。四個預設圖（按鈕）：

1. **連投曲線**（折線）：x＝`k`；y＝`estimate`（或 `mean`）；`lo`、`hi` 畫成帶；系列＝`pitch_type`；分面＝左右手組欄位；可疊加原始曲線。零線以虛線標示。
2. **前一球→下一球熱圖**：列＝`prev1_pitch_type`、欄＝`pitch_type`；顏色＝使用者選的欄位（例如 `whiff_per_swing`、`mean_value`、`hr_rate`）；格內顯示 `n`；`low_n=1` 的格子淡化；分面＝左右手組。
3. **位置熱圖**：x＝`loc_x_bin`、y＝`loc_z_bin`（高處在上）；顏色＝選定欄位；分面＝球種或左右手組。
4. **期望值對全壘打散點圖**：x＝`mean_value`（附 `value_lo`/`value_hi` 橫線）、y＝`hr_rate`（附 `hr_lo`/`hr_hi` 直線）；每點一格；顏色＝球種；`low_n` 的點空心。

通用規定：顏色盲友善且淺／深色背景都可讀（可先載入 `dataviz` skill 取得配色與驗證方法）；座標軸標示欄位名與單位；標題寫明「歷史結果，非改投結果」；缺值（`None`）不畫、不補；可下載 SVG。
測試：`tests/` 下的 `.cjs` Node 測試驗證「資料列→SVG 元素」純函式（座標映射、帶狀路徑、熱圖色階、`low_n` 淡化、空資料）；驗收：用 Playwright（`/opt/pw-browsers/chromium-1194/...`）在真實研究紀錄上各截一張圖並檢視。

### A.2 `streak_regression`（研究方法）

目的：用**固定效應線性機率模型**估計 δ（2.1），控制投手、打者、同一投手同一場比賽。`kind="streak_regression"`、`version=1`、`requires_scope=True`。

設定欄位：`purpose`、`re_scope`（僅 `rate=mean_pitch_value` 用）、`bunt_policy`、`confidence`（預設 0.99）、`filters`；`rate`（同 `streak_curve` 的清單）、`foul_tip_is_whiff`；`pitch_types`、`min_type_pitches`（5000 合格球）；`treatment`（`streak_position`｜`prev1_pitch_type`）；`kmax`（預設 4）；`hand_view`（`four_groups`｜`pooled`；**沒有** `same_opposite`）；`controls`（`str_list`，可含 `count`＝打席內第幾球×球數×好球數虛擬變數、`exposure`＝同球種已投球數（上限 5）、`hand`＝投打手組合（僅 `pooled` 時有意義）、`prev1_description`、`zone`、`release_speed`；預設 `["count","exposure","hand"]`）；`fixed_effects`（`str_list`，可含 `pitcher`、`batter`、`pitcher_game`；預設 `["pitcher","batter"]`）；`cluster_by`（`pitcher`｜`batter`｜`game`，預設 `pitcher`）；`fe_max_iter`(200)、`fe_tol`(1e-9)；`max_rows`(2,000,000，超過報錯不截斷)。

演算法（附錄 B.1 為已驗證的核心）：
1. 以 `build_pitch_table(..., prior_same_type_count=True, memory=1)` 取每球種的逐球列（只取合格球：`rate_terms` 的 eligible＝1），欄位 `pitcher, batter, game_pk, streak_pos, pitch_index_in_pa, balls, strikes, prior_same_type_count, prev1_description, zone, release_speed, p_throws, stand, e, x`；經 `run_checked` 與 lint；依 `(game_pk, at_bat_number, pitch_number)` 排序確保可重現。
2. 每個 (左右手組, 球種) 擬合一次：y＝x；處理變數＝k 虛擬變數（k＝2…kmax，參考＝1）或前一球球種虛擬變數（參考＝與當前同球種；球種少於 `min_samples`（200）者併入 `OTHER`）；加入 `controls` 的虛擬變數；缺值的控制變數（如 `zone` 為 NULL）→ 該列剔除並計數。
3. 用 `fe_ols`（FWL：交替投影去除固定效應；`pitcher_game` ＝ pitcher 與 game_pk 的組合）；CR1 分群穩健標準誤。
4. 輸出節 `coefficients`：左右手組、球種、`term`（`k=2`…或前一球球種）、`estimate`、`se`、`lo`、`hi`（`confidence`）、`n_obs`、`n_informative`（組內有變異的列數）、`clusters`、`fe_groups`（各固定效應的組數）、`converged`、`rows_dropped`、`low_clusters`（`clusters < 30`）；節 `fit_info`：規格全文（controls、fixed_effects）。附 `extras`：`model: "linear_probability"`、單位說明。
5. 小於 `min_type_pitches` 的 (組, 球種) 輸出 `insufficient=1` 的列（不填補）。

已知答案測試（用 `synthetic` 世界；該世界每個 PA 是獨立比賽、`batter` 為潛在打者型 100／101、只有一位投手 → 測試用 `fixed_effects:["batter"]`、`cluster_by:"game"`、`controls:["count","exposure"]`、`pitch_types:["SL"]`、`rate:"whiff_per_swing"`、30 萬打席，固定種子 1）。**已用附錄 B.1 的原型測得**：T0 k2 +0.0018、k3 +0.0004；T1 −0.0251、−0.0672；T2 +0.0012、−0.0021；T3 −0.0247、−0.0814。斷言：T0、T2 |k2|、|k3| < 0.015；T1 k2 ∈ [−0.045, −0.010]、k3 ∈ [−0.095, −0.040]；T3 k2 ∈ [−0.050, −0.005]、k3 ∈ [−0.110, −0.040]。再加：`fe_ols` 對手造資料（精確的固定效應結構）回復係數；分群 SE 與暴力計算一致；`reproduced` 為真；`rate` 與 `fixed_effects` 的非法值報 `ConfigError`；`treatment=prev1_pitch_type` 在合成世界（零衰減）下所有係數 ≈ 0。

### A.3 `run_compare`（研究方法）

`kind="run_compare"`、`requires_scope=False`。設定：`run_key_a`、`run_key_b`（字串，皆須存在於本機研究庫，否則 `ConfigError`；用 `run_key` 而非 id，因為 id 在匯入時會重新編號）、`section`（節標題開頭字串，兩個 run 都要找得到）、`key_columns`（預設取該節 `grain.keys`）、`estimate`（欄名）、`se`（欄名，可空：空則只比較差值，不算 z）、`min_abs_diff`（只列出 |差| 超過此值的列，預設 0）。輸出：節 `compare`（key、`a`、`b`、`diff`、`se_diff`＝√(se_a²＋se_b²)（視為獨立樣本）、`z`）、節 `unmatched`（只在 a 或只在 b 的 key 與個數）、節 `config_diff`（兩個 run 的 config 中值不同的鍵，**排除** `scope`、`purpose`、`train_years`、`test_years`）；`extras`：`share_abs_z_above_critical`、`n_matched`。
測試：手造兩份結果（已知 diff 與 z）、未配對列、`config_diff` 只列出被改的鍵、對自己比較 z＝0。

### A.4 `outcome_model` 升級（版本升為 2）

新增設定：`entity_features`（`str_list`，可含 `pitcher`、`batter`，預設 `[]`；**只對 `logistic` 生效**，其類別為稀疏單熱編碼：訓練列數 < `entity_min_count`（預設 200）的個體併入 `OTHER`；提升樹遇到非空 `entity_features` → `ConfigError`，因為高基數類別會讓提升樹記住個體）、`hgb_early_stopping`（bool，預設 `false`，傳給 sklearn 的 `early_stopping`；原本是 `"auto"`，這是造成不穩定的主因）。`Design` 支援稀疏輸出（`scipy.sparse`）與 `fit_predict` 接受稀疏矩陣。`memory` 允許 **0**（無任何前球特徵：`sequence_*` 清單視為空，`variants` 只剩 `base`，`sequence gain` 節不輸出）。
測試：合成世界「兩位投手」——投手 A 常連投滑球且揮空率較高、沒有真正的連投效應：不加 `entity_features` 時序列增益 > 0（序列特徵洩漏投手身分），加了之後增益 ≈ 0。**門檻須由開發者先實測再寫入測試**（沒有實測就不寫門檻；若加了個體效應增益仍明顯大於 0 → 停下來問）。其餘：`entity_features` 下特徵欄數、`OTHER` 併入、提升樹拒絕、`memory=0`、舊設定仍可執行（新欄位有預設值）。

### A.5 安慰劑「連同結果打亂」模式

`placebo_same_point` 新增參數 `mode`（`labels`｜`pairs`，預設 `pairs`）；`streak_curve` 新增設定 `placebo_mode`（預設 `pairs`，**方法版本升為 2**）。`pairs`：同一打席內把每一球的 (球種, 是否合格, 數值 x) **一起**在位置間打亂，位置相關的分層欄位（球序、球數）留在原位。已驗證程式見附錄 B.2。已知答案（合成世界，20 次打亂，觀察值減安慰劑平均）：T2（零衰減、危險率不同）k=2 ≈ 0（`labels` 模式為 −0.050 假陽性），k=3 在 30 萬打席時殘餘 −0.020（原始偏誤 −0.044，只去掉一半以上）；T1 k=2 −0.015（有削弱）。實作時的斷言：T0 觀察值在帶內；T1 觀察值低於帶；T2 k=2 |差| < 0.01、k=3 差 > −0.03；`labels` 模式在 T2 的差 < −0.03。**安慰劑只當診斷**（見第 1 節 4），k≥3 的解讀要更保守。

---

## 附錄 B：驗證過的參考程式

### B.1 固定效應線性機率迴歸核心（開發環境已驗證）

```python
import numpy as np

def codes(values):
    _, inv = np.unique(np.asarray(values, dtype=object).astype(str), return_inverse=True)
    return inv.reshape(-1)

def demean(a, groups, iters=200, tol=1e-9):
    """Alternating-projection removal of several fixed effects from the columns of a (n or n x p)."""
    a = np.array(a, dtype=float, copy=True)
    if not groups:
        return a
    one_d = a.ndim == 1
    if one_d:
        a = a[:, None]
    gs = [(g, np.bincount(g).astype(float)) for g in groups]
    for _ in range(iters):
        delta = 0.0
        for g, cnt in gs:
            for j in range(a.shape[1]):
                m = np.bincount(g, weights=a[:, j]) / cnt
                a[:, j] -= m[g]
                delta = max(delta, np.abs(m).max())
        if delta < tol or len(gs) == 1:
            break
    return a[:, 0] if one_d else a

def fe_ols(y, X, fe_groups, cluster):
    """FWL: residualize y and X on the fixed effects, OLS, CR1 cluster-robust SE. Returns (beta, se, informative rows, clusters)."""
    yt = demean(y, fe_groups); Xt = demean(X, fe_groups)
    informative = int((np.abs(Xt).sum(axis=1) > 1e-12).sum())
    beta, *_ = np.linalg.lstsq(Xt, yt, rcond=None)
    e = yt - Xt @ beta
    bread = np.linalg.pinv(Xt.T @ Xt)
    cl = codes(cluster); G = int(cl.max()) + 1
    S = np.zeros((G, Xt.shape[1]))
    for j in range(Xt.shape[1]):
        S[:, j] = np.bincount(cl, weights=Xt[:, j] * e, minlength=G)
    V = bread @ (S.T @ S) @ bread * G / (G - 1)
    return beta, np.sqrt(np.diag(V)), informative, G

def dummies(values, drop_first=True):
    c = codes(values); k = int(c.max()) + 1
    M = np.zeros((len(c), k)); M[np.arange(len(c)), c] = 1
    return M[:, 1:] if drop_first else M
```

注意：實作時 `np.sqrt(np.diag(V))` 在退化情形會得到負的對角元（出現過一次 RuntimeWarning）→ 以 `max(·, 0)` 夾住並把該係數標 `se=None`。`demean` 的逐欄迴圈在欄位很多（> 200）時慢，必要時改用向量化（`np.add.at`／稀疏矩陣）；先正確、再最佳化，並以同一組已知答案測試保證結果不變。

**合成世界已知答案測試的原型輸出**（30 萬打席；括號為 SE；控制＝球序×球數×好球數虛擬變數、同球種已投球數、打者固定效應）：

| 世界 | 真值 k=2／3 | S2（無打者效應） | 含打者固定效應 |
|---|---|---|---|
| T0 零衰減 | 0／0 | +0.0027／+0.0000 | +0.0018／+0.0004 |
| T1 真衰減 0.03 | −0.03／−0.06 | −0.0221／−0.0641 | −0.0251（0.0064）／−0.0672（0.0107） |
| T2 零衰減、危險率不同 | 0／0 | −0.0049／−0.0167 | +0.0012／−0.0021 |
| T3 真衰減、危險率不同 | −0.03／−0.06 | −0.0251／−0.0789 | −0.0247／−0.0814 |

### B.2 安慰劑 `pairs` 模式（開發環境已驗證）

```python
def pair_shuffle_estimates(rows, strata, shuffles, kmax=5, seed=0, ptype="SL", min_n=30):
    """rows: pitch-level dicts sorted by (game_pk, at_bat_number, pitch_index_in_pa) with e, x, pitch_type, strata fields."""
    n = len(rows); rng = np.random.default_rng(seed)
    pa = np.array([(r["game_pk"], r["at_bat_number"]) for r in rows])
    new = np.ones(n, bool); new[1:] = np.any(pa[1:] != pa[:-1], axis=1); pid = np.cumsum(new) - 1
    types = np.array([r["pitch_type"] for r in rows]); e = np.array([r["e"] for r in rows])
    x = np.array([r["x"] for r in rows], float)
    cols = [np.array([r[f] for r in rows]) for f in strata]; pos = np.arange(n)
    out = {k: [] for k in range(2, kmax + 1)}
    for _ in range(shuffles):
        order = np.lexsort((rng.random(n), pid))               # shuffle inside each plate appearance
        t, ee, xx = types[order], e[order], x[order]            # type, eligibility and value move TOGETHER
        change = np.ones(n, bool); change[1:] = new[1:] | (t[1:] != t[:-1])
        start = np.maximum.accumulate(np.where(change, pos, 0))
        k = np.minimum(pos - start + 1, kmax)                   # streak position recomputed on the shuffled order
        keep = (t == ptype) & (ee == 1)
        keys = np.column_stack([c[keep] for c in cols] + [k[keep]])
        uk, inv = np.unique(keys, axis=0, return_inverse=True); inv = inv.reshape(-1)
        cnt = np.bincount(inv); sx = np.bincount(inv, weights=xx[keep])
        cells = [{**{f: int(uk[i][j]) for j, f in enumerate(strata)}, "k": int(uk[i][-1]), "pitch_type": ptype,
                  "n": int(cnt[i]), "sx": float(sx[i]), "sxx": float(sx[i])} for i in range(len(uk))]
        _, same = same_point_curve(cells, group_fields=(), strata_fields=strata, kmax=kmax, min_n=min_n)
        for r in same:
            if r["estimate"] is not None:
                out[r["k"]].append(r["estimate"])
    return out
```
（`sxx = sx` 只對 0／1 的 x 成立；連續 x 要另外累加平方和。）

---

## 附錄 C：開發環境試跑數字（2023–2024 例行賽；**探索性，不是 P5 結論**）

**C.1 每次揮棒的揮空率，連投第 k 顆相對第 1 顆（百分點，括號為 SE；球種合併四個左右手組）**

| 球種 | k | S1 同一點 | S2 ＋同球種已投球數 | S3 ＋投手＋打者 | S4 投手×比賽＋打者 | S5 ＋前一球結果＋位置＋球速 |
|---|---|---|---|---|---|---|
| SL | 2 | +3.5 (0.4) | +4.1 (0.5) | +3.6 (0.45) | +3.3 (0.5) | +3.0 (0.4) |
| SL | 3 | +5.0 (0.6) | +6.6 (0.9) | +5.3 (0.8) | +4.0 (0.8) | +4.3 (0.7) |
| FF | 2 | +1.8 (0.2) | +3.2 (0.3) | +2.7 (0.3) | +2.4 (0.3) | +2.6 (0.3) |
| FF | 3 | +1.2 (0.3) | +2.7 (0.4) | +2.0 (0.4) | +1.2 (0.4) | +1.9–2.0 (0.4) |

（另：S1 下 SI k=2 為 −0.4、CH k=2 為 +1.1；分層法加投手分層時 SI k=2 為 −2.0，是目前唯一為負的球種。）S1 的覆蓋率：只控制球序與球數 99%；加同球種已投球數 31–71%；加投手 15–53%；再加兩者 0–8%；再加打者 0%。

**C.2 規模**：2023–2024 例行賽 1,432,583 球（5,235 球屬再見半局，價值為 NULL）；排除短打與非投球後 1,413,281 球。左右手組（模型訓練＋留出列數）：RvR 約 54.3 萬、RvL 約 49.5 萬、LvR 約 27.3 萬、LvL 約 10.2 萬。SL 合格揮棒 104,788、FF 218,835。

**C.3 `outcome_model`（訓練 2023→留出 2024，邏輯迴歸，無投手／打者效應）序列增益（logloss，base − full）**：RvR +0.0006、RvL +0.0007、LvR −0.0002、LvL −0.0017；提升樹：−0.008、+0.018、−0.112、+0.007（不穩定，見第 1 節 5）。

**C.4 其他**：標籤打亂安慰劑在 SL 滑球 k=2 給出 +4.3 到 +6.6 個百分點（大於觀察值的 +3.0 到 +3.9）；同側／異側鏡像的對稱性檢查 43% 的格子 |z| > 1.96。

---

## 附錄 D：設定範本與紀錄格式

**A6（S3，合併建模期）：**

```json
{
  "scope": {"game_years": [2023, 2024], "game_types": ["R"]},
  "purpose": "tuning",
  "rate": "whiff_per_swing",
  "pitch_types": ["FF", "SI", "FC", "SL", "ST", "CH", "CU", "FS", "KC"],
  "treatment": "streak_position",
  "kmax": 4,
  "hand_view": "four_groups",
  "controls": ["count", "exposure"],
  "fixed_effects": ["pitcher", "batter"],
  "cluster_by": "pitcher",
  "confidence": 0.99
}
```

規格對照：S2＝`"fixed_effects": []`；S4＝`["pitcher_game", "batter"]`；S5＝S3 ＋ `controls` 加 `"prev1_description","zone","release_speed"`。分季重複：只改 `scope.game_years` 為 `[2023]`、`[2024]`。檢驗（F1）：只改 `scope.game_years:[2025]` 與 `purpose:"final_test"`。

**A8（記憶長度 m＝2、邏輯迴歸）：**

```json
{
  "scope": {"game_years": [2023, 2024], "game_types": ["R"]},
  "purpose": "tuning", "split": "holdout_years", "train_years": [2023], "test_years": [2024],
  "models": ["logistic"], "variants": ["base", "full"], "memory": 2,
  "entity_features": ["pitcher", "batter"], "bootstrap_reps": 200
}
```

**`P5_LOG.md` 格式**（一列一步）：`日期 | 步驟 ID | 研究專案 | run_key 前 12 碼 | 耗時 | 一句話結果 | 偏離計畫？（無／說明）`。


---

## 附錄 E：5.0 工具完成紀錄（2026-10-06）

全套測試 **443 passed, 2 deselected**（約 8.6 分鐘）。五個工具各一個 commit。與附錄 A 不同或實作時才發現的事：

1. **圖表**（`web_static/research-charts.js`、`research-runs-page.js` 的「圖表」分頁）：純函式建出元素樹（Node 測試 `tests/research-charts.test.cjs`，另有 pytest 包裝），可轉成 DOM 或 SVG 字串下載。已用 Playwright 在真實研究紀錄上截圖檢視四種預設圖。**發現並修正兩個問題**：①SVG 的 CSS 類名 `panel` 與應用程式既有樣式衝突（整個圖不顯示），改為 `rc-` 前綴；②前一球熱圖與散點圖在含稀有球種與小樣本格時無法閱讀，預設隱藏 `low_n` 格並讓熱圖格子加大。圖型：連投曲線（含區間帶、零線、分面）、前一球→下一球熱圖、位置熱圖（高處在上）、期望值對全壘打散點圖（含誤差線）；每個圖都標示「歷史結果，不是改投別的球會怎樣」。
2. **`streak_regression`**（`research/regression_methods.py`、`fixed_effects.py`）：與附錄 B.1 相同的核心，通過合成世界 T0–T3 的已知答案測試（附錄 A.2 的門檻原樣使用）、分群 SE 與暴力計算一致、可重現。真實資料（SL、FF、四組合併、S3）：SL k=2/3/4 ＝ +0.0361／+0.0534／+0.0593、FF ＝ +0.0274／+0.0201／+0.0270，與探索時完全相同；耗時 22 秒。`prior_same_type_count` 一律計算；`pitcher_game` 固定效應的耗時依資料量，需另行量測。
3. **`run_compare`**（`research/compare_methods.py`）：用 `run_key` 找兩個成功的紀錄；`diff = b − a`；`se_diff = √(se_a²＋se_b²)`；輸出配對、未配對、設定差異三節。已用手算值測試（RE 差 0.5、se_diff 1.8028、z 0.2774）。
4. **`outcome_model` 版本 2**：新增 `entity_features`（只能搭配 `logistic`）、`entity_min_count`、`hgb_early_stopping`（預設關）、`memory=0`（自動只剩 `base`）。已知答案（合成「兩位投手」世界，無衰減）：不加個體效應時序列增益 +0.0048 [0.0045, 0.0051]（假的）；加入投手效應後 −0.00002 [−0.00005, 0.00001]。為此 `synthetic.simulate` 新增 `pitcher_mix`（舊種子的結果不變）。
5. **安慰劑 `pairs` 模式**：`placebo_same_point(mode=...)` 預設 `pairs`；`streak_curve` 版本 2 新增 `placebo_mode`。`synthetic_check` 的安慰劑檢查改為：T0 在帶內、T1 在帶外、T2 k=2 差 ≈ 0（`labels` 模式在 T2 為假陽性）。**實測 T2 的 k=3 在 30 萬打席時仍有 −0.020 的殘餘**（原始偏誤 −0.044），所以 k≥3 的安慰劑解讀要更保守；附錄 A.5 已更正。

未做：`streak_regression` 對 `pitcher_game` 的效能最佳化（真實資料 FF 四組合併約需 2 分鐘，可接受）；結果頁圖表沒有欄位值篩選器（用研究紀錄的「條件過濾」在分析端篩選）。
