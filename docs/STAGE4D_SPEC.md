# Stage 4D — Output / Visualization 正式規格

Updated: **2026-09-13**  
Branch: `refactor/unify-multifield-and-panel-lifecycle`  
Final status: **PASS / FORMALLY CLOSED**

本文件是 Stage 4D 的正式產品與工程契約，並已依最終實作／人工驗收結果校正。Stage 4D 把 Stage 4A–4C 產生的 formal analysis result 轉成可選取、可視覺化、可保存、可重用、可匯出、可產生報告的結果工作環境；它不新增新的分析語意，也不建立第二套統計邏輯。

**AI → AST 已明確移出 Stage 4D scope。** 目前也沒有 Stage 4E 或其他已定義的下一階段。

---

## 1. 核心原則

1. Visualization 只消費 formal analysis result / typed result contract，不自己重算 Average、Group By、Regression、Bootstrap、Clustering、Percentile、Rolling、Derived Metric 等分析運算。
2. Presentation 設定與 Analysis Payload 分離；單純改圖型、軸、圖例、尺寸、標題等不應迫使分析器重算。
3. 圖、comparison view、report、export 應保留可取得的 source、result section、grain、data revision、row count、sampling 與 provenance。
4. 大型資料不得 silent truncate；任何 presentation sampling 必須明確揭露方法、筆數與 seed（若有）。
5. 各分析頁結果區維持 table-first；完整圖表編輯集中在 Visualization 主頁。
6. **第一版 Visualization 採 **單圖模式****，以取得最大畫布與最低操作複雜度；但資料模型與保存格式**不得設計成「永遠只能有一張圖」**。如果未來真的需要，可以增加 **multi-chart / dashboard** composition layer，而不破壞既有 `VisualizationSpec`。
7. UI 維持中英並列與 XP / Windows 7 方向；publication chart / report 可使用獨立乾淨的輸出樣式。
8. Presentation sampling 不改變 underlying formal result，也不得改變 full-result export population。

---

## 2. 導覽與入口

最終 accepted navigation order：

```text
Data
  Data Management

Analysis
  Basic Analysis
  Sequence Pattern
  Follow-up Event
  Pitch Arsenal
  Pitch Role
  Temporal Comparison
  Individual Threshold
  Level Comparison
  Arsenal Change
  Research Workflow
  Clustering
  Regression
  Bootstrap / Confidence Interval
  Multi-stage Cluster Comparison

Output
  Visualization
  Analysis Library
  Analysis History
```

`Data` 必須在最上方，`Output` 必須在最下方。

### 2.1 Analysis Result

結果頁維持 table-first，提供輕量動作：

```text
[匯出 Export]
[送至視覺化 Open in Visualization]
```

`Open in Visualization` 將目前 analysis/result section 帶入獨立 Visualization 工作頁。

### 2.2 Visualization 主頁

基本流程：

```text
Data Source
→ Result Section
→ Presentation / Preset
→ Field Mapping
→ Data Handling
→ Display
→ Preview
→ Save / Export / Report
```

第一版一次編輯一張圖。

---

## 3. Visualization Sources

Visualization 不把 Statcast 主表當自由繪圖資料庫；source 必須可以回溯到正式分析結果／定義。

支援：

- Current Result / analysis payload；
- recent session result；
- Analysis History；
- Saved Analysis；
- Saved Visualization（Live / Frozen）。

History / Saved Analysis 如果 cached result 不完整或不可用，backend visualization-data path 可以在使用者實際載入／輸出操作中，依 formal Analysis Payload 取得完整結果。這條 path 必須移除 UI-only `result_limit`，不能把 browser retained rows 冒充完整結果。

Rapid source switching 採 **latest request wins**；舊 request 即使晚回也不能覆蓋最後選取的 source。使用者改變 source 時必須先 reset 舊 Result Section，避免上一個 multi-section source 的 section index 洩漏到新 source。

---

## 4. Multi-section Result

多 section 結果先選 section，再決定 compatible presentation。例如 Clustering：

```text
Cluster Summary
Auto Cluster Diagnostics
Cluster Assignments
```

不同 section 可有不同 columns、field metadata、grain 與 row count。

Saved Visualization 必須保存 section selection；restore 時要等 source/section/preset 初始化穩定後再套回保存的 spec，且要有 generation guard 防止舊 restore 覆蓋新操作。

---

## 5. Presentation Metadata Contract

在 `columns + rows + grain` 上提供 presentation metadata，描述結果而不新增分析。

可描述：

- `name`
- bilingual display label
- data type
- unit
- semantic role
- identifier / category / numeric / temporal
- sample-size role
- estimate / lower / upper / SE pairing（如果 result 已提供）
- probability / cluster identifier
- display precision

