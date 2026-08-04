package com.example.legacypricing.order.vo;

import java.math.BigDecimal;
import java.util.regex.Pattern;

/**
 * Monetary value that preserves the original amount and scale.
 */
public record Money(BigDecimal amount, String currency) {

    private static final Pattern CURRENCY_PATTERN = Pattern.compile("[A-Z]{3}");

    public Money {
        if (amount == null) {
            throw new IllegalArgumentException("amount must not be null");
        }
        if (currency == null || !CURRENCY_PATTERN.matcher(currency).matches()) {
            throw new IllegalArgumentException("currency must be three uppercase letters");
        }
    }
}
