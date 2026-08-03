package com.example.legacypricing.order.api;

import com.example.legacypricing.order.OrderPlacementResult;
import com.example.legacypricing.order.OrderPlacementStatus;
import java.time.Instant;

public record OrderPlacementResponse(
        OrderPlacementStatus status,
        String orderId,
        String reservationId,
        String authorizationId,
        Instant paymentDeadline
) {

    static OrderPlacementResponse from(OrderPlacementResult result) {
        return new OrderPlacementResponse(
                result.status(),
                result.orderId(),
                result.reservationId(),
                result.authorizationId(),
                result.paymentDeadline()
        );
    }
}
