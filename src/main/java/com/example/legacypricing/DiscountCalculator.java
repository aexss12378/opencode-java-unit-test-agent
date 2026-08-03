package com.example.legacypricing;

import java.math.BigDecimal;

@FunctionalInterface
public interface DiscountCalculator {

    BigDecimal calculateDiscount(String customerId, BigDecimal subtotal);
}
