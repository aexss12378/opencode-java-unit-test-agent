package com.example.legacypricing.pricing.calculator;

import java.math.BigDecimal;

@FunctionalInterface
public interface DiscountCalculator {

    BigDecimal calculateDiscount(String customerId, BigDecimal subtotal);
}
