# treepolo MLB Data Analytics — 架構、驗收題目與未來規劃

本文件是本專案的正式長期規劃來源，用來保存「為什麼這樣設計、哪些需求必須支援、哪些能力尚未完成、未來要往哪裡做」。重要產品方向與未來工作不得只存在聊天紀錄中。

---

## 1. 產品目標

`treepolo MLB Data Analytics` 是一套以 Baseball Savant / Statcast 逐球資料為基礎的 MLB 分析應用。

核心不是固定報表，而是讓使用者可以組合高度細緻的棒球問題，例如：

- 指定投打慣用手、球種、位置、球數、結果等條件。
- 在同一打席內描述有順序的球序條件。
- 依投手、比賽、時間區間、球種、武器庫等層級分組。
- 選取「第二常用球種」「排除四縫線後表現最好球種」等相對角色。
- 比較不同樣本、不同時間、不同資料層級的統計結果。
- 未來加入分群、迴歸、重抽樣等數值分析。

概念主線：

```text
逐球資料
  ↓
條件／篩選
  ↓
分組、球序模式、角色選擇、跨層級比較
  ↓
結果定義與統計
  ↓
排序／表格／未來圖表與其他輸出
```

對高度細分的比例或統計，應始終讓使用者看得到樣本數；未來可再加入信賴區間等不確定性資訊。

---

## 2. 完整系統架構

核心架構是 **Typed Analysis AST + Execution Planner**。前端描述分析意圖，後端建立有型別、知道資料層級的分析運算樹，再由執行層選擇適合的執行器。

```text
Baseball Savant / Statcast
          │
          ▼
原始資料保存 Raw Archive (.csv.gz)
          │
          ▼
SQLite 正規化主資料庫（source of truth）
          │
          ├───────────────┐
          │               ▼
          │       DuckDB columnar analytical mirror
          │               │
          ▼               ▼
前端分析建構器 → Typed Analysis AST
                      │
                      ▼
               Grain Validation
                      │
                      ▼
               Execution Planner
                      │
          ┌───────────┼──────────────┐
          ▼           ▼              ▼
     DuckDB SQL    SQLite SQL     未來 Numerical
      Executor      fallback       Executor
          │           │         clustering / regression /
          │           │         bootstrap / models
          └───────────┴──────────────┘
                      ▼
                  結果結構
                      │
                      ▼
             共用排序／Job/Progress
                      │
                      ▼
                   Web UI
```

### 2.1 資料層

資料來源為 Baseball Savant Statcast CSV。

原始層：

- 每次成功取得的 Savant CSV 原封保存。
- gzip 壓縮保存於 `data/raw/年/月/`。
- 保存 SHA-256、抓取時間、日期範圍等 manifest。
- 相同日期範圍且內容完全相同的快照去重。

主資料層：

- SQLite：`data/statcast.sqlite3`。
- SQLite 是同步、修訂、完整性與重建的 **source of truth**。
- 保留 Savant 回傳的全部合法欄位，不硬編碼固定欄位數。
- 新增上游欄位時自動擴充 schema 並留下 schema event。
- 舊年份不存在的欄位允許為 NULL。
- 逐球自然鍵：`game_pk + at_bat_number + pitch_number`。
- 缺自然鍵資料以 deterministic fallback ID 保存並在 integrity report 揭露。
- 增量抓取採 idempotent upsert，同一顆球不重複，Savant 事後修訂可更新既有資料。

分析加速層：

- DuckDB：預設 `data/statcast.duckdb`。
- DuckDB 是 SQLite 的持久化 columnar analytical mirror，不取代 SQLite 的資料真本角色。
- 第一次需要分析時可建立 mirror；之後依 SQLite `data_revision` / `_ingested_at` 增量刷新。
- Update / Backfill / Retry / Rebuild 完成後，若 mirror 已存在，會 best-effort 同步。
- DuckDB 無法使用時分析可安全 fallback 到 SQLite；加速層故障不得讓成功的資料匯入失敗。

### 2.2 同步／資料維護

已建立：

