package com.example.legacypricing.order.mapper;

import com.example.legacypricing.order.OrderPlacementResult;
import com.example.legacypricing.order.OrderPlacementStatus;
import com.example.legacypricing.order.entity.OrderPlacementEntity;
import java.util.Objects;
import org.springframework.stereotype.Component;

@Component
public final class OrderPlacementPersistenceMapper {

    public OrderPlacementEntity toEntity(
            String idempotencyKey,
            String requestFingerprint,
            OrderPlacementResult result
    ) {
        Objects.requireNonNull(result, "result");
        return new OrderPlacementEntity(
                idempotencyKey,
                requestFingerprint,
                result.status(),
                result.orderId(),
                result.reservationId(),
                result.authorizationId(),
                result.paymentDeadline()
        );
    }

    public OrderPlacementResult toResult(OrderPlacementEntity entity) {
        Objects.requireNonNull(entity, "entity");
        if (entity.getStatus() == OrderPlacementStatus.ACCEPTED) {
            return OrderPlacementResult.accepted(
                    entity.getOrderId(),
                    entity.getReservationId(),
                    entity.getAuthorizationId(),
                    entity.getPaymentDeadline()
            );
        }
        return OrderPlacementResult.withoutReservation(
                entity.getStatus(),
                entity.getOrderId()
        );
    }
}
