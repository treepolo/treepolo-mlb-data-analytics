# treepolo MLB Data Analytics — 架構、驗收題目與未來規劃

本文件是本專案的正式長期規劃來源。重要產品方向、架構邊界、驗收題與未來工作不得只存在聊天紀錄中。

最後重大更新：2026-08-26，Stage 4A–4C 第一版完成實作並進入整合驗收；Stage 4D 輸出／圖表仍刻意分開。

---

## 1. 產品目標

`treepolo MLB Data Analytics` 是一套以 Baseball Savant / Statcast 逐球資料為基礎的 MLB 分析工作站。

核心不是固定報表，而是讓使用者可以把完整研究問題組合出來：

- 指定投打慣用手、球種、位置、球數、結果與任意 Statcast 欄位條件。
- 在同一打席或跨比賽時間軸描述有順序的條件。
- 依投手、比賽、時間區間、球種、武器庫、cohort 等層級分析。
- 選取「第二常用球種」「排除 FF 後表現最好球種」等相對角色。
- 建立條件計數、比例、衍生欄位、rolling / lag / lead / consecutive-N 等多階段工作流。
- 比較不同樣本、不同時間、不同資料層級的統計結果。
- 對 movement / velocity / release / spin 等特徵做自動分群。
- 做線性／二元迴歸與明確指定重抽樣單位的 Bootstrap / confidence interval。
- 保存分析設定、回看分析歷史，對完全相同且資料版本未變的分析直接使用快取。

概念主線：

```text
逐球資料
  ↓
條件／篩選
  ↓
分組／球序／角色選擇／跨層級／多階段 workflow
  ↓
關聯分析結果
  ↓
必要時 Numerical Executor
  ↓
分群／迴歸／Bootstrap
  ↓
排序／表格／保存／快取／未來圖表與匯出
```

高度細分的比例或統計必須讓使用者看得到樣本數；不確定性分析必須說清楚 sampling / resampling unit。

---

## 2. 系統架構

核心仍是 **Typed Analysis AST + Grain-aware execution**，不是把所有邏輯塞進 dataframe。

```text
Baseball Savant / Statcast
          │
          ▼
Raw Archive (.csv.gz)
          │
          ▼
SQLite 正規化主資料庫（source of truth）
          │
          ├───────────────┐
          │               ▼
          │       DuckDB columnar analytical mirror
          │               │
          ▼               ▼
前端分析建構器 → Typed Analysis AST / Workflow Plan
                      │
                      ▼
               Grain Validation
                      │
                      ▼
               Execution Planner
                      │
          ┌───────────┼──────────────┐
          ▼           ▼              ▼
     DuckDB SQL    SQLite SQL     Numerical Executor
      primary       fallback      clustering / regression /
                                  bootstrap / continuation
          │           │              │
          └───────────┴──────────────┘
                      ▼
                 Typed Result
                      │
           ┌──────────┴──────────┐
           ▼                     ▼
   Result Cache / History    Web UI / future Output
```

### 2.1 資料層

資料來源為 Baseball Savant Statcast CSV。

Raw layer：

- 每次成功取得的 Savant CSV 原封保存。
- gzip 壓縮於 `data/raw/年/月/`。
- 保存 SHA-256、抓取時間、日期範圍等 manifest。
- 相同日期範圍且內容完全相同的快照去重。

SQLite 主資料層：

- `data/statcast.sqlite3`。
- SQLite 是同步、修訂、完整性與 rebuild 的 **source of truth**。
- 保留 Savant 回傳的全部合法欄位；schema 可隨上游演進。
- 舊年份不存在的欄位允許 NULL。
- 自然鍵 `game_pk + at_bat_number + pitch_number`；缺鍵資料使用 deterministic fallback ID 並在 integrity report 揭露。
- 同一球重新抓取採 idempotent upsert，Savant 事後修訂可更新舊值。

DuckDB 分析層：

- `data/statcast.duckdb`。
- 是 SQLite 的持久化 columnar analytical mirror，不取代 SQLite 真本角色。
- 第一次需要時建立；之後依 `data_revision` / `_ingested_at` 增量刷新。
- UI 啟動不得為「尚不存在的 mirror」偷偷開始數分鐘全量重建；初次建立附著於明確分析並顯示進度。
- mirror 已存在時可在背景 best-effort refresh。
- DuckDB 失敗時關聯分析可 fallback SQLite；分析加速層故障不得讓成功的資料匯入失敗。

