package com.example.legacypricing.pricing.dto;

import java.math.BigDecimal;

public record OrderQuote(
        BigDecimal subtotal,
        BigDecimal discount,
        BigDecimal discountedSubtotal,
        BigDecimal tax,
        BigDecimal shippingFee,
        BigDecimal total
) {
}
