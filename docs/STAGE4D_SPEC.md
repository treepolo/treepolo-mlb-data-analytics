# Stage 4D — Output / Visualization 正式規格

Date: **2026-09-01**  
Branch: `refactor/unify-multifield-and-panel-lifecycle`

本文件是 Stage 4D 的正式產品與工程規格。Stage 4D 的目標，是把 Stage 4A–4C 已經能正確產生的 analysis result，轉成可選取、可視覺化、可比較、可保存、可重用、可匯出的結果工作環境；Stage 4D 不新增新的分析語意，也不建立第二套統計邏輯。

**AI → AST 已明確移出 Stage 4D scope。**

---

## 1. 核心原則

1. Visualization 只消費正式 analysis result / typed result contract，不自己重算 Average、Group By、Regression、Bootstrap、Clustering、Percentile、Rolling、Derived Metric 等分析運算。
2. Presentation 設定與 Analysis Payload 分離；改圖型、軸、圖例、尺寸、標題等不得迫使分析器重算。
3. 所有圖、comparison view、report、export 都必須保留來源 analysis、result section、grain、data revision、sample size 與其他可用 provenance。
4. 大型資料不得 silent truncate；任何 sampling 必須明確標示方法、筆數與 seed（若有）。
5. 各分析頁結果區維持 table-first，不塞完整圖表編輯 UI。
6. 第一版 Visualization 採 **單圖模式**，以取得最大畫布與最低操作複雜度；但資料模型、API 與保存 schema 不得設計成「永遠只能有一張圖」。未來可以增加 multi-chart / dashboard / report composition，而不需破壞既有單圖 `VisualizationSpec`。
7. 應用 UI 維持目前雙語與 XP / Windows 7 方向；輸出的 publication chart / report 可以採獨立、乾淨的輸出樣式。

---

## 2. 導覽與入口

左側導覽改成明確群組：

```text
Analysis
  Basic Analysis
  ...
  Bootstrap
  Multi-stage Cluster Comparison

Output
  Visualization
  Analysis Library
  Analysis History

Data
  Data Management
```

### 2.1 各分析頁結果區

每個 `Analysis Result` 區保留現有結果表格，新增輕量動作：

```text
[匯出 Export]
[送至視覺化 Open in Visualization]
```

`Open in Visualization` 只負責把目前 analysis/result section 帶入 Visualization；完整圖表設定仍在 Visualization 主頁。

### 2.2 Visualization 主頁

Visualization 是獨立一級工作頁，第一版一次處理一張圖。基本流程：

```text
Data Source
→ Analysis Result
→ Result Section
→ Presentation Type / Preset
→ Field Mapping
→ Display Controls
→ Preview
→ Save / Export
```

頁面主要區域：

- Data Source selector
- Result Section selector
- Visualization / Comparison type selector
- 大型單圖畫布
- Field Mapping
- Presentation Controls
- Sampling / Data Handling
- Source / Provenance panel
- Save Visualization / Save Preset / Export actions

---

## 3. Visualization 可使用的資料來源

Visualization 不直接查 Statcast 主表作為自由繪圖資料庫；它從正式分析結果取得資料。

### 3.1 Recent Results

目前應用 session 中最近成功執行的分析結果。

### 3.2 Analysis History

從既有 Analysis History 選擇。

- 若完整 result 仍可恢復：直接使用。
- 若只剩 analysis payload / metadata：顯示 result unavailable，提供 `重新執行 Re-run`。
- 不因使用者只是打開 Visualization 而偷偷重跑。

### 3.3 Saved Analyses

從 Analysis Library 選已保存分析。

- result 可用時直接載入。
- result 不可用時可由使用者明確選擇重新執行。

### 3.4 Open in Visualization

從任一 Analysis Result 點擊後，直接建立目前 analysis/result section 的 source selection；使用者仍可在 Visualization 頁更換 section 或資料來源。

---

## 4. 多 Section Result

如果 analysis result 含多個 section，Visualization 必須先選 section，再決定可用 presentation。

例如 Clustering：

```text
Cluster Summary
Auto Cluster Diagnostics
Cluster Assignments
```

