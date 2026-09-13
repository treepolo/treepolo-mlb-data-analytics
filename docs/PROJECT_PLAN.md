# treepolo MLB Data Analytics — 架構、驗收題目與目前規劃

本文件是本專案的正式長期規劃來源。重要產品方向、架構邊界、驗收題與真正待辦不得只存在聊天紀錄中。

最後重大更新：**2026-09-13。Stage 4A–4D 均已完成實作、CI 與人工驗收並正式 CLOSED；CAP-04 Auto K、Supplemental Savant Data 第一版與 RESEARCH-01 也維持已完成狀態。目前沒有 Stage 4E 或其他已定義的下一階段。**

---

## 1. 產品目標

`treepolo MLB Data Analytics` 是一套以 Baseball Savant / Statcast 逐球資料為核心的 MLB 分析工作站。

核心不是固定報表，而是讓使用者能把完整研究問題組合出來：

- 指定投打慣用手、球種、位置、球數、結果與任意 Statcast 欄位條件。
- 在同一打席或跨比賽時間軸描述有順序的條件。
- 依投手、比賽、時間區間、球種、武器庫、cohort 等 grain 分析。
- 選取「第二常用球種」「排除 FF 後表現最好球種」等相對角色。
- 建立條件計數、比例、衍生欄位、rolling / lag / lead / consecutive-N 等多階段工作流。
- 比較不同樣本、不同時間、不同資料層級的統計結果。
- 對 movement / velocity / release / spin 等特徵做分群，並可自動選擇群數。
- 做線性／二元迴歸與明確指定重抽樣單位的 Bootstrap / confidence interval。
- 保存分析設定、回看分析歷史，對完全相同且資料版本未變的分析使用快取。
- 把正式 analysis result 送入獨立 Visualization／Export／Report presentation layer，而不在前端重做統計。
- 保存未來可能使用、但目前不應直接混入既有 Statcast 分析器的額外 Savant 資料來源。

概念主線：

```text
Statcast 逐球資料
  ↓
條件／篩選
  ↓
分組／球序／角色選擇／跨層級／多階段 workflow
  ↓
Typed relational result
  ↓ optional
Numerical Executor
  ↓
Structured result sections
  ├─ table-first Analysis Result
  ├─ Result Cache / History / Saved Analysis
  └─ Output presentation layer
       ├─ Visualization
       ├─ data / figure export
       └─ HTML / PDF report
```

高度細分的比例或統計必須讓使用者看得到樣本數；不確定性分析必須說清楚 sampling / resampling unit；Visualization sampling 只能影響 presentation，不得偷偷改變分析母體。

---

## 2. 系統架構

核心維持 **Typed Analysis AST + Grain-aware execution**。

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
          ┌───────────┼────────────────────┐
          ▼           ▼                    ▼
  Cache / History   Analysis UI       Output / Visualization
                                          │
                              ┌───────────┼───────────┐
                              ▼           ▼           ▼
                           Figure      Data Export    Report
