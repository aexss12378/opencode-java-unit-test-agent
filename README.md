# 舊系統定價範例

這是一個刻意保持精簡的 Maven 專案，用來驗證專案內的 OpenCode 單元測試代理。

正式規格在 `docs/pricing-rules.md`。既有測試只代表目前的回歸基準，不取代正式規格。測試代理沒有直接寫檔權限；人工核准測試意圖後，只能透過 `submit_unit_tests` 在隔離副本完成基準測試、編譯、測試、JaCoCo 與限定範圍 PIT，全部通過才可新增 `src/test/java/**`。工具不會修改正式程式來讓測試通過。

基準驗證：

```bash
./mvnw -B -ntp clean verify
./mvnw -B -ntp org.pitest:pitest-maven:mutationCoverage
```

在這個目錄啟動 OpenCode 後，可執行：

```text
/unit-test 請為 OrderPricingService 補上依正式規格設計的單元測試
```

代理會先提出附來源與分類的測試意圖，等待人工核准。建立候選測試後，OpenCode 會再顯示 `submit_unit_tests` 權限確認；只有工具回傳 `published` 才表示測試檔已加入專案。
