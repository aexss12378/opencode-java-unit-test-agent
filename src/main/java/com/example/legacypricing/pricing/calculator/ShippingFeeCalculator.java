package com.example.legacypricing.pricing.calculator;

import java.math.BigDecimal;

@FunctionalInterface
public interface ShippingFeeCalculator {

    BigDecimal calculateShippingFee(BigDecimal discountedSubtotal);
}