- 2015 起歷史回補。
- 預設 5 個日曆日一個下載區段。
- Resume：跳過已成功完成的相同日期區段。
- Retry Failed：只重跑已記錄的失敗區段。
- Incremental Update：更新新增日期並重抓最近修訂窗口。
- Auto Update 開關與 scheduler。
- Rebuild：從 raw archive 重新建立 SQLite；分析 mirror 隨之失效／重建。
- 資料完整性檢查。
- 快速持久化 Data Status，普通啟動不再掃描整張逐球大表。
- 歷史回補即時進度：總區段、已完成、目前區段、接收逐球數、失敗數、耗時、粗略 ETA。

### 2.3 分析資料層級（Grain）

每個分析節點都必須知道目前資料代表的層級，避免把不同層級的數值錯誤混用。

典型層級：Pitch、Plate Appearance、Game、Pitcher、Pitcher × Period、Pitcher × Pitch Type × Period、Arsenal、Cohort、Sequence / Pattern。

目前採可組合 grain keys，不把所有層級做成死板 enum。

### 2.4 分析運算樹（Typed Analysis AST）

目前關聯分析核心已支援：

- Source
- Filter
- Aggregate
- Project
- Sort
- Limit
- Set Operation
- Window / Rank
- Join
- Collect Set
- Event Pattern
- Follow Event
- 條件表達式、Boolean、Case、NULL、IN 等

AST 可序列化為 JSON，作為前端與分析核心的正式契約，也為未來儲存分析設定與 AI 產生分析提供基礎。

### 2.5 執行規劃器與執行器

關聯分析目前已有兩條 SQL 執行路徑：

1. **DuckDB analytical executor**：預設大型分析路徑，使用 columnar mirror。
2. **SQLite executor**：可靠 fallback，也保留作為 correctness / benchmark 對照。

這不等於未來的 Numerical Executor 已完成。分群、迴歸、Bootstrap 等非關聯數值計算仍需建立正式的第二類計算執行契約，不能把 dataframe 當成無型別萬用層。

### 2.6 SQLite 快速路徑

SQLite 仍需維持合理效能，已加入／規劃維護：

- `game_year` index。
- `(game_pk, at_bat_number, pitch_number)` 球序 index。
- `(game_year, pitch_type)`。
- `(pitcher, game_year, pitch_type)`。
- `(game_year, p_throws, stand)`。
- `ANALYZE` / `PRAGMA optimize`。
- `treepolo-mlb optimize` 可顯式建立／刷新分析索引與 planner statistics。

原則：不為 119 個欄位所有排列組合建立索引；一般 OLAP 工作主要交給 DuckDB。

### 2.7 棒球語意層

Baseball Semantic Registry 是薄便利層，不是封閉式 domain model。常用概念可重用，例如 fastball、sweeper、changeup、breaking ball、fastball family、swing / whiff / called strike、zone、RHP vs RHB；使用者仍可回到底層欄位與條件組合。

### 2.8 前端共用能力

前端原則：

- 所有對使用者有意義的分析意圖應有前端表達方式。
- 不直接暴露 Join、Window、Set Difference 等底層資料庫概念。
- UI 中英文永久並列。
- 維持 Windows XP + Windows 7 視覺語言。
- 圖表仍不建立第二套統計邏輯；未來只消費正式分析結果。

目前共用 UI 基礎：

- **8 個所有多選欄位共用同一 checklist renderer**，不再有 Basic Analysis 特例。
- **9 個分析頁共用同一 Result Ordering 元件與後端排序層**；各分析只宣告可排序輸出欄位，不各自實作排序。
- 可使用多重排序鍵；計算結果（例如平均球速、difference）可排序。
- **所有分析共用 Analysis Job / Progress**；不再由各頁自行做進度機制。
- DuckDB 可提供實際 query progress；SQLite fallback 無可靠百分比時顯示執行階段與耗時，禁止偽造百分比。

---

## 3. 十個架構壓力測試需求

這十題長期保留作為架構驗收題，不等於十題都已完整產品化。

