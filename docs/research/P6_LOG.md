# P6 研究與開發紀錄

格式：日期 | 步驟 ID | 內容 | 結果 | 偏離計畫？

2026-10-14 | 6.0a | `player_profiles`、`league_model`、`gbm`（xgboost／hgb）、合成球員世界 P0/P7/P8；`outcome_model` 決策時特徵版本；遠端執行說明 | 單元與已知答案測試全過（475 項）；使用者電腦 GPU 煙霧測試（RvR、2023→2024，xgboost／cuda，616 秒）成功；冷啟動 L3 對 L0 的 logloss 改善：投手側 0.0118、打者側 0.0124；G1a 全過；G1b 在冷啟動折過、在 season_unseen（樣本小）點估計為負 | 描述改為歷史模式（只用更早的比賽）；新增 `min_gain`；G1b 只用沒見過的球員判定
2026-10-14 | 6.0b | `policy_core`（價值迭代 DP、五種政策、信賴區域、邊際價值、佔據度）、`policy_build`（格點預測、支持規則、袋裝分析、交叉評估）、`policy_worlds`（W0–W3、W6 已知答案世界）、方法 `pitch_policy`（M0） | 見 6.0b 報告 | W4、W5（混淆世界）延到 6.0c，因為需要固定效應錨點；方法在內部擬合 L0 模型（見計畫 A.5 的差異說明）
2026-10-15 | 6.0c | `anchor`（固定效應 LPM、S3／S4、球種對比、連投項、J_BIP、與模型的一致性檢查）、方法 `action_effects`、W4／W5 混淆世界、圖表（方向圖、取捨前緣、特徵消融、情境條圖）與 JS 測試、`pitch_policy` 的 `anchor_run_id` 與方向圖／錨點一致性區段 | W4／W5 如預期：S3 擋得住投手素質、擋不住當天狀態，S4 兩者都擋得住；全聯盟 L0 模型在 W4 被混淆，錨點偵測得到 | 修正連投編碼（`streak_pos` 是本球位置，狀態要前一段長度），加 `derive_prior_streak`；`ResearchContext` 新增 `store`
2026-10-15 | 6.0d | `query_core`（情境表查詢 M0、M1–M3 即時計算、旗標、無法區分群組、名稱擷取、文字表格）、方法 `pitch_query`（5.7 驗收、延遲、名稱涵蓋）、`QueryRuntime`、API（`/api/research/pitch-query`、`/meta`、`/players`）、命令列 `research query`、網頁「情境查詢」頁與 Chromium 截圖測試（`docs/research/figures/p6_query_m0.png`、`p6_query_m1.png`） | 合成世界上：驗收全過；M1–M3 可重複、機率和為 1、區間有序；快速的投手（P7）比慢的投手預測更多揮空；延遲在測試規模遠小於 30 秒（真實規模要在使用者電腦上量） | `pitch_policy` 版本 2（情境表改存 float64 與計數）；`league_model` 模型包新增球員描述；`ProfileModel` 新增 `players`、`pitcher_arsenal`
2026-10-15 | 一鍵 | `param_search`（隨機搜尋 + 依球員分折的交叉驗證）、`pipeline`（五步驟背景工作、失敗後續跑）、API `/api/research/pipeline/*`、網頁「一鍵研究」頁（開始／停止／進度／結果摘要／開啟情境查詢） | 合成世界上五步驟全跑通、第二次按開始全部沿用、失敗步驟保留前面的結果；Chromium 測試涵蓋完成、執行中、失敗三種畫面 | 測試抓到並修了：`cold_only` 的模型包是空的（現在用全部資料另外擬合保存用的成員）；`league_model` 版本 2
