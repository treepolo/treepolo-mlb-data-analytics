# treepolo MLB Data Analytics — 架構、驗收題目與未來規劃

本文件是本專案的正式長期規劃來源，用來保存「為什麼這樣設計、哪些需求必須支援、哪些能力尚未完成、未來要往哪裡做」。

除非後續明確修改本文件，聊天中的臨時討論不應取代這裡的既定方向。

---

## 1. 產品目標

`treepolo MLB Data Analytics` 是一套以 Baseball Savant / Statcast 逐球資料為基礎的 MLB 分析應用。

核心不是只提供幾個固定統計頁，而是讓使用者可以組合高度細緻的棒球問題，例如：

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
表格／未來圖表與其他輸出
```

對高度細分的比例或統計，應始終讓使用者看得到樣本數；未來可再加入信賴區間等不確定性資訊。

---

## 2. 完整系統架構

目前確立的核心架構是 **Typed Analysis AST + Execution Planner**，而不是讓前端直接組 SQL，也不是再額外維護一套與 AST 重複的 Query Spec。

```text
Baseball Savant / Statcast
          │
          ▼
原始資料保存 Raw Archive
          │
          ▼
SQLite 正規化逐球資料庫
          │
          ▼
前端分析建構器
          │
          ▼
分析運算樹 Typed Analysis AST
          │
          ▼
分析資料層級檢查 Grain Validation
          │
          ▼
執行規劃器 Execution Planner
          │
     ┌────┴──────────────┐
     ▼                   ▼
資料庫執行器          數值計算執行器
SQLite / future      Python numerical
DuckDB optional      clustering / regression /
                     resampling / models
     └────┬──────────────┘
          ▼
      結果結構
          │
          ▼
     CLI / Web UI