### 1. 三顆 Sweeper 的兩個極端球序

同一打席總共恰好三顆 Sweeper，最後一球也是 Sweeper。比較：A 三顆完全連續；B 三顆彼此完全不相鄰。部分相鄰刻意排除。分析對象是第三顆 Sweeper。

需要：same-PA partition、pitch order、exact count、last-event、Nth event、adjacency classification。

**狀態：第二階段關聯能力已覆蓋。**

### 2. 武器庫組成 + 四縫線相對角色

球種使用率高於門檻才納入武器庫；依完全相同 pitch-type set 自動分組。各組再分 FF 是否最高使用率，並把 FF 與第二常用球種或最佳非 FF 指標球種比較。

需要：usage、set signature、dynamic grouping、role selector、rank / tie、relative comparison。

**狀態：第二階段核心能力已覆蓋；產品操作流程仍可強化。**

### 3. 跨打席／跨比賽時間序列

若某投手某球種使用率連續三場上升，檢查第四場球速／Stuff 類指標／揮空率是否改變。

需要：pitcher-game aggregation、time order、lag / lead、rolling / consecutive-period logic。

**狀態：lag / lead 與時間序列基礎已有；完整「連續 N 期條件」仍待高階 helper / window-frame。**

### 4. 動態參考球種

每位投手找出使用率最高的非 FF 球種，再與 FF 比較球速、揮空率、xwOBA 等並依差值衍生 cohort。

需要：per-entity argmax / role selector、exclude、within-pitcher comparison。

**狀態：第二階段主要能力已覆蓋。**

### 5. 巢狀分組 + 群內百分位

先依武器庫組成分組，再於每組依 FF 使用率百分位分高／中／低群，最後比較 changeup 表現。

需要：group → within-group percentile → regroup → aggregate。

**狀態：empirical percentile、group、join 已有；產品流程仍可強化。**

### 6. 變動間距的條件球序

每次 Sweeper 後，在最多 3 球內找第一顆再次出現的 Sweeper，依中間是否出現 FF 分組，比較第二顆 Sweeper 結果。

需要：bounded lookahead、first subsequent match、variable gap、between-event classifier。

**狀態：`FollowEvent` 已覆蓋。**

### 7. 跨資料層級比較

先算投手整季 FF 平均球速，再找單場 FF 平均低於整季至少 1.5 mph 的比賽，分析那些比賽第三輪對打線時的 breaking-ball 使用率。

需要：season → game → PA / pitch 跨 grain 聚合、join、derived predicate。

**狀態：typed cross-grain join / aggregate 已有；第三輪打線等高階棒球語意 helper 未完成。**

### 8. 動態集合關係：武器庫變化

比較同一投手上／下半季武器庫，找後半季新進入使用率門檻的球種，再分析新增球種後其他球種使用率或表現變化。

需要：period cohort、arsenal sets、set difference、role change。

**狀態：集合差與武器庫建構已覆蓋；已有 Arsenal Change 前端。**

### 9. 每位投手自己的樣本門檻

「高球速 FF」定義成該投手自己的 FF 球速第 80 百分位以上，再比較高／非高樣本的數量與結果。

需要：per-entity percentile、sample-derived threshold、derived predicate。

**狀態：empirical percentile 已覆蓋；已有 Individual Threshold 前端。**

### 10. 多階段選擇器 + 自動分群

先在某武器庫組找整體表現最佳的非 FF 球種；再於每位投手該球種的 movement / velocity / release / spin 特徵自動分群，選出每位投手表現最佳 cluster，再與 FF 比較。

需要：group-level selector → entity-level selector → multivariate clustering → nested comparison。

候選方法：K-means、Gaussian Mixture、DBSCAN、HDBSCAN。

**狀態：前半段關聯選擇器有基礎；真正自動分群尚未完成，仍是第四階段 Numerical Executor 的核心驗收題。**

---

## 4. 開發階段與目前狀態

### 資料基礎

**系統實作已完成；完整本機資料集需以實際 persistent backfill 為準。**

