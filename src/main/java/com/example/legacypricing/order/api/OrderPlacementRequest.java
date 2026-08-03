package com.example.legacypricing.order.api;

import com.example.legacypricing.order.OrderPlacementCommand;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import java.math.BigDecimal;

public record OrderPlacementRequest(
        @NotBlank String orderId,
        @NotBlank String idempotencyKey,
        @NotBlank String customerId,
        @NotBlank String sku,
        @Positive int quantity,
        @NotNull @DecimalMin(value = "0.00", inclusive = false) BigDecimal total,
        @NotBlank String currency,
        @NotBlank String paymentToken
) {

    OrderPlacementCommand toCommand() {
        return new OrderPlacementCommand(
                orderId,
                idempotencyKey,
                customerId,
                sku,
                quantity,
                total,
                currency,
                paymentToken
        );
    }
}
