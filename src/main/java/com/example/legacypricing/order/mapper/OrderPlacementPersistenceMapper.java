package com.example.legacypricing.order.mapper;

import com.example.legacypricing.order.OrderPlacementResult;
import com.example.legacypricing.order.OrderPlacementStatus;
import com.example.legacypricing.order.entity.OrderPlacementEntity;
import java.util.Objects;
import org.springframework.stereotype.Component;

/**
 * Maps between domain order placement results and the JPA entity used for persistence.
 */
@Component
public final class OrderPlacementPersistenceMapper {

    /**
     * Converts the given domain result into a JPA entity for persistence.
     *
     * <p>The idempotency key and request fingerprint are taken from the caller;
     * the status, order ID, reservation ID, authorization ID, and payment deadline
     * are extracted from {@code result}.
     *
     * @throws NullPointerException if {@code result} is null.
     * @param idempotencyKey the idempotency key for this order placement.
     * @param requestFingerprint the SHA-256 fingerprint of the original request.
     * @param result the domain placement result to persist.
     * @return a new {@code OrderPlacementEntity} populated from the given result.
     */
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

    /**
     * Converts the given persisted entity back into a domain result.
     *
     * <p>If the entity status is {@code ACCEPTED}, returns an accepted result
     * containing the order ID, reservation ID, authorization ID, and payment deadline.
     * Otherwise returns a result without reservation carrying the entity's status and order ID.
     *
     * @throws NullPointerException if {@code entity} is null.
     * @param entity the persisted order placement entity.
     * @return the corresponding domain placement result.
     */
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