已完成：Savant 下載、raw archive、SQLite/schema evolution、idempotent upsert、backfill/resume/retry、incremental update、auto update、rebuild、integrity、fast status、live E2E、回補進度。

仍需長期驗證：完整資料容量、季中 scheduler、真實 Savant 修訂案例、raw snapshot 長期成長。

### 第一階段：分析核心骨架 — 已完成

Grain model、Typed AST、filter/aggregate/project/sort/limit/set、ranking、semantic registry、SQLite compiler/executor、execution planner boundary、serialization。

### 第二階段：高階關聯分析 — 已完成

Window / lag / lead / rank / percentile、cross-grain Join、CollectSet / arsenal signature、EventPattern、FollowEvent、pitch usage、arsenal、pitch-role ranking、tie-safe ranking、stress-test acceptance coverage。

### 第三階段：正式使用介面 — 第一版完成，持續改善

已完成九種分析頁與 Data Management、雙語 XP/7 UI、table result、回補進度。

2026-08-25 使用實測後追加並完成：

- 8 個多選欄位統一 checklist renderer。
- 9 個分析頁統一 Result Ordering 元件／後端排序層。
- 所有分析統一 Analysis Job / Progress。
- Basic Analysis 的中位數、母體／樣本標準差與 computed-metric sorting。

### 第四階段：進階計算與產品完善 — **已提前啟動部分工作**

#### 4.1 Numerical Executor — 未完成

仍需建立非 SQL 數值計算正式路徑：

```text
AST / analysis plan
        ↓
Execution Planner
        ↓
Numerical Executor
```

要求：明確 input/output schema 與 grain；可與關聯結果組合；不得讓 dataframe 變成無型別萬用層。

**注意：2026-08-25 完成的 DuckDB executor 是關聯 analytical executor，不代表本節完成。**

#### 4.2 自動分群 — 未完成

優先滿足壓力測試 #10。需支援：特徵選擇、標準化、K-means、Gaussian Mixture；DBSCAN / HDBSCAN 視需求與依賴評估；cluster label 可回接 pitch/entity 並成為後續 filter/group/selector；超參數可重現；輸出樣本數與 cluster 摘要。

#### 4.3 迴歸與統計模型 — 未完成

方向：線性／廣義線性等基礎模型；指定 dependent / independent variables；輸出係數、樣本數、必要統計量；實際模型依棒球問題收斂。

#### 4.4 重抽樣／Bootstrap — 未完成

用途：比例／平均／差值不確定性、細分樣本 confidence interval、兩組差異重抽樣分布。必須明確指定重抽樣單位，不能在群聚逐球資料上錯誤假設每球獨立。

#### 4.5 更完整視窗／序列 — 部分完成、持續待辦

候選：explicit rolling frame、連續 N 期上／下降 helper、first/last/nth value 一般化、更複雜跨 PA/game sequence。

#### 4.6 大型資料效能 — **第一輪已提前完成，仍需真實完整 DB 驗收與後續 profiling**

2026-08-25 已完成：

- SQLite 重要索引補強。
- `ANALYZE` / `PRAGMA optimize` 與 `treepolo-mlb optimize`。
- 持久化 DuckDB columnar analytical mirror。
- DuckDB relational executor + SQLite fallback。
- 資料 revision / incremental mirror refresh。
- 共用分析 job/progress。
- 可重複 benchmark harness。
- `treepolo-mlb benchmark --year 2026 --backend both` 標準題：2026 各球種 Count + 平均球速。

**仍未完成：**

- 在使用者完整 2015→現在數 GB SQLite 上跑 benchmark，取得 SQLite vs DuckDB 實測時間。
- 明確驗收「2026 各球種平均球速」是否達互動式幾秒級；若未達標繼續 profiling，不接受分鐘級。
- intermediate result caching / compiled-query cache / repeated-result cache 目前尚未實作；只有在 benchmark 證明需要時再做。
- streaming / materialization strategy 仍依大結果實測決定。

#### 4.7 產品完善 — 多數未完成