每個 section 的 column metadata 與 row grain 不同，因此合法圖型也不同。

---

## 5. Presentation Metadata Contract

Stage 4D 在現有 `columns + rows + grain` 之上增加 presentation metadata。它描述結果，不重新分析資料。

每個 output field 至少可描述：

- `name`
- display label
- data type
- unit
- semantic role
- identifier / dimension / measure
- categorical / continuous
- temporal
- percentage / rate
- sample-size field
- estimate field
- interval lower / upper pairing
- standard error pairing（若 analysis result 已提供）
- probability field
- cluster identifier
- default display precision

用途：

- 判斷圖表是否合法；
- 自動帶入合理欄位；
- 避免把 pitcher ID 等 identifier 當普通量測值；
- 正確顯示單位／百分比；
- 把 `estimate + ci_low + ci_high` 識別成 interval；
- 產生 chart/preset compatibility check。

這一層不得推導 analysis result 中不存在的統計量。

---

## 6. 第一版通用 Presentation Types

### 6.1 Line Chart

適合 ordered / temporal X 軸，例如 season、date、period、candidate K。

支援：

- X
- Y
- Series
- point on/off
- reference line
- axis ranges
- labels

### 6.2 Bar Chart

支援：

- ordinary bar
- grouped bar
- stacked bar
- horizontal bar

### 6.3 Scatter Plot

支援：

- X
- Y
- Color / Series
- Label
- Point size
- Opacity
- reference lines

### 6.4 Point / Range Plot

正式支援：

```text
estimate + lower bound + upper bound
```

用於 confidence interval、Bootstrap、Regression coefficient 與其他 analysis result 已提供的 uncertainty。

### 6.5 Dumbbell / Paired Comparison

用於同一 entity 的兩個可比較值，例如 unit vs baseline、selected vs reference、period A vs B。

### 6.6 Difference Plot

呈現 entity × difference，支援依差值排序與 0 reference line。

---

## 7. 棒球專用 Presets / Presentation

這些 preset 只固定 presentation mapping 與棒球視覺元素，不新增分析。

### 7.1 Pitch Movement

需要 `pfx_x`、`pfx_z`。

支援：

- Color = pitch_type / cluster / compatible category
- every-pitch points
- group / cluster center
- equal aspect ratio

### 7.2 Release Point

需要 `release_pos_x`、`release_pos_z`。

可依 pitch_type、cluster、game、period 或其他合法 category 著色。

### 7.3 Pitch Location

需要 `plate_x`、`plate_z`。

程式內以 vector / canvas primitives 畫 strike zone、plate 等幾何元素，不需要外抓圖片；可依 pitch_type、description、events、cluster 等著色。

### 7.4 Pitch Usage Trend

結果已有 period × pitch_type × usage_rate 時，可提供 Line / Stacked Area preset。

### 7.5 Arsenal Comparison

已有 pitch_type × metric 結果時，可提供 grouped bar / dot comparison。

### 7.6 Arsenal Change

結果已有 Added / Removed / Retained 等正式欄位時，提供專用分類呈現。

### 7.7 棒球球體／縫線圖形資源政策

任何 Stage 4D 需要真正棒球球體、表面或縫線紋理的視覺元素，**只使用專案既有的 `research_assets/3d_baseball/` 資源與其固定上游載點，不另外搜尋或抓取其他棒球素材。**

既有機制：

```text
research_assets/3d_baseball/upstream_manifest.json
research_assets/3d_baseball/fetch_upstream.py
```

需要本地資產時由既有 helper 下載固定版本並驗證 byte size / Git blob SHA。

上游目前未確認明確 root license，因此：

- 本地開發／研究可依現有 helper 取得；
- 正式 redistributable package 若要直接內嵌第三方 texture / scene，必須先通過 license / redistribution gate；
- 不因 4D 開發另外換一套網路素材來源。

---

## 8. Numerical / Statistical Presentation

### 8.1 Clustering

#### Cluster Scatter

- 任選兩個 compatible numerical features 作 X/Y。
- Color = cluster。
- 可顯示 cluster center。