分析狀態層：

- `data/analysis_state.sqlite3`。
- 與 Statcast source of truth 分離，只保存 result cache、analysis history、saved analyses。
- cache key 至少包含 canonical analysis payload、Statcast `data_revision`、requested backend 與 cache format version。
- 資料 revision 改變時舊 cache 不會被新分析命中。
- state store 使用 WAL 與 thread-safe access；不能讓分析歷史／快取寫入污染 Statcast 主資料庫。

### 2.2 同步與資料維護

已建立：

- 2015 起歷史回補。
- 預設每 5 個日曆日一個下載區段。
- Resume、Retry Failed、Incremental Update、recent correction window。
- Auto Update 與 scheduler。
- Rebuild from raw archive。
- Integrity verification。
- persistent fast Data Status；普通啟動不掃描整張逐球表。
- backfill live progress。
- SQLite explicit optimize command 具有終端機 stage/index/elapsed 顯示；SQLite 無可信單一 CREATE INDEX 百分比時不偽造。

### 2.3 Grain

每個關聯或數值中間結果必須知道目前資料代表的分析層級。

典型 grain：Pitch、Plate Appearance、Game、Pitcher、Pitcher×Period、Pitcher×PitchType×Period、Arsenal、Cohort、Sequence/Pattern、Cluster。

採可組合 grain keys，不把所有層級硬編碼成封閉 enum。

### 2.4 Typed Analysis AST

關聯核心已支援：

- Source
- Filter
- Aggregate
- Project
- Sort
- Limit
- Set Operation
- Rank
- Window
- explicit row Window Frame
- Join
- Collect Set
- Event Pattern
- Follow Event
- Column / Literal / Binary / Boolean / Case / NULL / IN 等 expression

AST 可序列化 JSON；WindowFrame 也必須 round-trip。

### 2.5 關聯執行器

1. **DuckDB analytical executor**：大型分析預設路徑。
2. **SQLite executor**：fallback 與 correctness / benchmark 對照。

同一 AST 在 DuckDB / SQLite 應維持相同語意；例如使用率除法必須顯式轉 real，不能因 SQLite integer division 得到不同答案。

### 2.6 Numerical Executor

Stage 4C 第一版已建立正式 Numerical Executor，而不是直接暴露 dataframe：

- `NumericalTable`：明確 columns + rows + grain。
- `NumericalSection`：明確 output columns + rows + grain。
- clustering input/output 保留 grain；cluster assignment 可作 typed continuation。
- 完整 assignment 與「UI 顯示幾列」分離，不能因畫面 limit 破壞後續計算。
- Numerical input 有 explicit row safety guard；超過門檻時拒絕，不偷偷抽樣或截斷。

### 2.7 SQLite 快速路徑

已加入：

- `game_year` index。
- `(game_pk, at_bat_number, pitch_number)`。
- `(game_year, pitch_type)`。
- `(pitcher, game_year, pitch_type)`。
- `(game_year, p_throws, stand)`。
- `ANALYZE` / `PRAGMA optimize`。
- `treepolo-mlb optimize`。

不為所有 Statcast 欄位排列建立索引；一般 OLAP 交給 DuckDB。

### 2.8 Baseball Semantic / Workflow layer

Semantic Registry 維持薄便利層，不封閉底層欄位。

Stage 4B 的 typed workflow 可串接：

- Aggregate / conditional aggregate
- Derived arithmetic
- Filter
- Rolling window
- Lag / Lead
- Consecutive-N rising/falling
- First / Last / Nth
- Within-group Rank
- Project / Sort
- Arsenal signature annotation
- Relative pitch selector（含 exclude / rank / tie / usage or field metric）

這一層的目的，是把原本散落在不同頁面的 primitive 組成完整研究流程，而不是讓使用者手動搬結果。

### 2.9 前端共用能力

既有共用基礎：

- 8 個多選欄位共用 checklist renderer。
- 原 9 個分析頁共用 Result Ordering。
- 所有分析共用 Job / Progress。
- 中英永久並列。
- XP / Windows 7 桌面應用視覺方向。

Stage 4 新增：

