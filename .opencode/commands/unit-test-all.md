---
description: 盤點所有 Service，並以獨立子代理建立及驗證單元測試
agent: unit-test-orchestrator
subtask: false
---

執行模式：`unit-test-all/v1`

這次指令代表工程師已預先確認以下固定範圍與執行方式，不得再次詢問範圍、平行數量或是否開始：

1. 受測範圍是 `src/main/java` 中，簡單類別名稱以 `Service` 結尾的所有具體頂層類別；排除介面、抽象類別、巢狀類別與測試類別。
2. 完成全專案盤點後，對每個具備可信規格證據的範圍內 Service 派發一個全新的 `unit-test` 子代理。
3. 缺少可信規格證據或規格彼此衝突的 Service 不得編造預期結果，也不得靜默略過；請列為未開始並說明原因。
4. 只呼叫一次 `dispatch_unit_tests`，固定傳入 `execution_mode: unit-test-all/v1`、所有可派工 Service 的 `targets`、所有因規格原因不派工 Service 的 `not_started`，並將 `max_concurrency` 設為 `2`。兩份清單的聯集必須等於工具自行盤點的全部具體 Service。
5. 每個 Service 使用獨立本機分支、Git worktree 與測試檔。驗證完成後保留本機分支與 worktree 供工程師檢查；不得提交、推送或建立 PR。
6. 基準必須是乾淨、已與 GitHub 遠端同步的 `main`。前置檢查失敗時直接回報具體原因，不得自行提交、暫存或捨棄工程師的變更。

請留在目前主要工作階段等待所有子代理結束，再依協調主代理的結果核對規則彙整完整結果。