#### Auto-K Diagnostics

直接消費：

- candidate_k
- criterion / score
- valid
- selected
- rejection reason

顯示：

- X = K
- Y = selector score / BIC
- Selected K 特別標示
- Rejected K 可檢視 rejection reason

#### Cluster Size

Cluster × sample size bar chart。

### 8.2 Regression

#### Coefficient Plot

Linear OLS 已有 coefficient + CI 時呈現 point/range。

Logistic 若沒有 inferential SE / CI，就不得假造 interval。

#### Observed vs Predicted

只有 result 本身提供 observed/predicted rows 時才合法。

#### Regression Summary

可呈現現有 result 的 R²、RMSE、df、sample size 等 summary metadata。

### 8.3 Bootstrap / Confidence Interval

#### Interval Plot

Estimate + CI low + CI high。

#### A-B Difference

差值中心點 + CI。

必須能顯示 result 已提供的：

- resampling unit
- number of resamples
- confidence level
- sample size

---

## 9. Sample Size / Uncertainty Presentation

所有有 sample size 的 result 可啟用：

```text
☑ 顯示樣本數 Show N
```

所有 result 已有 CI / interval 時可啟用：

```text
☑ 顯示信賴區間 Show Interval
```

若 result 無 CI、SE、p-value、distribution 等資料，UI 必須明確顯示 unavailable，不自行計算或推測。

---

## 10. Comparison Presentation

Comparison View 與普通 chart 並列為 presentation type。

正式支援：

- Period A vs Period B
- Unit vs Baseline
- Selected vs Reference
- Cohort A vs Cohort B
- Cluster / reference comparison

可使用：

- grouped bar
- dumbbell
- difference ranking
- interval comparison

條件是 analysis result 已經明確提供可比較值與 grain。

---

## 11. Presentation Controls

第一版允許以下純顯示操作：

- result row / series display sorting
- show/hide series
- legend position
- axis range
- axis start at zero on/off
- title / subtitle
- data labels
- decimal precision
- percentage formatting
- reference line
- point opacity
- point size
- line width
- chart dimensions
- background
- publication aspect presets（例如 16:9、4:5、1:1）

禁止在 Visualization 內新增分析運算，例如 Average、Group By、Regression、Bootstrap、Cluster、Percentile、Rolling 或 Derived Metric。

---

## 12. Large-data Visualization / Sampling

Stage 4D 同時提供 **Automatic Sampling** 與 **Manual Sampling**；任何 sampling 都必須顯示。

Data Handling：

```text
○ Full Data
○ Automatic Sampling
○ Manual Sampling
```

### 12.1 Full Data

資料量在安全門檻內時可直接完整載入。

### 12.2 Automatic Sampling

系統依圖型與資料量選擇安全顯示筆數；畫面與輸出圖必須永久帶有，例如：

```text
Sampled: 5,000 of 47,728 rows
```

### 12.3 Manual Sampling

第一版支援：

- Random
- Every Nth row
- reproducible random sampling with explicit seed

可設定 sample row count。

### 12.4 Sampling state

Saved Visualization 必須保存：

- sampling mode
- method
- requested sample size
- effective sample size
- seed（若適用）
- total source rows

### 12.5 禁止 silent truncation

目前 UI 的 result paging / retained row limit 不得被當成完整 visualization dataset。

如果完整 result 比前端 retained rows 大，Visualization 必須明確知道：

```text
Total source rows
Loaded rows
Sampled rows
```

需要完整資料時走 backend visualization-data path，不依賴 DOM 或目前 200-row page。

---

## 13. Visualization Presets

### 13.1 Built-in Presets

第一版內建至少：

- Pitch Movement
- Pitch Location
- Release Point
- Pitch Usage Trend
- Generic Time Trend
- Category Comparison
- Cluster Map
- Auto-K Diagnostics
- Regression Coefficients
- Confidence Interval
- Cross-Level Comparison

### 13.2 User Presets

使用者可 `Save as Preset`。

Preset 保存 presentation 規則，不保存資料：