- Research Workflow。
- Clustering。
- Regression。
- Bootstrap / Confidence Interval。
- Multi-stage Cluster Comparison。
- Analysis Library：保存／載入／刪除分析。
- Analysis History。
- Result Cache 狀態顯示。
- clustering `Partition By`：可為每位投手／個體各自建模，不必把不同個體混在同一 cluster model。

Stage 4D 前，結果仍以 table-first 為主，不建立第二套圖表統計邏輯。

---

## 3. 十個架構壓力測試需求

十題同時扮演長期架構題與 acceptance suite。核心能力通過不代表每題都只能有一個固定頁面；重點是能從正式分析介面／workflow 得到正確結果。

### 1. 三顆 Sweeper 的兩個極端球序

同一打席恰好三顆 Sweeper，最後一球也是 Sweeper；比較三顆完全連續 vs 完全不相鄰，部分相鄰排除，分析第三顆。

需要：same-PA partition、pitch order、exact count、last-event、Nth、adjacency。

**狀態：已覆蓋；EventPattern acceptance test 保留。**

### 2. 武器庫組成 + FF 相對角色

使用率門檻決定武器庫；相同 pitch-type set 自動分組；再判定 FF 是否最高使用率並和相對角色比較。

需要：usage、set signature、dynamic grouping、role selector、ties。

**狀態：已覆蓋；Stage 4 workflow 又加入 Arsenal Signature / Relative Pitch Selector 可組合能力。**

### 3. 跨比賽時間序列

若某投手某球種使用率連續三場上升，檢查第四場指標是否變化。

需要：pitcher-game aggregation、conditional count / ratio、time order、consecutive-N、lead。

**狀態：Stage 4B 已完整覆蓋。Synthetic acceptance 明確驗證 1/4 → 2/4 → 3/4 後取得第四場值。**

### 4. 動態參考球種

每位投手找最高使用率非 FF，再與 FF 比較球速／whiff／xwOBA 等。

**狀態：已覆蓋；Relative Pitch Selector 可直接組合。**

### 5. 巢狀分組 + 群內百分位

先依 arsenal 分組，再於組內按 FF 使用率 percentile 分 cohort，最後比較另一球種。

**狀態：empirical percentile、arsenal signature、workflow aggregation / rank 已有。**

### 6. 變動間距條件球序

Sweeper 後最多 3 球內找第一顆再次出現的 Sweeper；依中間是否出現 FF 分組。

**狀態：FollowEvent 已覆蓋。**

### 7. 跨 grain 比較

投手整季 FF 平均 → 找單場低於整季門檻 → 再分析該場更細層級。

**狀態：typed cross-grain aggregate/join 已覆蓋；特定「第三輪打線」仍屬可追加的棒球語意 helper，不是架構缺口。**

### 8. Arsenal Set Difference

比較兩期間同投手 arsenal，找新進／移除球種並分析後續變化。

**狀態：Set Difference + Arsenal Change 已覆蓋。**

### 9. 每位投手自己的 percentile threshold

高球速 FF 定義成投手自己的 FF 第 80 百分位以上，再比較樣本／結果。

**狀態：empirical percentile + Individual Threshold 已覆蓋。**

### 10. 多階段選擇器 + 自動分群

先在某 arsenal 組找整體表現最佳非 FF；再對每位投手該球種 movement / velocity / release / spin 特徵分群；每位投手挑表現最佳 cluster，再與 FF 比較。

需要：arsenal signature → group-level relative selector → per-entity multivariate clustering → best-cluster selector → reference comparison。

**狀態：Stage 4C 第一版已完整產品化與 automated acceptance。**

目前正式路徑：

```text
Arsenal Signature
→ arsenal group 內 Relative Pitch Selector（可排除 FF）
→ selected pitch rows
→ Partition By pitcher clustering
→ 每位 pitcher best cluster
→ FF / reference pitch comparison
```

K-means 與 Gaussian Mixture 已支援；DBSCAN / HDBSCAN 仍屬候選擴充，不是第一版完成條件。

---

## 4. 開發階段與目前狀態

### 資料基礎 — 已完成第一版

Savant fetch、raw archive、SQLite schema evolution、upsert、backfill/resume/retry、incremental update、scheduler、rebuild、integrity、fast status、live E2E、progress 均已建立。

長期仍需實際季中運行驗證 Savant revisions、scheduler 與 raw archive 成長。

### 第一階段：分析核心骨架 — 已完成

