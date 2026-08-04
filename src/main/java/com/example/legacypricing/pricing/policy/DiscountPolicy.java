package com.example.legacypricing.pricing.policy;

import java.math.BigDecimal;

@FunctionalInterface
public interface DiscountPolicy {

    BigDecimal discountPercentFor(String customerId);
}