```

### 2.1 Statcast 主資料層

- `data/statcast.sqlite3` 是同步、修訂、完整性與 rebuild 的 **source of truth**。
- 每次成功取得的 Savant CSV 原封保存為 gzip raw snapshot，並保存 hash / manifest。
- 保留 Savant 回傳的全部合法欄位；schema 可隨上游演進。
- 自然鍵為 `game_pk + at_bat_number + pitch_number`；缺鍵資料使用 deterministic fallback ID 並在 integrity report 揭露。
- 同一球重新抓取採 idempotent upsert，Savant 事後修訂可更新舊值。

### 2.2 DuckDB 分析層

- `data/statcast.duckdb` 是 SQLite 的持久化 columnar analytical mirror，不取代 SQLite source-of-truth 角色。
- 第一次需要時建立；之後依資料 revision 增量／必要刷新。
- UI 啟動不得為尚不存在的 mirror 偷偷開始數分鐘全量重建。
- DuckDB 失敗時關聯分析可 fallback SQLite。

### 2.3 Analysis / Presentation state

- `data/analysis_state.sqlite3` 與 Statcast source of truth 分離。
- 保存 result cache、analysis history、saved analyses，以及 Stage 4D presentation metadata / presets / Saved Visualization references。
- cache key 至少包含 canonical analysis payload、Statcast `data_revision`、requested backend 與 cache format version。
- Frozen Visualization v2 的大型 result payload 不直接塞入 DB；採 content-addressed SHA-256 gzip JSON snapshot，DB 保存 hash/path/metadata/reference。
- Frozen v2 snapshot 可包含完整 multi-section result，重複內容去重，刪除時依 reference policy 清理。

### 2.4 Supplemental Savant Data — 第一版完成

2026-09-01 已完成兩類額外 Savant 資料來源的抓取／保存／管理能力；目前仍**只進資料管理層，不進既有 Statcast 分析器**。

#### Pitch3D

來源：

```text
/app/pitch-data/{player_id}
/app/pitch-data/{player_id}?minors=1
```

- MLB / MiLB namespace 分離。
- 一列一球，以 `game_pk + play_id` 作來源層逐球鍵。
- 完整保存來源欄位與 raw gzip snapshot。
- trajectory 是 polynomial model，不冒充 Hawk-Eye raw frame points。
- Backfill / Resume / Update / Retry Failed / Verify / Rebuild / progress 已建立。
- Ohtani `660271` 人工驗收：MLB 10,118 rows、MiLB 135 rows；完整 lifecycle PASS。

#### Hawk-Eye spin / seam aggregate

來源為 Savant pitcher page `serverVals.spinAxis`，grain：

```text
player × season × pitch_type
```

代表欄位：`image_spin_x/y/z`、`image_orientation_angle`、`hawkeye_measured`、`movement_inferred`、`active_spin`、`spin_rate`、`n_pitches`。

- 完整保存來源欄位與 dynamic schema。
- source/dataset = `spin_aggregate / mlb`。
- Ohtani `660271` 人工驗收：34 rows；完整 lifecycle PASS。

#### Supplemental analyzer 邊界

Pitch3D trajectory 與 spin aggregate 欄位目前不得出現在既有 Statcast analyzer 欄位列表，也不得因直接輸入欄位名而繞過限制。長期若要跨來源分析，必須另做明確 source / grain / join / revision contract。

### 2.5 RESEARCH-01 — Hawk-Eye seam orientation / ball pose

正式研究文件：`docs/RESEARCH_01_HAWKEYE_SEAM_ORIENTATION.md`。

結論維持：公開 Savant surfaces 可取得更高維 spin/orientation aggregate 與 Pitch3D trajectory，但目前沒有找到穩定公開的逐球 seam orientation / absolute ball pose / quaternion / rotation matrix / seam phase / full pose time-series endpoint。不得以 aggregate 或普通 `spin_axis` 偽造逐球真實球體姿態。

**RESEARCH-01：CLOSED at current public-client boundary.**

### 2.6 Grain / Typed Analysis AST

每個關聯或數值中間結果必須知道目前 grain。典型 grain 包含 Pitch、Plate Appearance、Game、Pitcher、Pitcher×Period、Pitcher×PitchType×Period、Arsenal、Cohort、Sequence/Pattern、Cluster。

Typed AST 已支援 Source、Filter、Aggregate、Project、Sort、Limit、Set Operation、Rank、Window、explicit Window Frame、Join、Collect Set、Event Pattern、Follow Event，以及 Column / Literal / Binary / Boolean / Case / NULL / IN 等 expression；可序列化 JSON。

### 2.7 Relational executors

1. **DuckDB analytical executor**：大型分析預設路徑。
2. **SQLite executor**：fallback 與 correctness / benchmark 對照。

同一 AST 在兩者間必須維持相同語意。

### 2.8 Numerical Executor

Stage 4C 已建立：

- `NumericalTable` / `NumericalSection` typed contract。
- clustering input/output 保留 grain；assignment 可作 typed continuation。
- 完整 assignment 與 UI 顯示列數分離。
- explicit row safety guard；超過門檻就拒絕，不偷偷抽樣。
- K-means / Gaussian Mixture。
- Linear OLS / Binary Logistic。
- Bootstrap / confidence interval，resampling unit 明確指定。

#### CAP-04 Auto Cluster Count

**PASS / CLOSED。**

- K=1 合法。
- adaptive maximum K。
- minimum cluster size protection。
- candidate diagnostics：K、criterion、score、valid、selected、cluster sizes、minimum size、adaptive max、rejection reason。
- partition-specific Auto K。
- GMM 使用 BIC；K-means 使用 full-covariance GMM BIC 選 K，再以真實 K-means fit。
- synthetic K=1 / K=2 / tiny-cluster / partition cases PASS。
- Scherzer 2024 FC+SL natural acceptance：189 complete-feature pitches，K-means 與 GMM 均選 K=1。

### 2.9 Frontend / Stage 4D Output

共同基礎：雙語、XP/Windows 7 視覺、shared field checklist、Result Ordering、Job / Progress、table-first Analysis Result。

Output group 現在正式包含：

- `Visualization`
- `Analysis Library`
- `Analysis History`

Stage 4D 已交付：

- Single-chart Visualization workspace。
- current / recent / History / Saved Analysis / Saved Visualization sources。
- multi-section result selection。
- presentation metadata + provenance。
- line / bar / scatter / range / dumbbell / difference。
- built-in presets：Pitch Movement、Pitch Location、Release Point、Pitch Usage Trend、Cluster Map、Auto-K、Regression Coefficients、Confidence Interval、Cross-Level、Difference、Generic Time、Category Comparison。
- Full / Automatic / Manual sampling；Random / Every Nth；seed；sample disclosure。
- Saved Visualization Live / Frozen v2。
- User Presets。
- CSV / JSON / XLSX / Parquet full-result export。
- SVG / PNG / Copy Image figure export。
- HTML / PDF bilingual reports。
- latest-request-wins、source-section reset、saved-restore generation guard、standalone export style fidelity。
- Analysis History persistent `#ID`。
- Analysis Library Save / Load / Delete + single `編輯 Edit` dialog for Name / Notes。

