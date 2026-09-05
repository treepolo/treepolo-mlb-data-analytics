# treepolo MLB Data Analytics — 架構、驗收題目與未來規劃

本文件是本專案的正式長期規劃來源。重要產品方向、架構邊界、驗收題與未來工作不得只存在聊天紀錄中。

最後重大更新：**2026-09-01，Stage 4A–4C 已正式完成實作、CI 與人工驗收；CAP-04 Auto K 與 Supplemental Savant Data 第一版納入完成範圍。Stage 4D Output / Visualization 為下一個實質產品階段。**

---

## 1. 產品目標

`treepolo MLB Data Analytics` 是一套以 Baseball Savant / Statcast 逐球資料為核心的 MLB 分析工作站。

核心不是固定報表，而是讓使用者可以把完整研究問題組合出來：

- 指定投打慣用手、球種、位置、球數、結果與任意 Statcast 欄位條件。
- 在同一打席或跨比賽時間軸描述有順序的條件。
- 依投手、比賽、時間區間、球種、武器庫、cohort 等層級分析。
- 選取「第二常用球種」「排除 FF 後表現最好球種」等相對角色。
- 建立條件計數、比例、衍生欄位、rolling / lag / lead / consecutive-N 等多階段工作流。
- 比較不同樣本、不同時間、不同資料層級的統計結果。
- 對 movement / velocity / release / spin 等特徵做自動分群，並可自動選擇群數。
- 做線性／二元迴歸與明確指定重抽樣單位的 Bootstrap / confidence interval。
- 保存分析設定、回看分析歷史，對完全相同且資料版本未變的分析直接使用快取。
- 保存未來會用到、但目前不應直接混入既有 Statcast 分析器的額外 Savant 資料來源。

概念主線：

