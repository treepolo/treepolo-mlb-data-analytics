# RESEARCH-01 — Hawk-Eye / Baseball Savant 球體姿態與縫線方向資料研究

狀態：**研究完成；目前沒有找到可從已檢查的公開 Baseball Savant / MLB 瀏覽器介面穩定取得的逐球 seam-orientation / absolute ball-pose 原始資料。**

本文件記錄研究過程、已確認資料層級、已排除的公開路徑，以及後續產品決策邊界。它不把未文件化的網站行為視為穩定 API 契約。

## 1. 研究目標

RESEARCH-01 要回答：

- Baseball Savant / Hawk-Eye 是否有逐球真實球體／縫線姿態資料；
- 公開 Savant client 是否會收到這些資料；
- 若會，資料來自哪個 endpoint、如何以逐球識別碼對應；
- 資料表示法是否為 3D spin vector、seam orientation、phase、quaternion、rotation matrix、axis-angle、landmark 或逐幀 pose；
- 是否能穩定批次取得；
- 與既有 Statcast CSV / Pitch3D 資料的關係。

研究判準刻意區分：

1. Hawk-Eye 內部是否量得到；
2. Savant 是否顯示聚合後資訊；
3. 公開瀏覽器 client 是否能取得 aggregation 前的逐球 seam / ball pose。

本研究主要解決第 3 點。

## 2. 結論摘要

### 2.1 Hawk-Eye 上游確實具有比公開 Statcast CSV 更高維度的旋轉資訊

公開技術資料與現行 Baseball Savant 球員頁都支持 Hawk-Eye 能直接量測旋轉方向；球員頁的 `serverVals.spinAxis` 還包含用於 3D 旋轉呈現的三維向量與 orientation 相關欄位。

但「MLB / Hawk-Eye 上游具有資料」不等於「公開 client 可取回 aggregation 前的逐球 seam pose」。

### 2.2 Baseball Savant 球員頁公開的是 `player × season × pitch_type` 聚合資料

球員頁初始 HTML 中可見 `serverVals.spinAxis`。代表性欄位包括：

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

`image_spin_x/y/z` 幾乎構成單位 3D 向量，前端將這些欄位交給代表性棒球旋轉動畫。

目前觀察到的 row grain 是：

```text
player
× season
× pitch_type
```

沒有 `play_id` / `game_pk` / `pitch_number` 等逐球識別。

### 2.3 已檢查的公開 Savant client 路徑都沒有暴露逐球 seam / absolute ball pose

研究已實際檢查：

- 球員頁初始 HTML；
- 球員頁 JavaScript bundle；
- 真實 Chrome page-load Network；
- Spin Direction leaderboard HTML / JS / Network；
- Spin Axis SVG endpoint；
- `sporty-videos?playId=...` 真實瀏覽器 Network；
- `/gf?game_pk=...` gamefeed；
- `/player-services/pitches-seasonal`；
- `/player-services/statcast-pitches-breakdown`；
- `/savant/api/v1/spin-direction-pitches`；
- `/savant/api/v1/spin-direction-by-pitcher`；
- `/leaderboard/spin-axis-by-pitcher`；
- 標準 Statcast Search CSV；
- Pitch3D `/app/pitch-data/{player_id}`；
- Savant 前端 endpoint literals / 高訊號字串；
- 公開 GitHub / 網頁對內部欄位名與可能 route 的搜尋。

在這些公開面上都沒有找到能回傳以下逐球資料的穩定 route：

```text
per-pitch image_spin_x/y/z
per-pitch image_orientation_angle
seam phase
seam landmarks
quaternion
rotation matrix
absolute ball pose
orientation time series
```

因此目前最精確的結論是：

> **MLB / Hawk-Eye 上游具有 seam-orientation / richer optical spin data；Baseball Savant 對外暴露了衍生與聚合結果，但已檢查的公開瀏覽器資料面沒有暴露 aggregation 前的逐球 seam-orientation / absolute ball-pose layer。**

這是「目前未找到公開取得方式」；不代表能證明 MLB 內部不存在其他受控 feed。

## 3. 球員頁資料鏈