用途：合法 mapping、合理預選欄位、避免 identifier 當 measure、正確顯示單位、支援 preset compatibility check。

未知欄位在 report 等 presentation 中使用明確 fallback label，例如 `資料欄位 Data Field · raw_name`，不得假裝知道其語意。

---

## 6. First-version Presentation Types

正式支援的 `VisualizationSpec.type`：

- `line`
- `bar`
- `scatter`
- `range`
- `dumbbell`
- `difference`

### Line

Ordered / temporal X，例如 season、date、period、candidate K。

### Bar

Vertical / Horizontal；支援 grouped/stacked behavior when compatible series exists。

### Scatter

X / Y / Series / Label / Point Size / Opacity / reference lines。

### Range

`estimate + lower + upper`，只在 analysis result 已提供 interval 時使用。

### Dumbbell

同一 entity 的兩個可比較值，例如 unit vs baseline、period A vs B。

### Difference

entity × difference，支援 0 reference line 與依差值排序。

---

## 7. Built-in Presets — shipped set

目前 backend `BUILTIN_PRESETS` 正式包含：

- Pitch Movement
- Pitch Location
- Release Point
- Pitch Usage Trend
- Cluster Map
- Auto-K Diagnostics
- Regression Coefficients
- Confidence Interval
- Cross-Level Comparison
- Difference Ranking
- Generic Time Trend
- Category Comparison

### 7.1 Pitch Movement

需要 `pfx_x` / `pfx_z`，可用 pitch_type / cluster / compatible category 作 series，支援 equal axes。

### 7.2 Pitch Location

需要 `plate_x` / `plate_z`；strike zone / plate 由本地 vector primitives 畫出，不依賴外抓圖片。

### 7.3 Release Point

需要 `release_pos_x` / `release_pos_z`。

### 7.4 Pitch Usage Trend

目前使用正式 `line` presentation。Stacked Area 並非目前 first-version `VisualizationSpec.type`，因此不列為已實作功能。

### 7.5 Arsenal / category results

Arsenal / Arsenal Change 若欄位 compatible，可使用 generic Category Comparison / Difference / Bar 等正式 presentation；目前沒有另外宣稱未實作的 dedicated `Arsenal Comparison` 或 `Added/Removed/Retained` renderer。

### 7.6 Baseball graphical asset policy

任何需要真正棒球球體、表面或縫線素材的功能只使用 repo 既有：

```text
research_assets/3d_baseball/upstream_manifest.json
research_assets/3d_baseball/fetch_upstream.py
```

不另外搜尋／抓取替代素材。上游 root license 尚未確認，因此 redistributable package 要內嵌第三方 texture / scene 前仍需 license gate。

---

## 8. Numerical / Statistical Presentation

### Clustering

- Cluster Map；
- Auto-K Diagnostics：candidate K / criterion / score / valid / selected / rejection reason；
- cluster-size/category presentations when source section supports them。

### Regression

- OLS coefficient + CI → range/point presentation；
- Logistic 若 result 沒有 inferential SE/CI，不得假造 interval；
- Observed vs Predicted 只有 source result 提供相關 rows 時才合法。

### Bootstrap / Confidence Interval

- estimate + lower + upper interval；
- A-B difference when provided；
- sample size / resampling unit / confidence metadata 只顯示 result 已存在的資訊。

若 result 無 uncertainty，UI 顯示 unavailable，不自行計算。

---

## 9. Display Controls

First-version UI 可保存／還原的主要 presentation controls 包含：

- X / Y / Series / Label / lower / upper mapping；
- title / subtitle；
- width / height；
- point size / opacity；
- X/Y min/max；
- X/Y reference lines；
- bar orientation；
- stacked toggle；
- legend；
- data labels；
- Show N；
- equal axes。

Visualization 內禁止新增 Average、Group By、Regression、Bootstrap、Cluster、Percentile、Rolling、Derived Metric 等分析運算。

---

## 10. Large-data / Sampling Contract

Current constants：

```text
AUTO_SAMPLE_ROWS = 5,000
MAX_MANUAL_SAMPLE_ROWS = 50,000
MAX_FULL_VISUALIZATION_ROWS = 100,000
MAX_EXPORT_ROWS = 500,000
REPORT_TABLE_ROWS = 80
```

Data Handling：

```text
○ Full Data
○ Automatic Sampling
○ Manual Sampling
```

Manual methods：

- Random
- Every Nth Row
- explicit reproducible seed

畫面必須顯示 source rows / returned rows / sampled state。Frontend paging（200-row page）或 retained-row limit 不得當成完整 visualization/export dataset。

