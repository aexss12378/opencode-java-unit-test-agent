---
description: 盤點所有 Service，並驗證每個 Service 都會取得獨立子代理
agent: unit-test-orchestrator
subtask: false
---

執行模式：`unit-test-all/task-smoke/v1`

這一輪只驗證 OpenCode 內建 Task 的派工行為，不撰寫、不驗證、也不提交任何單元測試。

1. 盤點 `src/main/java` 中，簡單類別名稱以 `Service` 結尾的所有具體頂層類別；排除介面、抽象類別、巢狀類別與測試類別。
2. 每個符合範圍的 Service 必須恰好呼叫一次內建 Task，並指定 `unit-test` 子代理；不得使用 SDK、plugin 或舊自訂派工工具。
3. 每次 Task 的提示詞必須包含 `execution_mode: unit-test-all/task-smoke/v1`、唯一的 `target_class`，以及 `operation: dispatch-check-only`。
4. 本輪子代理只能回覆收到的 `target_class`，不得呼叫任何工具或修改檔案。
5. 等待全部 Task 結束後，核對盤點數、Task 數與成功回覆數完全相同，再逐一列出 Service 與對應結果。

留在目前主要工作階段完成派工與結果彙整。