### 3.1 `serverVals.spinAxis` 是 server-rendered 初始資料

真實 Chrome Network 顯示球員頁 page load 的主要相關 request 包括：

```text
/player-services/histogram
/player-services/pitcher-running-game
/player-services/statcast-pitches-breakdown
/savant/api/v1/trending-players
```

沒有看到球員頁載入後再透過 XHR / fetch 呼叫一個 raw seam-orientation endpoint 來建立 `serverVals.spinAxis`。

因此目前可觀察到的資料流是：

```text
Savant server
    ↓
先形成 spinAxis 聚合資料
    ↓
HTML / inline serverVals
    ↓
player-page JS
    ↓
代表性 pitch-type 3D spin animation
```

### 3.2 前端 `Wce(...)` 的輸入是 aggregate row

目前 player bundle 中，棒球動畫使用：

```text
image_spin_x
image_spin_y
image_spin_z
image_orientation_angle
```

而呼叫端從 `serverVals.spinAxis.find(...)` 依 season + pitch type 找一列後交給該 renderer。

同一球員頁另一條逐球資料 `serverVals.statcastPitches` 則來自 `/player-services/pitches-seasonal`，兩者是不同資料流。

## 4. 已檢查的 Savant spin endpoints

### 4.1 `/savant/api/v1/spin-direction-pitches`

可回傳聚合欄位，例如：

```text
n_pitches
active_spin
hawkeye_measured
movement_inferred
image_spin_x
image_spin_y
image_spin_z
image_orientation_angle
```

但目前觀察到的是 pitcher/year/pitch-type aggregate。

曾針對已知逐球測試多種 selector / detail 形式，包括：

```text
play_id
playId
pid
game_pk
gamePk
game_date
date
type=details
type=detail
detail=true
raw=true
group_by=play_id
groupBy=play_id
min=0
```

未觀察到 response 被切成逐球 grain；detail 子路徑也未形成可用逐球 endpoint。

### 4.2 `/savant/api/v1/spin-direction-by-pitcher`

Spin Direction 前端 JavaScript 實際使用此 endpoint。

回傳內容是 clock-angle bucket distribution，包含類似：

```text
rn_clock
infer_n_pitches
meas_n_pitches
```

以及球種次數分布。

它仍是 aggregate distribution，不含逐球識別或 seam pose。

### 4.3 `/leaderboard/spin-axis-by-pitcher`

真實 Chrome Network 在 Spin Direction leaderboard 載入時會出現：

```text
/leaderboard/spin-axis-by-pitcher?pitcher=...&type=inferred&pov=Pit
/leaderboard/spin-axis-by-pitcher?pitcher=...&type=measured&pov=Pit
```

直接 GET 後的 response：

```text
HTTP 200
Content-Type: image/svg+xml
```

它是動態生成的 SVG spin-axis 圖，不是 raw JSON。沒有觀察到：

```text
play_id
pid
game_pk
pitch_number
image_spin_x/y/z
image_orientation_angle
hawkeye_measured
```

等逐球資料欄位。

## 5. 已檢查的逐球資料來源

### 5.1 `/player-services/pitches-seasonal`

Ohtani 2026 實測取得 1335 顆逐球。

包含：

```text
pid
pitch type
velocity
date / location / pitch metadata
```

但 0 顆 row 帶有：

```text
spin_axis
image_spin_x/y/z
image_orientation_angle
hawkeye_measured
movement_inferred
```

所以它可提供逐球識別與簡化資料，不能接回球員頁 aggregate animation 的 seam/spin-pose 上游。

### 5.2 `/player-services/statcast-pitches-breakdown`

真實 player page request：

```text
/player-services/statcast-pitches-breakdown
?playerId=660271
&position=1
&hand=
&pitchBreakdown=pitches
&timeFrame=yearly
&season=
&pitchType=
&count=
&gameType=
&updatePitches=true
```

response 定義 `window.serverVals.pitchDetails`，但內容仍是 pitch-type/year aggregate，沒有逐球 pose identifiers / image-spin fields。

### 5.3 `sporty-videos?playId=...`

以已知真實 `playId` 開啟頁面並抓 Chrome Network，沒有看到額外 seam / orientation / ball-pose data endpoint。

