package com.example.legacypricing.order.vo;

/**
 * Order identifier that preserves the caller-provided value without normalization.
 */
public record OrderId(String value) {

    private static final int MAX_LENGTH = 64;

    public OrderId {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException("order ID must not be blank");
        }
        if (value.length() > MAX_LENGTH) {
            throw new IllegalArgumentException("order ID must not exceed 64 characters");
        }
    }
}