```

### 2.1 資料層

資料來源目前為 Baseball Savant Statcast CSV。

原始層：

- 每次成功取得的 Savant CSV 原封保存。
- 以 gzip 壓縮保存於 `data/raw/年/月/`。
- 保存 SHA-256、抓取時間、日期範圍等 manifest。
- 相同日期範圍且內容完全相同的快照去重。

正規化層：

- SQLite：`data/statcast.sqlite3`。
- 保留 Savant 回傳的全部合法欄位，不硬編碼固定欄位數。
- 新增上游欄位時自動擴充 schema 並留下 schema event。
- 舊年份不存在的欄位允許為 NULL。
- 逐球自然鍵：`game_pk + at_bat_number + pitch_number`。
- 缺自然鍵資料不靜默丟棄，而以 deterministic fallback ID 保存並在 integrity report 中揭露。
- 增量抓取採 idempotent upsert；同一顆球不重複，Savant 事後修訂可更新既有資料。

### 2.2 同步／資料維護

已建立：

- 2015 起歷史回補。
- 預設 5 個日曆日一個下載區段。
- Resume：跳過已成功完成的相同日期區段。
- Retry Failed：只重跑已記錄的失敗區段。
- Incremental Update：更新新增日期並重抓最近修訂窗口。
- Auto Update 開關與 scheduler。
- Rebuild：從 raw archive 重新建立 SQLite。
- 資料完整性檢查。
- 歷史回補即時進度：總區段、已完成、目前區段、接收逐球數、失敗數、耗時、粗略 ETA。

### 2.3 分析資料層級（Grain）

每個分析節點都必須知道目前資料代表的層級，避免把不同層級的數值錯誤混用。

典型層級包括：

- Pitch：逐球。
- Plate Appearance：打席。
- Game：比賽。
- Pitcher。
- Pitcher × Time Period。
- Pitcher × Pitch Type × Time Period。
- Arsenal：武器庫。
- Cohort：樣本群組。
- Sequence / Pattern：球序模式。

目前實作採可組合的 grain keys，而不是把所有可能層級做成死板 enum。

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

AST 可序列化為 JSON，作為前端與分析核心之間的正式資料契約，也為未來儲存分析設定與 AI 產生分析提供基礎。

### 2.5 執行規劃器

正式方向：

```text
分析需求
↓
分析運算樹
↓
執行規劃器
↓
判斷每一段運算交給哪個執行器
```

目前只有 SQLite 關聯資料庫執行器。

未來不是要求所有 AST 節點都硬編成 SQL；分群、迴歸、重抽樣等運算可以由數值計算執行器負責，再與關聯結果組合。

DuckDB 可作為未來大型關聯分析的選配執行器，但不是目前必要核心。

### 2.6 棒球語意層

棒球 Semantic Registry 只能是一層薄便利定義，不應變成限制分析能力的核心模型。

目前例子：

- 四縫線 fastball
- sweeper
- changeup
- breaking ball
- fastball family
- 揮空
- 揮棒
- called strike
- 好球帶
- 右投對右打

原則：常用棒球概念可直接重用，但使用者仍能回到底層欄位與條件組合。

### 2.7 前端

前端原則：

- 所有「對使用者有意義的分析意圖」應有前端表達方式。
- 不把 Join、Window、Set Difference 等資料庫／程式底層概念原樣暴露給使用者。
- 底層技術差異若會改變分析語意，前端要翻成使用者真正需要決定的選項，例如「保留並列」與「固定選出唯一順位」。
- UI 中英文永久並列，不做語言切換；專有名詞也必須有中文名稱。
- 視覺風格明確採 Windows XP + Windows 7 年代桌面軟體語言，復古感是目標的一部分，而不是要被現代化消除。
- 第三階段刻意維持薄前端：先操作既有後端能力並忠實顯示表格，不在前端另造分析邏輯。

目前已有資料管理與多種分析頁；圖表等視覺化仍刻意延後。

---

## 3. 十個架構壓力測試需求

這十題是用來檢驗架構是否真的能承受未來複雜分析，而不是只會做固定報表。它們應長期保留作為驗收題目。

### 1. 三顆 Sweeper 的兩個極端球序

樣本宇宙：一個打席內總共恰好三顆 Sweeper，而且最後一球也是 Sweeper。

比較：

- A：三顆 Sweeper 完全連續。
- B：三顆 Sweeper 彼此完全不相鄰。

只比較兩個極端組；部分相鄰的打席可以刻意排除，不要求兩組涵蓋全部樣本。

分析對象是該打席的第三顆 Sweeper，也就是最後一球。

需要能力：same-PA partition、pitch order、exact count、last-event predicate、Nth matching event、adjacency / non-adjacency classification、選取特定事件後比較其欄位或結果。

**狀態：第二階段關聯分析能力已覆蓋。**

### 2. 武器庫組成 + 四縫線相對角色

自動依武器庫組成分組：

- 某球種使用率高於指定門檻（例：>5%）才算進武器庫。
- 擁有完全相同 qualifying pitch-type set 的投手歸為同一武器庫組，例如 `{FF, SL, CH}`。
- 武器庫組型應由資料自動發現，不是人工預先列舉。

每個武器庫組內再分：

- A：四縫線是最高使用率球種。
- B：四縫線不是最高使用率球種。

接著把 FF 與「相對角色球種」比較，例如：

- 第二常用球種。
- 排除 FF 後，在指定指標下表現最好的球種。

最後比較 A/B 兩群的 FF 相對差值。

需要能力：usage、set signature、dynamic grouping、pitch-role selector、rank、ties、relative comparison。

**狀態：第二階段核心能力已覆蓋；實際分析仍需在前端逐步把完整使用流程做得更自然。**

### 3. 跨打席／跨比賽時間序列

若某投手某球種使用率連續三場上升，檢查第四場的球速、Stuff 類指標或揮空率是否改變。

需要能力：pitcher-game aggregation、time ordering、lag / lead、rolling / consecutive-period logic、下一期結果比較。

**狀態：lag / lead 與時間序列基礎已存在；完整「連續 N 期條件」未做成專用高階操作，未來可補 helper 或更完整 window-frame 能力。**

### 4. 動態參考球種

每位投手先找出「使用率最高的非 FF 球種」，再把 FF 與該球種比較球速、揮空率、xwOBA 等，最後依差值衍生分群或 cohort。

需要能力：per-entity argmax / role selector、排除指定球種、within-pitcher comparison、derived cohort。

**狀態：第二階段關聯分析能力已覆蓋主要需求。**

### 5. 巢狀分組 + 群內百分位

先依武器庫組成分組，再在每個武器庫組內依 FF 使用率百分位把投手分成高／中／低群，最後比較各群 changeup 表現。

需要能力：group → within-group percentile → regroup → aggregate。

**狀態：第二階段已有 empirical percentile、group、join 等基礎；完整產品操作流程仍可再強化。**

### 6. 變動間距的條件球序

每次出現 Sweeper 後，找接下來最多 3 球內第一顆再次出現的 Sweeper，並依兩顆 Sweeper 中間是否曾出現 FF 分組，比較第二顆 Sweeper 結果。

需要能力：bounded lookahead、first matching subsequent event、variable gap、between-event classifier。

**狀態：第二階段 `FollowEvent` 已覆蓋。**

### 7. 跨資料層級比較

先算投手整季 FF 平均球速，再找單場 FF 平均球速低於整季平均至少 1.5 mph 的比賽，最後分析那些比賽中第三輪對打線時的 breaking-ball 使用率。

需要能力：season → game → PA / pitch 的跨層級聚合、join、derived predicate、再回到更細層級分析。

**狀態：第二階段已有 typed cross-grain join 與聚合基礎；第三輪打線等更高階棒球語意可後續補成便利操作。**

### 8. 動態集合關係：武器庫變化

比較同一投手上半季與下半季武器庫，找出後半季新進入使用率門檻的球種，再分析新增球種後其他球種的使用率或表現變化。

需要能力：time-period cohort、arsenal sets、set difference、role change、前後期比較。

**狀態：第二階段集合差與武器庫建構已覆蓋；第三階段已有 Arsenal Change 前端。**

### 9. 每位投手自己的樣本門檻

把「高球速 FF」定義成該投手自己的 FF 球速第 80 百分位以上，再比較高／非高球速樣本的數量與結果。

需要能力：per-entity percentile、sample-derived threshold、derived predicate。

**狀態：第二階段 empirical percentile 已覆蓋；第三階段已有 Individual Threshold 前端。**

### 10. 多階段選擇器 + 自動分群

先在某武器庫組內，找出該組整體表現最佳的非 FF 球種；接著在擁有此球種的每位投手內，對該球種的 movement / velocity / release / spin 等特徵自動分群，選出每位投手表現最佳的 movement cluster，再與 FF 比較。

需要能力：group-level selector → entity-level selector → multivariate clustering → nested comparison。

「分群」指依多個連續特徵自動找出不同子型，不是預先人工切區間。可考慮：

- K-means 類中心分群。
- Gaussian Mixture 類機率混合模型。
- DBSCAN 類密度分群。
- HDBSCAN 類階層式密度分群。

**狀態：前半段關聯選擇器已有基礎；真正自動分群尚未實作，明確屬於第四階段數值計算能力。不得把第二階段測試數量「10 題」誤解為此題已全部完成。**

---

## 4. 開發階段與目前狀態

### 資料基礎（已完成系統實作；完整本機歷史人口仍在實際回補）

已完成：

- Baseball Savant 下載器。
- raw archive。
- SQLite 儲存與 schema evolution。
- idempotent upsert。
- historical backfill / resume / retry。
- incremental update。
- auto update。
- rebuild。
- integrity report。
- live Savant E2E / CI。
- 歷史回補進度顯示。

尚需完成的實際驗證：

- 在真實本機把 2015 至現在完整資料回補完畢。
- 完整資料量下檢查實際檔案容量、下載耗時、索引大小。
- 完整資料量下重新跑代表性分析，取得真實查詢時間。
- 長時間 scheduler / correction refresh 的實際運行驗證。

### 第一階段：分析核心骨架（已完成）

- Grain model。
- Typed AST。
- filter / aggregate / project / sort / limit / set basics。
- ranking basics。
- semantic registry。
- SQLite compiler / executor。
- execution planner boundary。
- serialization。

### 第二階段：高階關聯分析（已完成）

- Window / lag / lead / rank / cumulative percentile。
- cross-grain Join。
- deterministic CollectSet / arsenal signature。
- EventPattern。
- FollowEvent。
- pitch usage builder。
- arsenal builder。
- pitch-role ranking。
- tie-safe dense rank / explicit deterministic row-number mode。
- acceptance tests covering the relational stress scenarios。

### 第三階段：正式使用介面（第一版已完成，後續仍可改善）

目前已完成：

- 本機 Web UI。
- Windows XP + Windows 7 視覺風格。
- 中英文永久並列。
- Data Management。
- Basic Analysis。
- Sequence Pattern。
- Follow-up Event。
- Pitch Arsenal。
- Pitch Role。
- Temporal Comparison。
- Individual Threshold。
- Level Comparison。
- Arsenal Change。
- table-only 結果。
- 歷史回補進度條。

第三階段原則仍是「薄前端」：不在前端重做分析邏輯、不先加圖表與大量產品功能。

### 第四階段：進階計算與產品完善（尚未開始）

第四階段不是單一功能，而是把目前以關聯分析為主的系統擴展成完整的進階分析平台。

#### 4.1 數值計算執行器

建立 Execution Planner 的第二條正式執行路徑：

```text
AST / analysis plan
        ↓
