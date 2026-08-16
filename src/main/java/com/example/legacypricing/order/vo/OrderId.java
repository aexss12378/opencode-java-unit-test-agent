package com.example.legacypricing.order.vo;

/**
 * Order identifier that preserves the caller-provided value without normalization.
 *
 * @param value the caller-provided order ID; must not be blank and must not exceed 64 characters
 */
public record OrderId(String value) {

    private static final int MAX_LENGTH = 64;

    /**
     * Validates that the order ID is not blank and does not exceed 64 characters.
     *
     * @param value the order ID value to validate
     * @throws IllegalArgumentException if {@code value} is null or blank, or exceeds 64 characters
     */
    public OrderId {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException("order ID must not be blank");
        }
        if (value.length() > MAX_LENGTH) {
            throw new IllegalArgumentException("order ID must not exceed 64 characters");
        }
    }
}
