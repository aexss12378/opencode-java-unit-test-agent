# 企業訂單平台測試專案

這是一個可直接啟動的 Spring Boot 訂單平台，用來評估單元測試是否能處理企業系統常見的規格密度與協作者互動，而不是只追求測試數量或涵蓋率。

## 業務流程

- `POST /api/quotes`：計算折扣、稅額、運費與訂單總額。
- `POST /api/checkouts`：保留庫存並建立付款期限。
- `POST /api/order-placements`：執行輸入驗證、冪等檢查、風險分流、庫存保留、付款授權、失敗補償與結果保存。
- `OrderLifecyclePolicy`：管理訂單從草稿、待付款、確認、出貨到終止狀態的合法轉移。

## 專案分層

專案採用 Spring Boot 企業專案常見的依功能分套件，再於功能內分層。根套件只保留 `EnterpriseOrderApplication` 啟動類別：

- `common/api`：共用 API 錯誤格式與例外轉換。
- `checkout/controller`、`dto`、`model`、`service`、`port`、`infra`：結帳與庫存保留流程。
- `pricing/controller`、`dto`、`service`、`calculator`、`policy`、`config`：報價、折扣、稅額、運費與付款期限。
- `customer/service`：顧客標籤行為。
- `order/controller`：HTTP 狀態與回應轉換。
- `order/service`：風險、庫存、付款、補償與冪等流程。
- `order/dao`：訂單放行結果的資料存取介面與 JPA 實作。
- `order/entity`：資料庫實體。
- `order/dto`：API 請求與回應資料。
- `order/vo`：`OrderId`、`IdempotencyKey` 與 `Money` 等具有不變條件的值物件。
- `order/mapper`：API、領域結果與資料庫實體之間的轉換。
- `order/util`：產生冪等請求指紋的純函式工具。
- `order/config`、`order/exception`、`order/port`、`order/infra`：設定、例外與外部服務邊界。

冪等結果使用 Spring Data JPA 寫入 H2。付款權杖只參與 SHA-256 請求指紋計算，不會寫入資料庫。

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
- Spring Data JPA、H2
- JUnit 5、Mockito、AssertJ

```bash
./mvnw spring-boot:run
```

應用程式預設監聽 `8080`，可透過 `server.port` 覆寫。
