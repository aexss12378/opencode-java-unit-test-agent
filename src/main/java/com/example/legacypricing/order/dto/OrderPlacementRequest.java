package com.example.legacypricing.order.dto;

import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;
import java.math.BigDecimal;

/**
 * API request payload for placing an order.
 *
 * Validation rules enforced by Bean Validation:
 *
 * <ul>
 * <li>{@code orderId} — non-blank, up to 64 characters.
 * <li>{@code idempotencyKey} — non-blank, up to 128 characters.
 * <li>{@code customerId} — non-blank.
 * <li>{@code sku} — non-blank.
 * <li>{@code quantity} — positive integer.
 * <li>{@code total} — non-null, strictly greater than zero.
 * <li>{@code currency} — non-blank, exactly three uppercase letters.
 * <li>{@code paymentToken} — non-blank.
 * </ul>
 *
 * @param orderId           unique order identifier (max 64 characters)
 * @param idempotencyKey    idempotency key for request deduplication (max 128 characters)
 * @param customerId        customer identifier
 * @param sku               product SKU
 * @param quantity          order quantity, must be positive
 * @param total             order total amount, must be greater than zero
 * @param currency          ISO 4217 currency code (three uppercase letters)
 * @param paymentToken      payment authorization token
 */
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
