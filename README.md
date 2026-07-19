# 舊系統定價範例

這是一個精簡的 Maven 專案，用來驗證專案內的 OpenCode 單元測試 Agent。正式規格位於 `docs/pricing-rules.md`；既有程式與既有測試只能說明目前行為，不能取代正式規格。

## 單元測試 Agent

Agent 一次只處理一個 Service 或類別，並且只能讀取專案、詢問工程師及呼叫 `submit_unit_tests`。它不能直接編輯檔案，也不能修改正式原始碼、`pom.xml`、文件或 `src/test/resources/**`。

一次提交包含多個測試案例與一個 `src/test/java/**/*Test.java` 候選檔。每個案例只有 `id`、`scenario`、`expected`、`evidence` 四個欄位；證據不足或規格衝突時，Agent 必須先詢問工程師。

`submit_unit_tests` 會完成下列流程：

1. 在作業系統暫存目錄的專案副本執行 `./mvnw -B -ntp test`。
2. 確認候選測試出現在 Maven 測試報告中、至少執行一次且沒有被跳過。
3. 通過後，在 `.opencode/unit-test-review/` 產生 `cases.md`、`changes.diff` 及完整候選測試，並等待人工核准。
4. 工程師核准後，工具才將同一份候選內容建立或更新至 `src/test/java/**`；拒絕或任何失敗都不發布。

審查期間不要修改正式測試檔。需要調整候選內容時，請拒絕並在對話中說明原因。此流程不得使用 OpenCode 自動核准模式。

候選測試會在人工審查前執行，因此只適用於公司內部可信任的測試產生流程，不是用來執行不受信任 Java 程式碼的安全沙箱。

## 使用方式

在專案根目錄啟動 OpenCode，確認目前 Agent 為 `unit-test`，然後輸入明確目標，例如：

```text
請為 OrderPricingService 補上依 docs/pricing-rules.md 設計的單元測試
```

只有工具回傳 `published: true`，才表示測試已寫入正式測試目錄。