## 6. 標準 Statcast Search CSV：`spin_axis` 與 `hawkeye_measured`

標準 Statcast CSV 已提供逐球 `spin_axis`，但它不能直接當作球員頁 `hawkeye_measured` 的同值欄位。

為了驗證兩者關係，使用 Ohtani 2026 regular season 全部 1335 顆逐球：

1. 依 pitch type 收集逐球 `spin_axis`；
2. 使用 circular mean；
3. 與球員頁 embedded `serverVals.spinAxis` 同球種的 `hawkeye_measured` 比較；
4. 同時檢查反向座標 `(360° - circular_mean(spin_axis)) mod 360°`。

結果：

| Pitch type | CSV n | embedded n | circular mean `spin_axis` | `360-mean` | `hawkeye_measured` | reverse diff |
|---|---:|---:|---:|---:|---:|---:|
| CU | 133 | 132 | 37.764° | 322.236° | 323.065° | 0.829° |
| FF | 602 | 602 | 216.396° | 143.604° | 146.573° | 2.969° |
| FS | 114 | 114 | 234.838° | 125.162° | 118.539° | 6.623° |
| SI | 69 | 69 | 218.039° | 141.961° | 144.081° | 2.120° |
| SL | 15 | 15 | 75.679° | 284.321° | 289.368° | 5.047° |
| ST | 393 | 393 | 66.481° | 293.519° | 291.028° | 2.490° |

FC 在 CSV 中有 9 球，但 player-page 2026 embedded aggregate 沒有對應 row，因此未列入差值統計。

六個可比球種：

```text
mean reverse difference ≈ 3.346°
max reverse difference  ≈ 6.623°
```

這支持：

- 公開逐球 `spin_axis` 與 `hawkeye_measured` 描述高度相關的旋轉軸方向資訊；
- 座標／觀察方向慣例不同；
- `(360 - circular mean(spin_axis))` 在這個樣本中與 aggregate measured direction 很接近；
- 但差值不是 0，且目前沒有公開 API 契約保證這個簡單轉換等同於 Savant 的 aggregate calculation。

因此：

> **不可把逐球 `spin_axis` 原值或 `360-spin_axis` 衍生值標成逐球 `hawkeye_measured`；更不能把它當作 seam phase / absolute ball pose。**

這項實證主要用來界定資料層級，而不是創造一個假裝存在的 raw Hawk-Eye 欄位。

## 7. Pitch3D `/app/pitch-data/{player_id}`

Pitch3D 是另一個很有價值、但不同目的的資料源。

### 7.1 格式與逐球識別

```text
/app/pitch-data/{player_id}
```

回傳 UTF-8 CSV，每列一球，可見：

```text
game_pk
play_id
pitcher
game_date
api_pitch_type
```

未來若正式整合，`game_pk + play_id` 可作為與其他逐球資料對接的重要 key。

### 7.2 連續 3D flight trajectory

Pitch3D CSV 包含：

```text
polynomial_x_1..3
polynomial_y_1..3
polynomial_z_1..3
api_end_time
api_plate_time
release-position / break / velocity fields
```

可用來重建連續 `x(t), y(t), z(t)` 球路。

它應描述 MLB/Statcast 處理後供 Pitch3D 重建的連續軌跡模型；不要標成 Hawk-Eye 相機逐幀原始座標。

### 7.3 沒有 seam / ball pose

完整 header 掃描未找到：

```text
seam orientation
seam phase
ball orientation
quaternion
rotation matrix
axis-angle
pose samples
seam landmarks
```

Pitch3D client 目前也沒有依逐球 seam pose 去旋轉有縫線棒球模型的資料流。

### 7.4 Endpoint behavior research

已實測：

- long-career MLB pitchers 的可用資料常從約 2017 起；這是觀察結果，不是官方 API 保證；
- `year=` / `season=` 未觀察到 server-side filtering；
- 超過 22,000 球的實例仍一次回完整 CSV；沒有觀察到 pagination / row cap；
- `page` / `limit` / `offset` 測試沒有切分 response；
- MLB `/app/pitch-data/{player_id}` 與 `?minors=1` 為分離資料集合；測試時 schema 相同；
- 即時重抓可 byte-for-byte 相同，且 response 有 cache headers；仍應把資料源視為未來可能修訂。

