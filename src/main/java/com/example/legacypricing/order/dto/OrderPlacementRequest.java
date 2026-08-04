package com.example.legacypricing.order.dto;

import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;
import java.math.BigDecimal;

public record OrderPlacementRequest(
        @NotBlank @Size(max = 64) String orderId,
        @NotBlank @Size(max = 128) String idempotencyKey,
        @NotBlank String customerId,
        @NotBlank String sku,
        @Positive int quantity,
        @NotNull @DecimalMin(value = "0.00", inclusive = false) BigDecimal total,
        @NotBlank @Pattern(regexp = "[A-Z]{3}") String currency,
        @NotBlank String paymentToken
) {
}
