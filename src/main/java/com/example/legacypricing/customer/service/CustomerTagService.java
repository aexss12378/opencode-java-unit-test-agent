package com.example.legacypricing.customer.service;

import java.util.Objects;

/**
 * Service that creates customer tags by combining a configured prefix with a normalized customer ID.
 */
public final class CustomerTagService {

    private static final int MAX_CUSTOMER_ID_LENGTH = 12;

    private final String prefix;

    /**
     * Creates a new customer tag service with the given prefix.
     *
     * @param prefix the prefix string to prepend to every generated tag; must not be null
     */
    public CustomerTagService(String prefix) {
        this.prefix = Objects.requireNonNull(prefix, "prefix");
    }

    /**
     * Creates a tag by combining the configured prefix with the given customer ID.
     *
     * <p>The customer ID is trimmed of leading and trailing whitespace before
     * concatenation. The resulting tag has the form {@code prefix-customerId}.
     *
     * @param customerId the customer identifier to embed in the tag; must not be null or blank, and must be at most 12 characters after trimming
     * @return the generated tag string in the form {@code prefix-customerId}
     * @throws IllegalArgumentException if customerId is null or blank, or exceeds 12 characters after trimming
     */
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
