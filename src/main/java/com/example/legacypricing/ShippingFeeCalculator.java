package com.example.legacypricing;

import java.math.BigDecimal;

@FunctionalInterface
public interface ShippingFeeCalculator {

    BigDecimal calculateShippingFee(BigDecimal discountedSubtotal);
}
