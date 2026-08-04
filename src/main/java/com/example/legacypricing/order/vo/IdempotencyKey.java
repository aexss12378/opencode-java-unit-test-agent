package com.example.legacypricing.order.vo;

/**
 * Exact, case-sensitive idempotency key with no normalization.
 */
public record IdempotencyKey(String value) {

    private static final int MAX_LENGTH = 128;

    public IdempotencyKey {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException("idempotency key must not be blank");
        }
        if (value.length() > MAX_LENGTH) {
            throw new IllegalArgumentException(
                    "idempotency key must not exceed 128 characters"
            );
        }
    }
}
