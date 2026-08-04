package com.example.legacypricing.customer.service;

import java.util.Objects;

public final class CustomerTagService {

    private static final int MAX_CUSTOMER_ID_LENGTH = 12;

    private final String prefix;

    public CustomerTagService(String prefix) {
        this.prefix = Objects.requireNonNull(prefix, "prefix");
    }

    public String createTag(String customerId) {
        if (customerId == null || customerId.isBlank()) {
            throw new IllegalArgumentException("customerId must not be blank");
        }

        String normalizedCustomerId = customerId.trim();
        if (normalizedCustomerId.length() > MAX_CUSTOMER_ID_LENGTH) {
            throw new IllegalArgumentException(
                    "customerId must be at most 12 characters"
            );
        }

        return prefix + "-" + normalizedCustomerId;
    }
}