未來：圖表、更多視覺化、匯出、儲存／載入 AST、複製分析、分析歷史/preset、sample-size / uncertainty UI、AI → AST。

---

## 5. 其他未來待辦

### 資料與可靠性

- 完成／持續維護 2015→現在 persistent full backfill。
- 記錄 SQLite、DuckDB mirror、raw archive 的實際磁碟容量。
- 驗證季中長期 Auto Update。
- 驗證 Savant 真實歷史修訂造成 row update 並正確刷新 DuckDB mirror。
- 評估 5-day chunk 是否需依實測調整。
- raw snapshot 長期成長過快時才設計 retention / compaction；未量測前不刪原始資料。

### 分析正確性

- 所有細分統計持續顯示 sample size。
- confidence interval / uncertainty 加入時明確定義計算單位。
- ties、NULL、低樣本、球種分類變更、歷史欄位缺失保持一致政策。
- 增加由真實棒球研究問題導出的 acceptance tests。
- DuckDB 與 SQLite 對同一 AST 必須維持語意／結果一致性；複雜球序、Join、Window、Arsenal 皆需持續回歸測試。

### 前端／使用體驗

- 持續依真實使用修正難懂操作，不自行堆額外功能。
- 中英永久並列、XP/7 視覺方向保持。
- 所有可共用的互動（多選、排序、Job/Progress）必須共用元件；除非存在無法抽象的真實語意差異，不各頁維護特殊版本。
- 高階球序／球種角色持續補 tooltip / 使用教學。
- 圖表延後；未來圖表只消費正式分析結果。
- 評估儲存／載入／複製分析設定 UI。

### 效能驗收

- 標準 benchmark 必須保留，至少包含「指定球季 → pitch_type group → Count + Average release_speed → computed metric sort」。
- benchmark 應分離第一次 DuckDB mirror build 時間與已建 mirror 的互動查詢時間。
- 大型分析出現分鐘級延遲視為效能問題，不以「資料很多」直接合理化。
- 所有可能長時間執行的分析都必須有共用進度／狀態顯示；沒有可信百分比時顯示 indeterminate，不偽造進度。

### 工程治理

- `main` 維持可工作的正式版本。
- branch → tests → PR → CI → squash merge 為預設流程。
- 真實 Savant smoke test 保留。
- 重要架構變更同步更新本文件。

---

## 6. 不應被誤解的決策

1. **SQLite 是資料 source of truth；DuckDB 是分析加速 mirror。** 不應倒過來讓同步與修訂依賴 DuckDB。
2. **DuckDB executor 是關聯分析執行器，不是 Numerical Executor。** 自動分群／迴歸／Bootstrap 仍未完成。
3. **AST 是分析契約，不要求所有運算都轉 SQL。**
4. **Baseball Semantic Registry 是便利層，不是封閉 domain model。**
5. **十個壓力測試不是十題皆完整產品化。** 尤其 #10 自動分群仍未完成。
6. **第三階段不做圖表是刻意縮小範圍，不代表永久不要圖表。**
7. **完整歷史資料能力已具備，但 persistent full dataset 狀態以實機為準。**
8. **共用 UI 功能應維護一套元件。** Basic Analysis 不再因額外連動需求維護獨立 checklist／sorting renderer；差異應透過 callback / output schema 等參數化方式處理。
9. **分析進度必須誠實。** DuckDB 可顯示實際 query progress；SQLite 無可信百分比時用不定進度與 elapsed time。
10. **效能優化以 benchmark 驗收。** 不以單純新增某個 index 就宣告完成，也不在沒有量測前建立 119 欄位所有索引排列。

---

## 7. 更新規則

出現以下情況必須更新本文件：

- 新增／取消／重新定義確定要做的未來功能。
- 架構邊界改變。
- 某壓力測試被正式完整支援。
- 第四階段某項提前實作或完成。
- 真實完整資料 benchmark 後效能策略改變。
- 新增另一個持久資料層或執行器。

本文件的目的不是凍結所有細節，而是確保架構、驗收題與未來工作不會因聊天上下文變長而遺失。