若未來正式整合，已決定的資料原則是：

```text
完整保存原始 Pitch3D CSV
→ 不裁欄位
→ MLB / MiLB 分離抓取與進度入口
→ 獨立 Pitch3D table / provenance
→ 以逐球 key 關聯既有 Statcast
→ 重抓整個 player response 後做 INSERT / UPDATE / unchanged 比對
```

**此功能目前尚未開始開發。**

## 8. 現有產品中的 `spin_axis`

現有 Statcast ingestion 不需要為這次研究重新加 `spin_axis`：

- Savant 原始 CSV payload 會完整 snapshot；
- storage 會依合法 upstream headers 自動增加 schema columns；
- `spin_axis` 已被定義為 REAL；
- `spin_axis` 已存在 CURRENT_DOCUMENTED_COLUMNS；
- UI 已有「旋轉軸 Spin Axis」欄位名稱；
- RS-02 已處理 circular feature semantics（sin/cos + isotropic scaling）。

因此 RESEARCH-01 不應順手改動既有 `spin_axis` product path。

## 9. 3D baseball research asset

Repo 已另外保存：

```text
research_assets/3d_baseball/
```

內容只保存第三方 Savant-style Three.js baseball reconstruction 的 provenance / manifest / reproducible fetch helper。

由於研究時沒有在上游 repo root 發現明確 license，沒有把第三方 scene / texture payload 直接 vendoring 進本 repo。

這個 asset 可供未來 coordinate-system、spin-axis 或 seam-orientation rendering 研究使用，但目前不是 production dependency。

## 10. 存取與穩定性邊界

研究發現多個未文件化 Savant route 可在一般瀏覽器情境下回資料；未文件化 endpoint 可能隨時變更，不應當成官方穩定 API 契約。

此外，未來若要把未文件化 MLB Digital Properties endpoint 做成自動批次取得來源，應在實作前重新確認 MLB 當時的 Terms of Use、授權與合理的流量策略。RESEARCH-01 的「技術上可讀」結論不等於已處理完使用條款或資料授權問題。

## 11. RESEARCH-01 最終判定

### 已完成

- Pitch3D 真正資料來源：找到。
- Pitch3D 格式與 grain：找到。
- Pitch3D 逐球識別：找到。
- Pitch3D 歷史覆蓋／分頁／MLB-MiLB／更新性：完成實測。
- 球員頁 3D spin aggregate：找到。
- 球員頁 aggregate 的 server-render / frontend data flow：確認。
- Spin Direction / Spin Axis 公開 route：已系統性檢查。
- 公開 per-pitch candidates：已檢查。
- `spin_axis` 與 aggregate measured direction：完成逐球種實證比對。

### 仍沒有公開取得方式的目標資料

```text
aggregation 前的逐球 seam orientation
absolute seam phase
full ball pose
quaternion / rotation matrix
逐幀 orientation sequence
```

### 結論

> **就本次已檢查的 Baseball Savant / MLB 公開 client surface 而言，RESEARCH-01 已經把可觀察的資料鏈追到公開層邊界；沒有發現可穩定取得 aggregation 前逐球真實 seam-orientation / absolute ball-pose 的公開 endpoint。**

如果未來仍要取得這一層資料，方向應改成：

- 合法的 MLBAM / Hawk-Eye 受控資料權限；
- 新出現的官方公開資料產品；
- 明確已知會收到 raw seam pose 的其他 MLB client；
- 或新的可驗證公開 endpoint。

不建議繼續以無限 URL 猜測維持研究。

## 12. 下一個產品決策點

依既定專案順序，RESEARCH-01 完成後先決定：

1. 是否繼續尋求非公開／受控 Hawk-Eye seam-pose 資料權限；
2. 目前是否暫停真正的 Hawk-Eye ball-pose integration；
3. 已另行決定、但尚未開始開發的「完整 Pitch3D CSV 整合」何時排入 roadmap。

完成這個決策後，再進 CAP-04 Auto Cluster Count。