Grain model、Typed AST、filter/aggregate/project/sort/limit/set、ranking、semantic registry、SQLite compiler/executor、planner boundary、serialization。

### 第二階段：高階關聯分析 — 已完成

Window / lag / lead / rank / percentile、cross-grain Join、CollectSet / arsenal signature、EventPattern、FollowEvent、pitch usage、role ranking、tie handling、stress tests。

### 第三階段：正式使用介面 — 第一版完成，持續 UX 改善

九種原始分析頁 + Data Management、雙語 XP/7 UI、table result、backfill progress。

2026-08-25 實測後完成：

- 8 multiselect checklist 統一。
- 9 page Result Ordering 統一。
- shared Analysis Job / Progress。
- Median、Population SD、Sample SD。
- computed metric sorting。
- Basic 非 Count metric 必須指定 field；`Average + None` 前後端均阻擋並顯示明確雙語錯誤。

### 第四階段 A：效能、快取與分析工作區 — 第一版完成

已完成：

- persistent result cache。
- canonical payload + data revision + backend + cache-format key。
- Analysis History。
- Save / Load / Delete Analysis。
- cached stored result restoration。
- state DB 與 Statcast DB 分離。
- 既有 DuckDB relational performance 路徑完成實機 benchmark。

### 第四階段 B：完整關聯 workflow — 第一版完成

已完成：

- explicit rolling frame。
- conditional metrics。
- derived arithmetic。
- consecutive-N trend。
- generalized lag / lead。
- first / last / nth。
- rank / filter / project / sort composition。
- arsenal signature stage。
- relative pitch selector stage。
- SQLite / DuckDB ratio semantics parity。
- Research Workflow UI。

### 第四階段 C：Numerical Executor — 第一版完成

#### Numerical contract

- typed NumericalTable / NumericalSection。
- grain-preserving clustering continuation。
- numerical input safety guard。
- deterministic seed parameters。

#### Clustering

- K-means。
- Gaussian Mixture。
- optional feature standardization。
- global or `Partition By` per entity clustering。
- cluster sample size / center / mean / SD summary。
- GMM assignment probability。
- full internal assignment retained; UI rows may be separately limited。
- Multi-stage Cluster Comparison 完成壓力測試 #10。

#### Regression

- Linear OLS：coefficients、SE、t statistic、p value、CI、R²、RMSE、df。
- Binary Logistic：coefficients、accuracy、log loss。
- predictor standardization option。
- synthetic known-answer tests：`y = 2 + 3x` 必須恢復 intercept 2 / slope 3 / R² 1。

第一版 Logistic 係數 inferential SE / p / CI 尚未提供；欄位明確為 NULL，不冒充已計算。

#### Bootstrap

- mean / median / proportion。
- optional A-B difference。
- explicit resampling unit 為強制條件。
- percentile confidence interval。
- deterministic seed。
- 若 A/B 各 resampling unit 互斥，採 stratified unit resampling 保留兩組樣本結構。
- mean / proportion 使用 unit-level sufficient summaries 加速。
- row-wise median / mixed-unit workload 過大時明確拒絕，避免無上限長時間運算。

### 第四階段 D：Output / Visualization — 尚未開始，本輪刻意不做

下一輪候選：

- 正式圖表／視覺化。
- 匯出。
- sample-size / uncertainty visualization。
- richer comparison presentation。
- preset / library UX 深化。
- 視需要 AI → AST。

圖表不得另寫一套統計邏輯；只消費正式 analysis result。

---

## 5. 大型資料效能實測

2026-08-26 使用完整本機資料庫：

- pitch rows：**9,192,548**。
- benchmark：2026 → Group By `pitch_type` → Count + Average `release_speed` → computed metric sort。

第一次：

- SQLite：2.3596 / 2.3296 / 2.3451 s，median **2.3451 s**。
- DuckDB：0.07023 / 0.06959 / 0.06950 s，median **0.06959 s**。
- 初次 DuckDB mirror prepare：**245.532 s**，`mirror_rebuilt = true`。

第二次：

- SQLite median **2.3146 s**。
- DuckDB median **0.07560 s**。
- mirror prepare/check：**0.03931 s**，`mirror_rebuilt = false`。

代表性 warmed DuckDB query 約比 SQLite 快 **30× 以上**；而初次 mirror build 是一次性成本，不混入 query timing。

