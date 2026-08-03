# 企業訂單平台測試專案

這是一個可直接啟動的 Spring Boot 訂單平台，用來評估單元測試是否能處理企業系統常見的規格密度與協作者互動，而不是只追求測試數量或涵蓋率。

## 業務流程

- `POST /api/quotes`：計算折扣、稅額、運費與訂單總額。
- `POST /api/checkouts`：保留庫存並建立付款期限。
- `POST /api/order-placements`：執行輸入驗證、冪等檢查、風險分流、庫存保留、付款授權、失敗補償與結果保存。
- `OrderLifecyclePolicy`：管理訂單從草稿、待付款、確認、出貨到終止狀態的合法轉移。

## 權威規格

- `docs/pricing-rules.md`：折扣計算與取位。
- `docs/order-placement-rules.md`：訂單放行、冪等、風險、庫存、付款與補償。
- `docs/order-lifecycle-rules.md`：訂單狀態轉移與付款期限。
- 公開 API 的 Javadoc：類別或方法特有的可觀察行為。
- Bean Validation 註記與 `src/main/resources/application.yml`：輸入限制與部署設定值。

正式原始碼只用來理解介面與流程。若實作、既有測試與上述權威規格衝突，以權威規格為準。

## 技術條件

- Java 17
- Spring Boot 3
- Maven Wrapper
- JUnit 5、Mockito、AssertJ

```bash
./mvnw spring-boot:run
```

應用程式預設監聽 `8080`，可透過 `server.port` 覆寫。