Execution Planner
        ↓
Numerical Executor
```

需求：

- 從關聯執行器接收乾淨的分析中間資料。
- 執行非 SQL 最適合的計算。
- 回傳具明確欄位與 grain 的結果。
- 可再交回關聯流程或直接形成最終結果。
- 不把 Python dataframe 當成新的無型別「萬用層」，仍要維持分析契約與資料層級。

#### 4.2 自動分群

優先滿足壓力測試 #10。

初期應支援：

- 選擇分群特徵。
- 特徵標準化。
- K-means 類方法。
- Gaussian Mixture 類方法。
- DBSCAN / HDBSCAN 類方法視依賴與實際需求評估。
- cluster label 回接原始 pitch / entity。
- 群數或超參數的可重現設定。
- 每群樣本數與中心／摘要。

需要避免「分群只是畫圖」：cluster 必須能成為後續 filter、group、selector 的正式資料。

#### 4.3 迴歸與統計模型

方向：

- 線性／廣義線性模型等基礎回歸。
- 可指定 dependent / independent variables。
- entity / time grouping 視需求逐步擴充。
- 結果輸出係數、樣本數、必要統計量，而不是只回傳一個預測值。

實際模型範圍待有具體棒球問題時再收斂，不預先堆大量無需求模型。

#### 4.4 重抽樣／Bootstrap

用途：

- 比例、平均、差值等指標的不確定性估計。
- 細分樣本下的 confidence interval。
- 兩組差異的重抽樣分布。

必須能明確指定重抽樣單位，避免逐球資料有群聚結構時錯把每顆球視為完全獨立。

#### 4.5 更完整的視窗／序列能力

候選：

- explicit rolling window frame。
- 連續 N 期上升／下降的高階 helper。
- first / last / nth value 類一般化能力。
- 更複雜的跨 PA / game sequence。

是否增加新 AST node，應依無法乾淨組合現有節點的實際需求決定。

#### 4.6 大型資料效能

完整 2015+ 資料建立後再依 profiling 決定，而不是先猜。

候選：

- query profiling。
- SQLite index 調整。
- intermediate result caching。
- AST / compiled-query cache key。
- repeated analysis result cache。
- 必要時加入 DuckDB executor。
- 大型分析的 streaming / materialization strategy。

原則：先量測完整資料上的真實瓶頸，再優化。

#### 4.7 產品完善

在核心分析正確性與效能穩定後再做：

- 圖表。
- 更多結果視覺化。
- 匯出。
- 儲存／載入分析 AST。
- 複製既有分析後修改。
- 更完整的分析歷史／preset 管理。
- 前端 sample-size 與錯誤提示的進一步強化。
- 未來 AI 產生 AST（只產生分析規格，不讓 AI 直接寫任意 SQL）。

---

## 5. 其他未來待辦

以下項目不一定全部屬於第四階段，但必須保留：

### 資料與可靠性

- 完成第一次 2015→現在的 persistent full backfill。
- 記錄完整資料庫最終磁碟容量與 raw archive 容量。
- 驗證季中長期 auto-update。
- 驗證 Savant 真實歷史修訂造成 row update 的案例。
- 檢視下載區段大小是否需依歷史實測動態調整；目前 5 天是可靠性折衷，不是 Savant 強制規格。
- 若長期 raw snapshot 成長過快，再設計 retention / compaction；未量測前不先刪原始資料。

### 分析正確性

- 所有細分統計結果持續顯示 sample size。
- 未來加入 confidence interval / uncertainty 時，明確定義計算單位。
- 對 ties、NULL、低樣本、球種分類變更、歷史欄位缺失建立一致政策。
- 增加更多從真實棒球研究問題導出的 acceptance tests。

### 前端／使用體驗

- 繼續實際使用第三階段 UI，記錄難懂或不自然的操作。
- 保持中英永久並列。
- 保持 XP / 7 復古桌面軟體視覺方向；不改造成一般現代 SaaS 卡片風。
- 不直接暴露 Join、Window、Set Difference 等底層名稱，除非未來有真正需要給進階使用者的 expert mode。
- 增加說明／tooltip / 使用教學，尤其是高階球序與球種角色功能。
- 圖表目前延後，等表格分析工作流驗證完成後再設計。
- 評估儲存、載入、複製、修改分析設定的正式 UI。
- 未來若加入圖表，圖表只消費分析結果，不建立另一套統計邏輯。

### 工程治理

- GitHub `main` 維持可工作的正式版本。
- 功能以 branch → tests → PR → CI → squash merge 為預設流程。
- 真實 Savant smoke test 繼續保留，避免只靠 synthetic tests。
- 重要架構變更要同步更新本文件，不只留在聊天紀錄。

---

## 6. 不應被誤解的幾個決策

1. **SQLite 是目前執行器，不是整個分析架構。** 未來可以增加 DuckDB 或 numerical executor，而不必重寫前端分析語意。
2. **AST 是分析契約，不是要求所有東西都轉 SQL。**
3. **Baseball Semantic Registry 是便利層，不是封閉式棒球 domain model。**
4. **前端不需要替每個後端底層 operator 做一顆按鈕。** 使用者有意義的分析意圖才應直接出現在 UI。
5. **十個壓力測試不代表十題都已完全實作。** 第 10 題自動分群明確未完成；部分題目的高階 convenience operation 仍可在第四階段補強。
6. **第三階段不做圖表是刻意縮小範圍，不代表永久不要圖表。**
7. **完整 2015+ 歷史資料程式已能回補，但「完整本機資料集」是否完成要以實際 persistent backfill 跑完為準。**

---

## 7. 更新規則

後續每當出現以下情況，應更新本文件：

- 新增一個確定要做的未來功能。
- 某個待辦被取消或改變定義。
- 架構邊界改變。
- 某個壓力測試被正式完整支援。
- 第四階段拆出新的正式子階段。
- 真實完整資料量測後，效能策略有所改變。

本文件的目的不是凍結所有產品細節，而是確保重要的未來工作與設計理由不會只存在於單一聊天上下文中。
