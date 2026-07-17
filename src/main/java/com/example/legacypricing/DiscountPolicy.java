package com.example.legacypricing;

import java.math.BigDecimal;

@FunctionalInterface
public interface DiscountPolicy {

    BigDecimal discountPercentFor(String customerId);
}
