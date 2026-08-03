# 訂單生命週期規格

本文件是 `OrderLifecyclePolicy` 的正式依據。

## 合法轉移

| 目前狀態 | 事件 | 下一狀態 |
|---|---|---|
| `DRAFT` | `SUBMIT` | `PAYMENT_PENDING` |
| `PAYMENT_PENDING` | `AUTHORIZE_PAYMENT` | `CONFIRMED` |
| `PAYMENT_PENDING` | `CANCEL` | `CANCELLED` |
| `CONFIRMED` | `START_FULFILLMENT` | `FULFILLING` |
| `FULFILLING` | `SHIP` | `SHIPPED` |

## 期限與錯誤

1. `PAYMENT_PENDING` 的付款或取消事件發生在期限之後時，一律轉為 `EXPIRED`。
2. 事件時間剛好等於期限仍視為有效，依事件轉為 `CONFIRMED` 或 `CANCELLED`。
3. `SHIPPED`、`CANCELLED` 與 `EXPIRED` 是終止狀態，不接受任何事件。
4. 表格未列出的其他狀態與事件組合拋出 `IllegalStateException`。
5. 時間判斷必須使用注入的 `Clock`；只有 `PAYMENT_PENDING` 需要付款期限。
