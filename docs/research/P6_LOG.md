# P6 研究與開發紀錄

格式：日期 | 步驟 ID | 內容 | 結果 | 偏離計畫？

2026-10-14 | 6.0a | `player_profiles`、`league_model`、`gbm`（xgboost／hgb）、合成球員世界 P0/P7/P8；`outcome_model` 決策時特徵版本；遠端執行說明 | 單元與已知答案測試全過（475 項）；使用者電腦 GPU 煙霧測試（RvR、2023→2024，xgboost／cuda，616 秒）成功；冷啟動 L3 對 L0 的 logloss 改善：投手側 0.0118、打者側 0.0124；G1a 全過；G1b 在冷啟動折過、在 season_unseen（樣本小）點估計為負 | 描述改為歷史模式（只用更早的比賽）；新增 `min_gain`；G1b 只用沒見過的球員判定
2026-10-14 | 6.0b | `policy_core`（價值迭代 DP、五種政策、信賴區域、邊際價值、佔據度）、`policy_build`（格點預測、支持規則、袋裝分析、交叉評估）、`policy_worlds`（W0–W3、W6 已知答案世界）、方法 `pitch_policy`（M0） | 見 6.0b 報告 | W4、W5（混淆世界）延到 6.0c，因為需要固定效應錨點；方法在內部擬合 L0 模型（見計畫 A.5 的差異說明）
