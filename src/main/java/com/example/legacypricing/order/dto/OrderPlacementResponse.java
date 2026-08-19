package com.example.legacypricing.order.dto;

import com.example.legacypricing.order.OrderPlacementStatus;
import java.time.Instant;

/**
 * 訂單放行 API 的回應資料。
 *
 * 對應訂單放行業務結果，包含狀態、訂單編號、庫存保留編號、付款授權編號與付款期限。
 *
 * 非 ACCEPTED 狀態時，reservationId、authorizationId 與 paymentDeadline 為 null。
 *
 * @param status 訂單放行狀態。
 * @param orderId 訂單編號。
 * @param reservationId 庫存保留編號；僅在 ACCEPTED 時有值。
 * @param authorizationId 付款授權編號；僅在 ACCEPTED 時有值。
 * @param paymentDeadline 付款期限；僅在 ACCEPTED 時有值。
 */
public record OrderPlacementResponse(
        OrderPlacementStatus status,
        String orderId,
        String reservationId,
        String authorizationId,
        Instant paymentDeadline
) {
}