UI 實測「逐季 FF 聯盟平均球速」實際顯示 DuckDB backend，約 1 秒完成整個 UI request/render 路徑。

結論：

- 原本接近 10 分鐘的 SQLite 路徑已不再是正常基準。
- 大型互動分析以 DuckDB 為 primary。
- SQLite 保留 fallback / correctness / 小型操作合理效能。
- repeated-result cache 已進一步消除「資料沒變、分析完全相同卻重算」的浪費。
- 若未來某正式分析仍達分鐘級，視為需要 profiling 的效能問題，不以資料量直接合理化。

---

## 6. 仍需長期維護／未來待辦

### 資料與可靠性

- 持續維護 2015→現在 full dataset。
- 記錄 SQLite、DuckDB、raw archive、analysis_state 實際磁碟容量與成長。
- 季中長期 Auto Update 驗證。
- 真實 Savant 歷史 revision → SQLite update → DuckDB incremental refresh 驗證。
- raw snapshot retention / compaction 僅在量測後設計。

### 分析正確性

- 細分統計持續顯示 sample size。
- uncertainty 明確定義計算單位。
- ties、NULL、低樣本、歷史欄位缺失政策保持一致。
- DuckDB / SQLite AST parity 持續回歸。
- clustering 的 low-sample partition 要明確報錯，不偷偷降 k。
- regression / bootstrap / clustering 持續增加由真實棒球問題導出的 known-answer / synthetic acceptance tests。

### 前端／UX

- 依真實使用修正難懂操作，不為了「看起來功能多」堆設定。
- 中英永久並列、XP/7 視覺方向保持。
- 共用互動維護單一元件／單一 contract。
- Research Workflow 與 Numerical pages 持續以完整研究問題為導向，不把 Join / SQL 等實作細節直接丟給使用者。
- Stage 4D 再處理圖表與輸出。

### 效能

- canonical benchmark 永久保留。
- mirror build time 與 query time 分開報告。
- cache 必須受 `data_revision` 與 format version 約束。
- 大型 numerical input 不允許 silent truncation。
- 所有可能長時間操作需要 progress；無可信百分比時用 indeterminate + elapsed。

### 工程治理

- `main` 維持可工作正式版。
- branch → tests → PR → CI → squash merge。
- live Savant smoke test 保留。
- 重要架構變更同步更新本文件。

---

## 7. 不應被誤解的決策

1. **SQLite 是 source of truth；DuckDB 是 analytical mirror。**
2. **DuckDB executor 是 relational executor；Numerical Executor 是另一條正式 typed 計算路徑。**
3. **AST / workflow 是分析契約，不要求所有運算轉 SQL。**
4. **Numerical output 仍有 grain；cluster assignment 不能變成無身分的 dataframe。**
5. **Baseball Semantic Registry / workflow helpers 是便利層，不是封閉 domain model。**
6. **十個壓力測試保留為 regression / acceptance requirements；#10 現已具有正式多階段分群比較路徑。**
7. **Stage 4A–C 完成不代表 Stage 4D 已完成。圖表／匯出仍是下一階段。**
8. **完整資料 benchmark 已完成，不再把「真實完整 DB benchmark」列為待辦。**
9. **初次 DuckDB mirror build 是 one-time preparation；不得和 warmed query timing 混報。**
10. **分析進度必須誠實。** SQLite / CREATE INDEX 無可信百分比時不偽造。
11. **快取不是 SQL 字串快取。** 使用 analysis payload + data revision 等高層 contract，讓 relational / numerical result 共用一致失效原則。
12. **Bootstrap 不默認逐球獨立。** resampling unit 必須由使用者明確指定。
13. **數值安全門檻不是抽樣。** 超過上限就拒絕並要求縮小／先聚合，不偷偷截斷資料。

---

## 8. 更新規則

以下情況必須更新本文件：

- 新增／取消／重新定義確定要做的功能。
- 架構邊界改變。
- 某壓力測試正式完整支援或語意改變。
- Numerical / relational executor contract 改變。
- full-data benchmark 改變效能策略。
- 新增持久資料層。
- Stage 4D 或後續階段開始／完成。

本文件不是凍結所有 UI 細節，而是確保架構、驗收題、已完成能力與真正待辦不會隨聊天上下文遺失。