Stage 4D 不建立第二套統計引擎；presentation 只消費正式 result。

---

## 3. 十個架構壓力測試需求

十題永久保留為 regression / acceptance requirements：

1. **三顆 Sweeper 的兩個極端球序** — PASS；EventPattern / Event Pattern Cohorts。
2. **武器庫組成 + FF 相對角色** — PASS；Arsenal Signature / Relative Pitch Selector。
3. **跨比賽時間序列** — PASS；conditional aggregate / consecutive-N / lead。
4. **動態參考球種** — PASS；Relative Pitch Selector / Annotation。
5. **巢狀分組 + 群內百分位** — PASS；Empirical Percentile + composable workflow。
6. **變動間距條件球序** — PASS；FollowEvent。
7. **跨 grain 比較** — PASS；typed cross-grain aggregate / join。
8. **Arsenal Set Difference** — PASS；Set Difference + Arsenal Change，NULL pitch type 排除。
9. **每位投手自己的 percentile threshold** — PASS。
10. **多階段選擇器 + 自動分群** — PASS；Multi-stage Cluster Comparison + CAP-04 Auto K。

詳細 Stage 4A–4C remediation 與歷史 closure 見 `docs/STAGE4_ACCEPTANCE_REPORT.md`；Stage 4D 最終狀態見 `docs/STAGE4D_IMPLEMENTATION_STATUS.md`。

---

## 4. 開發階段 — 最終狀態

### 資料基礎 — 第一版完成

Statcast fetch、raw archive、SQLite schema evolution、upsert、backfill/resume/retry、incremental update、scheduler、rebuild、integrity、fast status、live E2E、progress 均已建立。Supplemental Pitch3D / spin aggregate 第一版資料管理也已完成。

### 第一階段：分析核心骨架 — 完成

Grain model、Typed AST、filter/aggregate/project/sort/limit/set、ranking、semantic registry、SQLite compiler/executor、planner boundary、serialization。

### 第二階段：高階關聯分析 — 完成

Window / lag / lead / rank / percentile、cross-grain Join、CollectSet / arsenal signature、EventPattern、FollowEvent、pitch usage、role ranking、tie handling、stress tests。

### 第三階段：正式使用介面 — 完成第一版

分析頁 + Data Management、雙語 XP/7 UI、table result、backfill progress、共用欄位控制、共用排序／結果顯示／分析進度。

### Stage 4A：效能、快取與分析工作區 — **PASS / CLOSED**

Persistent cache、Analysis History、Saved Analysis、state DB、DuckDB analytical path、full-data benchmark、large-result safety。

### Stage 4B：完整關聯 workflow — **PASS / CLOSED**

Composable relational workflow、Arsenal Signature、Relative Pitch Selector/Annotation、Event Pattern Cohorts、Empirical Percentile、Research Workflow UI。

### Stage 4C：Numerical Executor — **PASS / CLOSED**

Typed numerical boundary、clustering、regression、bootstrap、Multi-stage Cluster Comparison、CAP-04 Auto K。

### Stage 4D：Output / Visualization — **PASS / CLOSED**

原始人工驗收 **1–15 全數通過**。包含 sampling、Saved Visualization、User Preset、CSV/JSON/XLSX/Parquet、SVG/PNG、HTML/PDF、Analysis Library / History，以及驗收期間發現的 race/lifecycle/export/report defects remediation。

其中 #12 實際以 18,887-row Level Comparison result 測試，在 Visualization 只抽樣 50 rows 時，四種資料格式仍完整輸出 18,887 rows；Parquet 另有 18,887-row DuckDB round-trip regression test。

驗收後新增的 Analysis Library Name/Notes `編輯 Edit` 亦已人工驗證通過。

**目前沒有 Stage 4E 或其他已定義的下一階段。** 新需求先視為一般 maintenance / backlog，除非未來明確重新定義專案階段。

---

## 5. 大型資料效能實測

2026-08-26 完整本機資料庫：

- pitch rows：**9,192,548**。
- benchmark：2026 → Group By `pitch_type` → Count + Average `release_speed` → computed metric sort。
- warmed SQLite median：約 **2.31–2.35 s**。
- warmed DuckDB median：約 **0.07–0.08 s**。
- 初次 DuckDB mirror prepare：約 **245.5 s**，與 query timing 分開報告。