同 seed 的 Random sampling 必須 reproducible。

---

## 11. User Presets

User Preset 保存 presentation 規則，不保存 source data：

- presentation type；
- field mapping；
- display controls；
- dimensions；
- sampling defaults（如有）。

套用前要做 compatibility check；缺欄位不可硬套。

---

## 12. Saved Visualization

每筆 Saved Visualization 保存：

- name / notes；
- source reference / analysis definition；
- result section；
- `VisualizationSpec`；
- save mode；
- timestamps / provenance。

### 12.1 Live

保存 source + spec；重新載入時依 source/result state 準備資料。Presentation restore 必須在 source data 穩定後完成。

### 12.2 Frozen v2

保存完整 result snapshot + spec，之後不隨 Statcast DB 更新改變。

Final implementation：

- snapshot version = `stage4d-frozen-result-v2`；
- full multi-section result 進 content-addressed gzip JSON；
- SHA-256 作 snapshot identity；
- `analysis_state.sqlite3` 只保存 snapshot hash / path / metadata / reference；
- identical snapshot dedup；
- metadata 含 section count / rows / columns / grain / revision / backend；
- delete 依 reference-aware policy 清理 unreferenced snapshot；
- legacy Frozen snapshot 仍可讀取。

---

## 13. Analysis Library / Analysis History

Analysis Library 管 saved analysis definition，不是 Visualization Library。

最終功能：

- Save
- Load
- Delete
- `編輯 Edit`：以同一 XP-style dialog 修改 Name + Notes；欄位自動帶入；更新同一 saved item，不建立副本。

Analysis History 顯示 persistent `#ID`；該 ID 與 Visualization 的 History source selector 使用同一 history record id。

Historical Data / Historical Result 提示不得破壞原本 Load 行為。

---

## 14. Data / Figure Export

### Data formats

- CSV
- JSON
- XLSX
- Parquet

`/api/export` 是 synchronous backend export endpoint：request resolve source → 取得 selected full result section → serialize → 直接回傳 attachment bytes。Current implementation 沒有 async export-job/status API，因此不得在文件中宣稱已有 export job system。

Full export：

- 不使用 DOM table；
- 不使用目前 sampled rows；
- backend 必要時以 formal Analysis Payload 取得 full result；
- 超過 `MAX_EXPORT_ROWS = 500,000` 明確拒絕；
- 不 silent truncate。

Current format metadata behavior：

- JSON：包含 `metadata` + `section`；
- XLSX：Result sheet + Metadata sheet；
- CSV：純 tabular data + header，目前沒有 sidecar metadata；
- Parquet：typed tabular data，目前沒有 sidecar metadata。

Parquet final implementation 使用 temporary CSV → DuckDB bulk ingest → `COPY ... FORMAT PARQUET`，避免 row-by-row `executemany()`；UI 匯出期間顯示 `匯出中 Exporting…`。

### Figure formats

- SVG
- PNG
- Copy Image where browser supports it

Standalone SVG 必須 self-contained 地帶入 gridline / axis / reference / label / title / subtitle / legend styling；PNG / Copy Image 共用同一可獨立渲染的 SVG path。

---

## 15. HTML / PDF Report

`/api/report` 目前是 synchronous backend endpoint，支援：

- HTML
- PDF

Current first-version report 以**一個 selected source/result section + 一張 current Visualization**為單位，採固定正式版面；目前沒有 multi-Saved-Visualization report composer，也沒有 free-form drag/drop designer。

Report 包含可取得的：

- bilingual report headings / fixed labels；
- analysis mode / section；
- source / revision / backend / grain / row count；
- sampling disclosure；
- visualization；
- result table（最多 80 rows）；
- provenance；
- presentation spec；
- generation timestamp。

HTML：sanitized/self-contained chart SVG、responsive fixed-layout result table、long-cell wrapping。

PDF：wide result（例如 >7 columns）使用 landscape A4；cell text wrapping；chart 包含 gridlines、axis ticks、legend、reference lines 等 first-version presentation elements。

Report table 80-row限制只限制 report 版面；完整資料應使用 CSV / JSON / XLSX / Parquet export。

---

## 16. Presentation State Storage

Presentation state 與 Statcast source of truth 分離。

`analysis_state.sqlite3` 延伸保存：

- visualizations；
- visualization presets；
- frozen snapshot reference metadata。

Frozen v2 payload 使用獨立壓縮 snapshot，不把大型 JSON 全塞入 DB。

---

## 17. API / Service Contract — implemented

Current Stage 4D service surface包含：