```text
Statcast 逐球資料
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
排序／表格／保存／快取／Stage 4D 圖表與匯出
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

### 2.1 Statcast 主資料層

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
- UI 啟動不得為尚不存在的 mirror 偷偷開始數分鐘全量重建。
- mirror 已存在時可在背景 best-effort refresh。
- DuckDB 失敗時關聯分析可 fallback SQLite；分析加速層故障不得讓成功的資料匯入失敗。

分析狀態層：

- `data/analysis_state.sqlite3`。
- 與 Statcast source of truth 分離，只保存 result cache、analysis history、saved analyses。
- cache key 至少包含 canonical analysis payload、Statcast `data_revision`、requested backend 與 cache format version。
- 資料 revision 改變時舊 cache 不會被新分析命中。
- state store 使用 WAL 與 thread-safe access。

### 2.2 Supplemental Savant Data — 第一版已完成

2026-09-01 已完成兩類額外 Savant 資料來源的抓取／保存／管理能力。它們目前**只進資料管理層，不進既有 Statcast 分析器**。

#### Pitch3D 三維球路資料

來源：

```text
/app/pitch-data/{player_id}
/app/pitch-data/{player_id}?minors=1
```

目前確認：

- MLB 與 MiLB 明確分離。
- 一列一球。
- 以 `game_pk + play_id` 作為來源層逐球對應鍵。
- 完整保存來源 CSV 欄位；schema 可隨來源演進。
- 連續軌跡為 polynomial trajectory model，不冒充 Hawk-Eye raw frame points。
- raw response 獨立 gzip snapshot，保存 fetched time、hash、Last-Modified、Content-Type 等 metadata。
- Backfill、Resume、Update、Retry Failed、Verify、Rebuild、獨立進度均已建立。
- MLB / MiLB 共用 physical table 時仍保留 dataset namespace，不互相覆蓋。

人工驗收樣本 Ohtani `660271`：

- Pitch3D MLB：10,118 rows。
- Pitch3D MiLB：135 rows。
- Backfill / Resume / Update / Verify / Rebuild 均通過。
- Verify：duplicate row keys 0、missing snapshot files 0、hash mismatches 0。

目前沒有必要為尚未存在的跨來源分析功能先做全量下載；功能驗收樣本已足夠。

#### Hawk-Eye 旋轉／縫線姿態聚合資料

來源為 Savant pitcher page 的 `serverVals.spinAxis`，grain 為：

```text
player × season × pitch_type
```

代表性欄位：

```text
image_spin_x
image_spin_y
image_spin_z
image_orientation_angle
hawkeye_measured
movement_inferred
active_spin
alan_active_spin_pct
spin_rate
n_pitches
```

規則：

- 完整保存來源欄位，未知新欄位亦透過 dynamic schema 保存。
- source/dataset 固定為 `spin_aggregate / mlb`。
- Backfill、Resume、Update、Retry Failed、Verify、Rebuild、獨立進度均已建立。
- 人工驗收 Ohtani `660271`：34 rows；Backfill / Resume / Update / Verify / Rebuild 均通過。

#### Supplemental data 的分析邊界

目前既有 Statcast 分析器必須維持隔離：

- Pitch3D polynomial / trajectory 欄位不得出現在既有分析欄位列表。
- `image_spin_x/y/z`、`image_orientation_angle` 等 aggregate 欄位不得出現在既有分析欄位列表。
- 使用者直接輸入上述欄位，也不得被既有分析器當成合法 Statcast 欄位帶入。

2026-09-01 人工驗收已確認此隔離成立。

**長期方向不是永久隔離。** 未來應建立明確的多資料源相容分析架構，讓 Statcast、Pitch3D 與 spin aggregate 可以在知道各自 grain 與 join semantics 的前提下互相分析；不可藉由現在的資料匯入偷偷混表或污染既有 analyzer contract。

### 2.3 RESEARCH-01 — Hawk-Eye seam orientation / ball pose

正式研究文件：`docs/RESEARCH_01_HAWKEYE_SEAM_ORIENTATION.md`。

研究結論：

- Hawk-Eye 上游確實具有比公開 Statcast CSV 更高維度的旋轉／縫線資訊。
- Savant 公開 client 可看到 player × season × pitch_type 的 3D spin / orientation 聚合資料。
- 標準逐球 Statcast `spin_axis` 是公開的逐球 2D spin-axis direction；現有 circular feature handling 已正確處理。
- Pitch3D 公開的是連續 trajectory model。
- 在已檢查的公開 Baseball Savant / MLB browser surfaces 中，**沒有找到穩定可取得的逐球 seam orientation / absolute ball pose / quaternion / rotation matrix / seam phase / full pose time series endpoint**。
- 不以 aggregate spin data 或 `spin_axis` 偽造逐球球體姿態。

因此 RESEARCH-01 已於公開 client 邊界正式完成；若未來要做真實逐球 seam-pose integration，需等待合法且可驗證的新來源、受控資料權限或新的公開產品介面。

### 2.4 同步與資料維護

Statcast 已建立：

- 2015 起歷史回補。
- 預設每 5 個日曆日一個下載區段。
- Resume、Retry Failed、Incremental Update、recent correction window。
- Auto Update 與 scheduler。
- Rebuild from raw archive。
- Integrity verification。
- persistent fast Data Status；普通啟動不掃描整張逐球表。
- backfill live progress。
- SQLite explicit optimize command 具有終端機 stage/index/elapsed 顯示；SQLite 無可信單一 CREATE INDEX 百分比時不偽造。

Supplemental data 的同步單位可依來源不同，不強迫套用 Statcast 日期 chunk；目前以 pitcher 為主要 unit。

### 2.5 Grain

每個關聯或數值中間結果必須知道目前資料代表的分析層級。

典型 grain：Pitch、Plate Appearance、Game、Pitcher、Pitcher×Period、Pitcher×PitchType×Period、Arsenal、Cohort、Sequence/Pattern、Cluster。

採可組合 grain keys，不把所有層級硬編碼成封閉 enum。

未來多資料源分析必須額外尊重：

- Statcast：pitch grain。
- Pitch3D：pitch grain，可透過 `game_pk + play_id` 與對應來源逐球資料連接。
- spin aggregate：player × season × pitch_type aggregate grain。

### 2.6 Typed Analysis AST

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

### 2.7 關聯執行器

1. **DuckDB analytical executor**：大型分析預設路徑。
2. **SQLite executor**：fallback 與 correctness / benchmark 對照。

同一 AST 在 DuckDB / SQLite 應維持相同語意；例如使用率除法必須顯式轉 real，不能因 SQLite integer division 得到不同答案。

### 2.8 Numerical Executor

Stage 4C 已建立正式 Numerical Executor：

- `NumericalTable`：明確 columns + rows + grain。
- `NumericalSection`：明確 output columns + rows + grain。
- clustering input/output 保留 grain；cluster assignment 可作 typed continuation。
- 完整 assignment 與 UI 顯示列數分離。
- Numerical input 有 explicit row safety guard；超過門檻時拒絕，不偷偷抽樣或截斷。

#### CAP-04 — Auto Cluster Count

2026-09-01 正式完成。

Auto K contract：

- `K=1` 是合法候選。
- K 上限依樣本數 adaptive 決定，不固定盲試極大 K。
- candidate 受 minimum cluster size protection；不允許以極小群換取表面較佳分數。
- 每個候選 K 輸出 diagnostics：candidate K、criterion、score、valid、selected、cluster sizes、minimum cluster size、adaptive max K、rejection reason。
- global clustering 與 `Partition By` per-entity clustering 均可使用 Auto K；不同 partition 可選不同 K。
- 手動 K 模式維持原行為。

選模：

- Gaussian Mixture：BIC。
- K-means：使用 full-covariance Gaussian Mixture BIC 作為 **K selector**，選出 K 後實際分群仍由 K-means 執行。
- 原先嘗試的簡化 K-means selector 曾在明顯雙群 synthetic data 過切到 K=8，因此沒有交付；正式版本以 known-answer tests 防止 K=1 與明顯 K=2 失真。

Synthetic acceptance：

- 單一 Gaussian → K=1。
- 清楚雙群 → K=2。
- tiny candidate clusters → rejected。
- partition A 單群、partition B 雙群 → 可分別選 K=1 / K=2。

Natural acceptance：Max Scherzer 2024 FC + SL。

設定：

```text
pitcher = 453286
season = 2024
pitch_type IN FC,SL
features = release_speed,pfx_x,pfx_z,release_spin_rate
standardize = true
pitch_type 不作為 clustering feature
```

189 球具有完整四特徵資料。K-means Auto K 與 Gaussian Mixture Auto K 都選 **K=1**。候選 K=1–8 的 BIC 從 2152.270585 起，K=2 為 2206.783363，之後持續惡化。這個案例驗證 Auto K 不會因來源標籤存在 FC / SL 兩類就強迫 K=2。

### 2.9 SQLite 快速路徑

已加入：

- `game_year` index。
- `(game_pk, at_bat_number, pitch_number)`。
- `(game_year, pitch_type)`。
- `(pitcher, game_year, pitch_type)`。
- `(game_year, p_throws, stand)`。
- `ANALYZE` / `PRAGMA optimize`。
- `treepolo-mlb optimize`。

一般 OLAP 交給 DuckDB，不為所有 Statcast 欄位排列建立索引。

### 2.10 Baseball Semantic / Workflow layer

Semantic Registry 維持薄便利層，不封閉底層欄位。

Stage 4B typed workflow 可串接：

- Aggregate / conditional aggregate
- Derived arithmetic
- Filter
- Rolling window
- Lag / Lead
- Consecutive-N rising/falling
- First / Last / Nth
- Within-group Rank
- Project / Sort
- Arsenal Signature
- Relative Pitch Selector
- Relative Pitch Annotation
- Event Pattern Cohorts
- Empirical Percentile

這一層的目的，是把 primitive 組成完整研究流程，而不是讓使用者手動搬結果。

### 2.11 前端共用能力

既有共用基礎：

- 多選欄位共用 checklist renderer。
- 分析頁共用 Result Ordering。
- 所有分析共用 Job / Progress。
- 中英永久並列。
- XP / Windows 7 桌面應用視覺方向。

Stage 4A–C 已包含：

- Research Workflow。
- Clustering。
- Regression。
- Bootstrap / Confidence Interval。
- Multi-stage Cluster Comparison。
- Analysis Library：保存／載入／刪除分析。
- Analysis History。
- Result Cache 狀態顯示。
- clustering `Partition By`。
- CAP-04 Auto K。
- Supplemental Data Sources 管理 UI。

Stage 4D 前，結果維持 table-first，不建立第二套圖表統計邏輯。

---

## 3. 十個架構壓力測試需求

十題同時扮演長期架構題與 acceptance suite。Stage 4A–4C closure 時十題核心能力皆已覆蓋；詳細 remediation 與人工驗收見 `docs/STAGE4_ACCEPTANCE_REPORT.md`。

### 1. 三顆 Sweeper 的兩個極端球序

需要：same-PA partition、pitch order、exact count、last-event、Nth、adjacency。

**狀態：PASS。EventPattern / Event Pattern Cohorts 已覆蓋。**

### 2. 武器庫組成 + FF 相對角色

需要：usage、set signature、dynamic grouping、role selector、ties。

**狀態：PASS。Arsenal Signature / Relative Pitch Selector 已可組合。**

### 3. 跨比賽時間序列

需要：pitcher-game aggregation、conditional count / ratio、time order、consecutive-N、lead。

**狀態：PASS。**

### 4. 動態參考球種

每位投手找最高使用率非 FF，再與 FF 比較。

**狀態：PASS。Relative Pitch Selector / Annotation 已覆蓋。**

### 5. 巢狀分組 + 群內百分位

arsenal grouping → percentile cohort → downstream comparison。

**狀態：PASS。Empirical Percentile + Arsenal Signature + workflow composition 已覆蓋。**

### 6. 變動間距條件球序

Sweeper 後最多 N 球內找第一顆符合條件的目標球。

**狀態：PASS。FollowEvent 已覆蓋。**

### 7. 跨 grain 比較

投手整季 aggregate → 單場條件 → 更細 grain。

**狀態：PASS。Typed cross-grain aggregate / join 已覆蓋。**

### 8. Arsenal Set Difference

比較兩期間 arsenal 的新增／移除球種。

**狀態：PASS。Set Difference + Arsenal Change 已覆蓋並排除 NULL pitch_type。**

### 9. 每位投手自己的 percentile threshold

例如每位投手 FF 自己的第 80 百分位以上。

**狀態：PASS。Empirical Percentile + Individual Threshold 已覆蓋。**

### 10. 多階段選擇器 + 自動分群

Arsenal Signature → group-level relative selector → per-entity clustering → best cluster → reference comparison。

**狀態：PASS。Multi-stage Cluster Comparison 已產品化；CAP-04 又補上 Auto K，包括 K=1。**

K-means 與 Gaussian Mixture 已支援；DBSCAN / HDBSCAN 仍只是候選擴充，不是 Stage 4A–4C 完成條件。

---

## 4. 開發階段與目前狀態

### 資料基礎 — 第一版完成

Statcast fetch、raw archive、SQLite schema evolution、upsert、backfill/resume/retry、incremental update、scheduler、rebuild、integrity、fast status、live E2E、progress 均已建立。

Supplemental Pitch3D / spin aggregate 第一版資料管理能力也已完成，但尚未納入現有 analyzer。

長期仍需實際季中運行驗證 Savant revisions、scheduler 與 raw archive 成長。

### 第一階段：分析核心骨架 — 完成

Grain model、Typed AST、filter/aggregate/project/sort/limit/set、ranking、semantic registry、SQLite compiler/executor、planner boundary、serialization。

### 第二階段：高階關聯分析 — 完成

Window / lag / lead / rank / percentile、cross-grain Join、CollectSet / arsenal signature、EventPattern、FollowEvent、pitch usage、role ranking、tie handling、stress tests。

### 第三階段：正式使用介面 — 第一版完成，持續 UX 改善

分析頁 + Data Management、雙語 XP/7 UI、table result、backfill progress、共用欄位控制、共用排序／結果顯示／分析進度。

### 第四階段 A：效能、快取與分析工作區 — **正式完成**

完成：

- persistent result cache。
- canonical payload + data revision + backend + cache-format key。
- Analysis History。
- Save / Load / Delete Analysis。
- cached stored result restoration。
- state DB 與 Statcast DB 分離。
- DuckDB relational performance 路徑與完整資料 benchmark。
- result row safety / paging / large-result history behavior remediation。

### 第四階段 B：完整關聯 workflow — **正式完成**

完成：

- explicit rolling frame。
- conditional metrics。
- derived arithmetic。
- consecutive-N trend。
- generalized lag / lead。
- first / last / nth。
- rank / filter / project / sort composition。
- Arsenal Signature。
- Relative Pitch Selector / Annotation。
- Event Pattern Cohorts。
- Empirical Percentile。
- SQLite / DuckDB ratio semantics parity。
- Research Workflow UI。

### 第四階段 C：Numerical Executor — **正式完成**

#### Numerical contract

- typed NumericalTable / NumericalSection。
- grain-preserving clustering continuation。
- numerical input safety guard。
- deterministic seed parameters。

#### Clustering

- K-means。
- Gaussian Mixture。
- optional feature standardization。
- global / `Partition By` per entity clustering。
- cluster sample size / center / mean / SD summary。
- GMM assignment probability。
- full internal assignment retained；UI rows 可另行限制。
- Multi-stage Cluster Comparison。
- **CAP-04 Auto K：K=1、adaptive K max、minimum cluster size、candidate diagnostics、GMM BIC、K-means selector。**

#### Regression

- Linear OLS：coefficients、SE、t statistic、p value、CI、R²、RMSE、df。
- Binary Logistic：coefficients、accuracy、log loss。
- predictor standardization option。
- synthetic known-answer：`y = 2 + 3x` 恢復 intercept 2 / slope 3 / R² 1。

第一版 Logistic 係數 inferential SE / p / CI 尚未提供；欄位明確為 NULL，不冒充已計算。

#### Bootstrap

- mean / median / proportion。
- optional A-B difference。
- explicit resampling unit 強制指定。
- percentile confidence interval。
- deterministic seed。
- A/B resampling units 互斥時採 stratified unit resampling。
- mean / proportion 使用 unit-level sufficient summaries 加速。
- row-wise median / mixed-unit workload 過大時明確拒絕。

### Stage 4A–4C Closure

2026-09-01 正式 closure：

- persistent automated tests：PASS。
- live Savant integration tests：PASS。
- 十個架構壓力測試：核心能力 PASS。
- PERF-10A、BUG-10A、BUG-10B、RS-01、RS-02、FIELD-01、WORDING-01、UX-12～15 等已驗收項目維持 PASS。
- CAP-04 synthetic + Scherzer 2024 natural acceptance：PASS。
- Supplemental Pitch3D / Hawk-Eye aggregate live fetch：PASS。
- Supplemental Backfill / Resume / Update / Verify / Rebuild 人工驗收：PASS。
- Supplemental fields 與既有 Statcast analyzer 隔離人工驗收：PASS。
- final branch CI：PASS。

因此 Stage 4A–4C 不再列為開發中或待驗收。

### 第四階段 D：Output / Visualization — **下一階段，尚未開始**

候選範圍：

- 正式圖表／視覺化。
- 匯出。
- sample-size / uncertainty visualization。
- richer comparison presentation。
- preset / library UX 深化。

圖表不得另寫第二套統計邏輯；只能消費正式 analysis result / typed result contract。

---

## 5. 大型資料效能實測

2026-08-26 使用完整本機資料庫：

- pitch rows：**9,192,548**。
- benchmark：2026 → Group By `pitch_type` → Count + Average `release_speed` → computed metric sort。

第一次：

- SQLite median **2.3451 s**。
- DuckDB median **0.06959 s**。
- 初次 DuckDB mirror prepare：**245.532 s**，`mirror_rebuilt = true`。

第二次：

- SQLite median **2.3146 s**。
- DuckDB median **0.07560 s**。
- mirror prepare/check：**0.03931 s**，`mirror_rebuilt = false`。

代表性 warmed DuckDB query 約比 SQLite 快 30× 以上；初次 mirror build 是一次性 preparation，不混入 query timing。

結論：

- 大型互動分析以 DuckDB 為 primary。
- SQLite 保留 fallback / correctness / 小型操作合理效能。
- repeated-result cache 消除資料未變時的完全相同重算。
- 若未來某正式分析仍達分鐘級，視為需 profiling 的效能問題，不以資料量直接合理化。

---

## 6. 仍需長期維護／未來待辦

### 資料與可靠性

- 持續維護 2015→現在 full Statcast dataset。
- 記錄 SQLite、DuckDB、raw archive、analysis_state、supplemental raw/archive 的實際磁碟容量與成長。
- 季中長期 Auto Update 驗證。
- 真實 Savant 歷史 revision → SQLite update → DuckDB incremental refresh 驗證。
- raw snapshot retention / compaction 僅在量測後設計。
- Supplemental data 暫不要求全量下載；等跨資料源分析有實際需求再決定資料覆蓋範圍與抓取策略。

### 未來多資料源分析

需要另外設計，不屬 Stage 4A–4C closure：

- Statcast + Pitch3D pitch-level join。
- Statcast / Pitch3D 與 player × season × pitch_type spin aggregate 的合法 grain-aware join。
- 明確 source selector / provenance。
- 欄位衝突、duplicate source fields、NULL / historical availability policy。
- DuckDB mirror / analytical source 如何納入 supplemental tables。
- cache/data revision 如何反映多來源更新。

### 分析正確性

- 細分統計持續顯示 sample size。
- uncertainty 明確定義計算單位。
- ties、NULL、低樣本、歷史欄位缺失政策保持一致。
- DuckDB / SQLite AST parity 持續回歸。
- regression / bootstrap / clustering 持續增加真實棒球問題導出的 known-answer / synthetic acceptance tests。
- Auto K 未來若更換 selector，必須重新通過 K=1、K=2、tiny cluster、partition-specific 與 Scherzer natural acceptance。

### 前端／UX

- 依真實使用修正難懂操作，不為了「看起來功能多」堆設定。
- 中英永久並列、XP/7 視覺方向保持。
- 共用互動維護單一元件／單一 contract。
- Research Workflow 與 Numerical pages 持續以完整研究問題為導向。
- Stage 4D 處理圖表與輸出。

### 效能

- canonical benchmark 永久保留。
- mirror build time 與 query time 分開報告。
- cache 必須受 `data_revision` 與 format version 約束。
- 大型 numerical input 不允許 silent truncation。
- 所有可能長時間操作需要 progress；無可信百分比時用 indeterminate + elapsed。

### 工程治理

- `main` 維持可工作正式版。
- branch → tests → PR → CI → merge。
- live Savant smoke test 保留。
- 重要架構變更同步更新本文件。

---

## 7. 不應被誤解的決策

1. **SQLite 是 Statcast source of truth；DuckDB 是 analytical mirror。**
2. **DuckDB executor 是 relational executor；Numerical Executor 是另一條正式 typed 計算路徑。**
3. **AST / workflow 是分析契約，不要求所有運算轉 SQL。**
4. **Numerical output 仍有 grain；cluster assignment 不能變成無身分的 dataframe。**
5. **Baseball Semantic Registry / workflow helpers 是便利層，不是封閉 domain model。**
6. **十個壓力測試保留為 regression / acceptance requirements。**
7. **Stage 4A–C 已正式完成；Stage 4D 尚未開始。**
8. **完整資料 benchmark 已完成，不再列為待辦。**
9. **初次 DuckDB mirror build 是 one-time preparation；不得和 warmed query timing 混報。**
10. **分析進度必須誠實。** 無可信百分比時不偽造。
11. **快取不是 SQL 字串快取。** 使用 analysis payload + data revision 等高層 contract。
12. **Bootstrap 不默認逐球獨立。** resampling unit 必須由使用者明確指定。
13. **數值安全門檻不是抽樣。** 超過上限就拒絕並要求縮小／先聚合。
14. **Auto K 必須允許 K=1。** 來源球種標籤數量不是強迫群數的理由。
15. **Supplemental data 現在保存、未來可跨來源分析，但目前不得被既有 Statcast analyzer 讀取。**
16. **Pitch3D trajectory polynomial 不是 raw Hawk-Eye frame tracking。**
17. **Spin aggregate 不是逐球 seam pose。** 不得把 aggregate 或 `spin_axis` 冒充逐球真實球體姿態。
18. **未找到公開逐球 seam-pose endpoint 就維持未找到。** 不以無止境 URL 猜測或推導資料偽造來源。

---

## 8. 更新規則

以下情況必須更新本文件：

- 新增／取消／重新定義確定要做的功能。
- 架構邊界改變。
- 某壓力測試正式支援狀態或語意改變。
- Numerical / relational executor contract 改變。
- full-data benchmark 改變效能策略。
- 新增持久資料層或多資料源分析能力。
- Stage 4D 或後續階段開始／完成。

本文件不是凍結所有 UI 細節，而是確保架構、驗收題、已完成能力與真正待辦不會隨聊天上下文遺失。