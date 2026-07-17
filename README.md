# 舊系統定價範例

這是一個刻意保持精簡的 Maven 專案，用來驗證專案內的 OpenCode 單元測試代理。

正式規格在 `docs/pricing-rules.md`。既有測試只代表目前的回歸基準，不取代正式規格。測試代理只能修改 `src/test/**`，不應修改正式程式碼來讓測試通過。

基準驗證：

```bash
./mvnw -B -ntp clean verify
./mvnw -B -ntp org.pitest:pitest-maven:mutationCoverage
```

在這個目錄啟動 OpenCode 後，可執行：

```text
/unit-test 請為 OrderPricingService 補上依正式規格設計的單元測試
```