- presentation type
- field mapping rules
- series mapping
- display controls
- axis / legend
- theme
- dimensions
- sampling defaults（若使用者明確保存）

套用 preset 前必須做 compatibility check；缺少必要欄位時不得硬套。

---

## 14. Saved Visualization

Saved Visualization 保存一張完整、可重開的 visualization。

保存：

- name
- source analysis reference / definition
- source mode
- result section
- `VisualizationSpec`
- presentation metadata version
- sampling state
- created / updated timestamps

第一版採單圖模式，因此一筆 Saved Visualization 對應一個 `VisualizationSpec`。

**這不是永久單圖限制。** 未來若做 multi-chart / dashboard，可以新增 `VisualizationCollection` / dashboard entity，把多個既有 `VisualizationSpec` 組在一起；不得把第一版 schema 寫死成全系統只能存在一張圖。

---

## 15. Live / Frozen Saved Visualization

儲存時由使用者選：

```text
○ Live — 連結分析
○ Frozen — 凍結這次結果
```

### 15.1 Live

保存 analysis definition/reference + presentation spec，不把 result 當永久 immutable snapshot。

重新開啟時：

- 若目前 data revision 的 compatible result 已存在，可載入；
- 若來源 data revision 已改變，明確顯示 stale / refresh available；
- 由使用者按 Refresh / Re-run 後才以新資料更新；
- 不 silent recompute。

### 15.2 Frozen

保存當次 result snapshot + presentation spec，之後不隨資料庫更新改變。

為避免大型 snapshot 膨脹 `analysis_state.sqlite3`：

- DB 保存 metadata / hash / path / source information；
- frozen result payload 優先採壓縮檔案 snapshot 保存於 presentation snapshot directory；
- snapshot 必須有 hash / version / row count / columns / grain metadata；
- 刪除 Frozen Visualization 時依 reference policy 清理不再被引用的 snapshot。

---

## 16. Visualization Library

Visualization 主頁內建 Library 區：

- Saved Visualizations
- User Presets
- Built-in Presets

Analysis Library 仍管理分析設定與結果關聯；Visualization Library 管理 presentation。

不另外新增新的左側 `Visualization Library` 頁。

---

## 17. Export

### 17.1 各分析頁 Result Export

正式支援：

- CSV
- JSON
- XLSX
- Parquet

### 17.2 Visualization Export

正式支援：

- PNG
- SVG
- Copy Image（瀏覽器／平台支援時）

### 17.3 Export 必須走 backend full-result path

不能把 DOM table、200-row page、5000 retained rows 當完整 export。

完整資料 export 以 analysis definition / result reference 建立 backend export job，直接寫檔／stream，不需要把全部 rows 先塞回瀏覽器。

若分析 result 實際太大而超過 export safety policy：

- 明確拒絕或要求使用者縮小分析；
- 不 silent truncate。

### 17.4 Export Metadata

JSON / Parquet / XLSX metadata layer 或 sidecar 至少可保留：

- analysis name / mode
- analysis payload
- source result section
- grain
- data revision
- backend
- row count
- export timestamp

CSV 若無法自然內嵌 metadata，使用明確 sidecar metadata file 或 export bundle；不能污染資料列本身。

---

## 18. HTML / PDF Report

Stage 4D 第一版同時支援：

- HTML Report
- PDF Report

Report 採固定正式結構，不做自由拖拉排版器。

內容可包含：

- analysis name
- analysis settings / filters
- source / data revision / backend
- grain
- sample size
- result table（依 report safety / pagination policy）
- chosen Saved Visualization(s)
- statistical metadata
- sampling disclosure
- report generation timestamp

第一版 Visualization 本身是單圖，但 report 資料模型可選擇多筆 Saved Visualization 放進同一 report；這不等於 Visualization 編輯頁已提供 dashboard。

---

## 19. Presentation State Storage

Presentation state 與 Statcast source of truth 分離。

優先延伸 `analysis_state.sqlite3` 保存 presentation metadata / JSON spec，例如：

- `visualizations`
- `visualization_presets`
- `report_definitions`（若需要保存 report 設定）