結論：大型互動 relational analysis 以 DuckDB 為 primary；SQLite 保留 source-of-truth / fallback / correctness 角色。分鐘級正式分析仍視為 profiling target，不以資料量直接合理化。

---

## 6. 長期維護／Backlog

這些是未來可能工作，**不是已排定的下一階段**。

### 資料與可靠性

- 持續維護 2015→現在 full Statcast dataset。
- 記錄 SQLite / DuckDB / raw archive / analysis_state / supplemental storage 實際容量與成長。
- 季中長期 Auto Update 驗證。
- 真實 Savant historical revision → SQLite update → DuckDB refresh 驗證。
- raw snapshot retention / compaction 僅在量測後設計。

### 多資料源分析候選

若未來真的需要，再設計：

- Statcast + Pitch3D pitch-level join。
- player × season × pitch_type spin aggregate 的 grain-aware join。
- source selector / provenance / field-conflict UX。
- DuckDB supplemental analytical integration。
- multi-source cache / data revision contract。

### 分析與統計

- 細分統計持續顯示 sample size。
- uncertainty 明確定義計算單位。
- ties、NULL、低樣本、歷史欄位缺失政策保持一致。
- DuckDB / SQLite AST parity 持續回歸。
- numerical methods 持續增加由真實棒球問題導出的 known-answer / synthetic tests。
- Auto K 若更換 selector，必須重跑 K=1、K=2、tiny cluster、partition-specific 與 Scherzer natural acceptance。

### Presentation / UX

- 依真實使用修正難懂操作，不為了「看起來功能多」堆設定。
- 中英永久並列、XP/7 視覺方向保持。
- 共用互動維護單一元件／單一 contract。
- current Visualization 維持 single-chart；multi-chart/dashboard 僅保留資料模型相容性，沒有目前開發承諾。
- current report 是單一 source/section + 一張 Visualization 的固定正式報告；多圖 report composition / free-form designer 不屬現行已實作功能。

### 效能／工程治理

- canonical benchmark 永久保留；mirror build 與 query time 分開。
- cache 必須受 `data_revision` / format version 約束。
- numerical / visualization / export safety limit 不允許 silent truncate。
- 長時間操作需 progress；無可信百分比時使用 indeterminate + elapsed。
- `main` 維持可工作正式版；branch → tests → PR → CI → merge。
- live Savant smoke test 保留。
- 重要架構或產品邊界變更同步更新本文件。

---

## 7. 不應被誤解的決策

1. **SQLite 是 Statcast source of truth；DuckDB 是 analytical mirror。**
2. **DuckDB executor 是 relational executor；Numerical Executor 是另一條正式 typed 計算路徑。**
3. **AST / workflow 是分析契約，不要求所有運算轉 SQL。**
4. **Numerical output 仍有 grain；cluster assignment 不能變成無身分 dataframe。**
5. **Baseball Semantic Registry / workflow helpers 是便利層，不是封閉 domain model。**
6. **十個壓力測試永久保留為 regression / acceptance requirements。**
7. **Stage 4A–4D 均已正式完成並 CLOSED；目前沒有 Stage 4E。**
8. **完整資料 benchmark 已完成，不再列為待辦。**
9. **初次 DuckDB mirror build 是 one-time preparation，不和 warmed query timing 混報。**
10. **分析進度必須誠實；無可信百分比時不偽造。**
11. **快取不是 SQL 字串快取；使用 analysis payload + data revision 等高層 contract。**
12. **Bootstrap 不默認逐球獨立；resampling unit 必須明確指定。**
13. **數值／完整視覺化／匯出 safety threshold 不是 silent sampling。**
14. **Auto K 必須允許 K=1。**
15. **Supplemental data 已保存但目前不得被既有 Statcast analyzer 偷偷讀取。**
16. **Pitch3D trajectory polynomial 不是 raw Hawk-Eye frame tracking。**
17. **Spin aggregate 不是逐球 seam pose。**
18. **Stage 4D presentation 不新增分析統計邏輯。**
19. **Visualization sampling 不得改變 full-result export population。**
20. **第一版 Visualization 採單圖模式，但不得把資料模型寫死成永遠只能單圖。**

---

## 8. 更新規則

以下情況必須更新本文件：

- 新增／取消／重新定義確定要做的功能。
- 架構邊界改變。
- 某壓力測試正式支援狀態或語意改變。
- Numerical / relational / presentation contract 改變。
- full-data benchmark 改變效能策略。
- 新增持久資料層或多資料源分析能力。
- 未來若真的定義新的專案階段，其開始／完成狀態。

本文件不凍結所有 UI 細節；它確保架構、驗收題、已完成能力與真正 backlog 不會隨聊天上下文遺失。
