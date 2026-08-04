package com.example.legacypricing.checkout.dto;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;

public record CheckoutRequest(
        @NotBlank String orderId,
        @NotBlank String sku,
        @Min(1) int quantity
) {
}
