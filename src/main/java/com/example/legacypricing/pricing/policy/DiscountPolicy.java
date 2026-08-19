package com.example.legacypricing.pricing.policy;

import java.math.BigDecimal;

/**
 * Discount policy used to determine the discount percentage for a given customer.
 *
 * <p>Implementations must return a non-null percentage between 0 and 100 inclusive.
 */
@FunctionalInterface
public interface DiscountPolicy {

    /**
     * Returns the discount percentage for the given customer ID.
     *
     * <p>The returned value is never {@code null} and is always between 0 and 100 inclusive.
     *
     * @param  customerId the customer identifier; must not be null or blank
     * @throws IllegalArgumentException when customerId is null or blank
     * @return the discount percentage (0–100 inclusive)
     */
    BigDecimal discountPercentFor(String customerId);
}
