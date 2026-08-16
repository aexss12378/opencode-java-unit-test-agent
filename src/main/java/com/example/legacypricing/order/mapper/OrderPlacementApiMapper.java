package com.example.legacypricing.order.mapper;

import com.example.legacypricing.order.OrderPlacementCommand;
import com.example.legacypricing.order.OrderPlacementResult;
import com.example.legacypricing.order.dto.OrderPlacementRequest;
import com.example.legacypricing.order.dto.OrderPlacementResponse;
import com.example.legacypricing.order.vo.IdempotencyKey;
import com.example.legacypricing.order.vo.Money;
import com.example.legacypricing.order.vo.OrderId;
import java.util.Objects;
import org.springframework.stereotype.Component;

/**
 * Spring component that converts between API DTOs and domain objects for order placement.
 */
@Component
public final class OrderPlacementApiMapper {

    /**
     * Converts an {@code OrderPlacementRequest} into an {@link OrderPlacementCommand}.
     *
     * Wraps {@code orderId} in {@link OrderId}, {@code idempotencyKey} in {@link IdempotencyKey}, and
     * {@code total}/{@code currency} in {@link Money}.
     *
     * Throws {@link NullPointerException} if {@code request} is null.
     */
    public OrderPlacementCommand toCommand(OrderPlacementRequest request) {
        Objects.requireNonNull(request, "request");
        return new OrderPlacementCommand(
                new OrderId(request.orderId()),
                new IdempotencyKey(request.idempotencyKey()),
                request.customerId(),
                request.sku(),
                request.quantity(),
                new Money(request.total(), request.currency()),
                request.paymentToken()
        );
    }

    /**
     * Converts an {@link OrderPlacementResult} into an {@link OrderPlacementResponse}.
     *
     * All fields are forwarded as-is: {@code status}, {@code orderId}, {@code reservationId},
     * {@code authorizationId}, and {@code paymentDeadline}.
     *
     * Throws {@link NullPointerException} if {@code result} is null.
     */
    public OrderPlacementResponse toResponse(OrderPlacementResult result) {
        Objects.requireNonNull(result, "result");
        return new OrderPlacementResponse(
                result.status(),
                result.orderId(),
                result.reservationId(),
                result.authorizationId(),
                result.paymentDeadline()
        );
    }
}
