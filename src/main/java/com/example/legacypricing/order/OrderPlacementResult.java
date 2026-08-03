package com.example.legacypricing.order;

import java.time.Instant;

public record OrderPlacementResult(
        OrderPlacementStatus status,
        String orderId,
        String reservationId,
        String authorizationId,
        Instant paymentDeadline
) {

    public static OrderPlacementResult withoutReservation(
            OrderPlacementStatus status,
            String orderId
    ) {
        return new OrderPlacementResult(status, orderId, null, null, null);
    }

    public static OrderPlacementResult accepted(
            String orderId,
            String reservationId,
            String authorizationId,
            Instant paymentDeadline
    ) {
        return new OrderPlacementResult(
                OrderPlacementStatus.ACCEPTED,
                orderId,
                reservationId,
                authorizationId,
                paymentDeadline
        );
    }
}
