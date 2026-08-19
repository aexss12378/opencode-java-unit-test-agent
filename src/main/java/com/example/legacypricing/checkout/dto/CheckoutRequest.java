package com.example.legacypricing.checkout.dto;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;

/**
 * Checkout request payload for the {@code /api/checkouts} endpoint.
 *
 * Fields are validated by Bean Validation before reaching the use case:
 * {@code orderId} and {@code sku} must not be null or blank;
 * {@code quantity} must be at least one. The use layer also rejects
 * non-positive quantities with {@code IllegalArgumentException}.
 *
 * @param orderId the order identifier; must not be blank
 * @param sku the stock keeping unit identifier; must not be blank
 * @param quantity the number of units to reserve; must be one or greater
 */
public record CheckoutRequest(
        @NotBlank String orderId,
        @NotBlank String sku,
        @Min(1) int quantity
) {
}
