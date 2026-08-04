package com.example.legacypricing.order.dto;

import com.example.legacypricing.order.OrderPlacementStatus;
import java.time.Instant;

public record OrderPlacementResponse(
        OrderPlacementStatus status,
        String orderId,
        String reservationId,
        String authorizationId,
        Instant paymentDeadline
) {
}