Frozen result payload 不應把超大型 JSON 全塞進 DB；使用獨立壓縮 snapshot 檔案並由 DB reference。

所有 presentation state 使用 schema / format version，未來可以 migration。

---

## 20. API / Service Contract

Stage 4D 建立獨立 presentation / export service boundary。具體 URL 可以在實作時依現有 routing conventions 微調，但 contract 至少包含：

- list visualization sources
- fetch one source/result section
- fetch full visualization dataset
- create/update/delete Saved Visualization
- create/update/delete user preset
- resolve Live visualization state
- create backend export job
- read export job status / artifact
- create HTML/PDF report

Visualization frontend 不直接查 SQLite / DuckDB。

---

## 21. Rendering Engine

產品 contract 不綁死特定第三方 chart library；實作引擎必須滿足：

- local/offline 可運作，不依賴 CDN；
- line / bar / scatter / range / area / annotation 能力；
- 足夠的大量 scatter 效能；
- PNG / SVG export 可驗證；
- data zoom / tooltip / legend / axis controls 可實作；
- 不迫使整個 plain HTML/CSS/JS frontend 重寫成其他 framework。

Rendering engine 是可替換 implementation detail；`VisualizationSpec` 才是產品契約。

---

## 22. 第一版 Single-chart 與未來 Multi-chart 相容性

Stage 4D 第一版：

- Visualization 頁一次只編輯一張圖；
- 一筆 Saved Visualization 一張圖；
- 畫布與設定面板為單圖最佳化。

未來允許：

- multi-chart workspace
- dashboard
- linked brushing / coordinated views
- report composer
- dashboard-level filter controls

為保留這條路，第一版必須：

1. 把 `VisualizationSpec` 設計成可獨立序列化 entity；
2. source binding 不使用單一全域 singleton；
3. backend API 用 visualization ID / source ID，不假設全系統永遠只有 current chart；
4. CSS / DOM identifier 不把唯一圖表寫死成無法複用的資料模型；
5. report 可以引用多個 Saved Visualization，而不需要複製 chart logic。

---

## 23. Stage 4D 第一版功能範圍

Stage 4D 正式分成五個產品子系統：

### 4D-1 Visualization Workspace

- Output 導覽群組
- Visualization 獨立主頁
- Recent / History / Saved Analysis source selection
- Result Section selection
- Presentation Metadata
- 通用圖型
- 棒球 presets
- single-chart editor
- source provenance
- large-data data handling

### 4D-2 Statistical & Comparison Presentation

- sample size
- CI / interval
- Bootstrap
- Regression coefficient
- Auto-K diagnostics
- Cluster views
- A/B
- Unit vs Baseline
- Selected vs Reference
- Cohort comparison

### 4D-3 Presets & Visualization Library

- Built-in Presets
- User Presets
- Saved Visualizations
- Live / Frozen
- compatibility validation

### 4D-4 Data / Figure Export

- CSV
- JSON
- XLSX
- Parquet
- PNG
- SVG
- Copy Image where supported

### 4D-5 Report Output

- HTML
- PDF
- fixed formal report contract

---

## 24. 明確 Out of Scope

Stage 4D 第一版不包含：

- AI → AST
- 新增分析統計邏輯
- 自由 SQL chart builder
- hidden aggregation
- hidden uncertainty calculation
- silent sampling / silent truncation
- free-form dashboard editor
- multi-chart Visualization editor 第一版
- 自由拖拉 report designer

其中 multi-chart / dashboard **只是第一版不做，不是永久禁止。**

---

## 25. Acceptance Matrix

Stage 4D 完成前至少驗證：

### Source / Navigation

- Visualization 出現在左側 Output group。
- Analysis Library / History 移入 Output group 後原功能不退化。
- 任一分析結果可 `Open in Visualization`。
- Recent Result 可用。
- History 有 result 時可用。
- History 無 result 時不 silent rerun，能明確 Re-run。
- Saved Analysis 同理。
- Multi-section result 可正確選 section。

### Generic Visualization

- grouped analysis → Line。
- grouped analysis → Bar。
- numerical X/Y → Scatter。
- estimate + lower + upper → Point/Range。
- paired values → Dumbbell。
- difference values → Difference Plot。
- incompatible field mapping 被阻擋並說明原因。