```text
GET  /api/visualization/sources
POST /api/visualization/data
POST /api/visualization/describe
GET  /api/visualizations
GET  /api/visualizations/{id}
POST /api/visualizations
POST /api/visualizations/{id}
DELETE /api/visualizations/{id}
GET  /api/visualization-presets
POST /api/visualization-presets
DELETE /api/visualization-presets/{id}
GET  /api/visualization/baseball-asset
POST /api/export
POST /api/report
```

Export/report 回傳同步 attachment bytes；frontend 不直接查 SQLite / DuckDB。

---

## 18. Rendering / Lifecycle Contract

Renderer 必須 local/offline 運作，不依賴 CDN，也不要求把 plain HTML/CSS/JS frontend 重寫成其他 framework。

Final browser bundle採 declarative module concatenation，active compatibility modules包含：

- `stage4d-visualization-fixes-v2.js`
- `stage4d-preset-state-reset.js`
- `font-minimum-compat.js`
- `stage4d-layout-containment.js`
- `stage4d-save-lifecycle.js`
- `stage4d-load-stability.js`
- `stage4d-latest-request.js`
- `stage4d-axis-layout.js`
- `stage4d-export-fidelity.js`
- `stage4d-export-progress.js`

舊 `stage4d-saved-restore.js` / old fix module 可以存在於 repo，但不是 active bundle contract。

---

## 19. Single-chart now / future compatibility

Current product：

- Visualization 頁一次只編輯一張圖；
- 一筆 Saved Visualization 一個 `VisualizationSpec`；
- current report 一次使用一張 current Visualization。

Future-compatible architecture：

- `VisualizationSpec` 可獨立序列化；
- source binding 以 explicit source/id 表達；
- Saved Visualization 是獨立 entity；
- 未來若需求成立，可在其上建立 collection/dashboard/composer。

這是相容性保留，不代表目前有下一階段開發承諾。

---

## 20. Out of Scope / Not Implemented as first-version claims

- AI → AST
- 新增分析統計邏輯
- free SQL chart builder
- hidden aggregation / hidden uncertainty calculation
- silent sampling / silent truncation
- current multi-chart editor
- dashboard editor
- multi-visualization report composer
- free-form report designer
- async export job/status service
- CSV/Parquet metadata sidecar bundle
- Area chart as a first-version `VisualizationSpec.type`

這些項目若未來真的要做，需另行定義需求；不得從舊 pre-implementation spec wording 推定已實作。

---

## 21. Final Acceptance Matrix — completed

原始人工驗收 **1–15** 全數完成：

1. Output navigation — PASS。
2. Clustering run/source — PASS。
3. Open in Visualization — PASS。
4. Multi-section switching — PASS。
5. Auto-K Diagnostics — PASS。
6. Generic Scatter — PASS。
7. Pitch Movement — PASS；Pitch Location / Release Point 的 required-field unavailable 情境為 non-blocking data coverage gap。
8. Vertical / Horizontal Bar — PASS；Stacked 在單一 series 測試資料下為 non-blocking coverage gap。
9. Full / Automatic / Manual sampling + seed — PASS。
10. Saved Visualization Live / Frozen — PASS。
11. User Preset — PASS。
12. CSV / JSON / XLSX / Parquet — PASS；18,887-row source 在畫面 sample 50 時仍完整輸出 18,887 rows。
13. SVG / PNG — PASS；standalone style fidelity 已修復並人工比對。
14. HTML / PDF Report — PASS；bilingual、chart fidelity、wide-table layout 人工確認。
15. Analysis Library / Analysis History — PASS。

另外人工驗證：

- rapid source switching latest-request-wins — PASS；
- cross-source Result Section reset — fixed / regression-covered；
- Analysis Library Name/Notes `編輯 Edit` — PASS。

Automated accepted implementation head before documentation refresh：

```text
226 passed, 2 deselected
live-savant-smoke: PASS
```

---

## 22. 已定案決策摘要

- Navigation：Data first；Output last。
- Output group：Visualization + Analysis Library + Analysis History。
- Analysis Result：table-first + Export + Open in Visualization。
- 第一版：single-chart。
- Saved Visualization：Live + Frozen v2。
- Large data：Full + Automatic + Manual；不得 silent sampling。
- Data export：CSV + JSON + XLSX + Parquet；full-result backend path。
- Figure export：SVG + PNG + Copy Image where supported。
- Report：single-source/single-current-visualization HTML + PDF fixed report。
- Baseball graphical assets：只使用 repo `research_assets/3d_baseball/` manifest/fetch path。
- AI → AST：不屬 Stage 4D。
- Stage 4D：**PASS / CLOSED**。
- 目前沒有 Stage 4E。

後續若要改變上述產品契約，先更新本文件；不得讓實作細節或舊 pre-implementation wording 悄悄改寫正式狀態。
