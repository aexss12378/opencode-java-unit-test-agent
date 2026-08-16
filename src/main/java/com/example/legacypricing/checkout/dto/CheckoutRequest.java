package com.example.legacypricing.checkout.dto;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;

/**
 * 結帳請求 DTO，用於保留庫存並建立付款期限。
 *
 * 透過 `POST /api/checkouts` 提交，由 Bean Validation 驗證欄位限制：
 * - `orderId` 與 `sku` 不得為空白。
 * - `quantity` 必須大於或等於 1。
 *
 * 驗證通過後由 `CheckoutUseCase.checkout` 處理，成功時回傳 HTTP 201
 * （`CheckoutStatus.PAYMENT_PENDING`），庫存不足時回傳 HTTP 409
 * （`CheckoutStatus.OUT_OF_STOCK`）。
 *
 * @param orderId 訂單識別碼。不得為空白。
 * @param sku 商品編號（SKU）。不得為空白。
 * @param quantity 購買數量。必須大於或等於 1。
 */
public record CheckoutRequest(
        @NotBlank String orderId,
        @NotBlank String sku,
        @Min(1) int quantity
) {
}
