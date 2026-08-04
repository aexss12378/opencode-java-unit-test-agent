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

@Component
public final class OrderPlacementApiMapper {

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