### Baseball Presets

- pitch rows → Pitch Movement。
- pitch rows → Pitch Location。
- pitch rows → Release Point。
- temporal usage result → Usage Trend。
- Arsenal result → Arsenal Comparison。
- Arsenal Change result → Added/Removed/Retained presentation。
- 若需要球體／縫線素材，只走 `research_assets/3d_baseball/` 既有 manifest/fetch helper，不另抓素材。

### Numerical / Statistical

- Clustering → Cluster Map。
- CAP-04 → Auto-K Diagnostics，selected K 正確標示。
- Regression OLS → Coefficient Plot + existing CI。
- Logistic 無 inferential CI 時不顯示假 CI。
- Bootstrap → interval plot。
- Sample N 可顯示。
- analysis result 無 uncertainty 時顯示 unavailable。

### Sampling / Large Data

- Full Data 在安全範圍內可完整顯示。
- Automatic Sampling 明確標示 sampled/total rows。
- Manual Random sampling 可設定 sample size。
- Every Nth row 可用。
- reproducible random sampling 同 seed 產生同結果。
- Saved Visualization reload 後 sampling state 不變。
- 不會把 200-row page 或 5000 retained rows 冒充完整資料。

### Save / Library

- Save Preset → compatible result 可重用。
- incompatible preset 套用被阻擋。
- Saved Visualization → reload 完整恢復 presentation。
- Live source revision changed → 顯示 stale/refresh，不 silent recompute。
- Frozen → DB 更新後圖表仍維持原 snapshot。
- Frozen snapshot hash / row count 可驗證。

### Export

- Full CSV export 不受 UI paging 影響。
- Full JSON export 不受 UI paging 影響。
- XLSX export 正確。
- Parquet export 正確。
- PNG export 正確。
- SVG export 正確。
- sampling visualization export 保留 sampling disclosure。
- 大型 export 不 silent truncate。

### Reports

- HTML Report 包含 analysis metadata、result、visualization、sample info。
- PDF Report 與 HTML 的核心數值一致。
- Report 可以引用多筆 Saved Visualization，即使 Visualization editor 第一版仍是單圖模式。

### Architectural Regression

- Visualization 不新增分析計算路徑。
- chart/presentation 改設定不造成不必要 analysis rerun。
- Statcast source-of-truth DB 不被 presentation state 污染。
- Stage 4A–4C 全部既有 tests 維持 PASS。
- live Savant smoke 維持 PASS。

---

## 26. 建議實作順序

1. Presentation Metadata + `VisualizationSpec` contract。
2. Output 導覽與 Visualization source selector。
3. single-chart generic renderer + field mapping + provenance。
4. backend full visualization dataset + sampling。
5. baseball presets + statistical / comparison presentation。
6. Saved Visualization / Presets / Live-Frozen state。
7. CSV / JSON / XLSX / Parquet export。
8. PNG / SVG export。
9. HTML / PDF report。
10. 完整 acceptance matrix + large-result regression。

---

## 27. 已定案決策摘要

- Visualization：左側獨立主頁。
- 導覽：Output group = Visualization + Analysis Library + Analysis History。
- 各分析結果區：保留表格；新增 Export 與 Open in Visualization，不直接塞完整圖表 UI。
- 第一版工作模式：Single-chart。
- 未來：保留 multi-chart / dashboard 擴充能力，不設永久單圖限制。
- Saved Visualization：Live + Frozen 都支援。
- Large data：Automatic Sampling + Manual Sampling 都提供；不得 silent sampling。
- Data export：CSV + JSON + XLSX + Parquet。
- Figure export：PNG + SVG。
- Report：HTML + PDF。
- Baseball graphical assets：需要球體／縫線時只使用 repo 已保存的 `research_assets/3d_baseball/` manifest / fetch path，不另外抓素材。
- AI → AST：移出 Stage 4D。

這些決策在後續實作中若要改變，必須先更新本文件；不得讓實作細節悄悄改寫產品契約。
