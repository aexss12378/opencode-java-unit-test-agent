package com.example.legacypricing.pricing.service;

import com.example.legacypricing.pricing.calculator.DiscountCalculator;
import com.example.legacypricing.pricing.policy.DiscountPolicy;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Objects;
import org.springframework.stereotype.Service;

@Service
public final class OrderPricingService implements DiscountCalculator {

    private static final BigDecimal ONE_HUNDRED = new BigDecimal("100");

    private final DiscountPolicy discountPolicy;

    public OrderPricingService(DiscountPolicy discountPolicy) {
        this.discountPolicy = Objects.requireNonNull(discountPolicy, "discountPolicy");
    }

    @Override
    public BigDecimal calculateDiscount(String customerId, BigDecimal subtotal) {
        if (customerId == null || customerId.isBlank()) {
            throw new IllegalArgumentException("customerId must not be blank");
        }
        if (subtotal == null || subtotal.signum() < 0) {
            throw new IllegalArgumentException("subtotal must not be null or negative");
        }

        BigDecimal discountPercent = discountPolicy.discountPercentFor(customerId);
        if (discountPercent == null
                || discountPercent.compareTo(BigDecimal.ZERO) < 0
                || discountPercent.compareTo(ONE_HUNDRED) > 0) {
            throw new IllegalArgumentException("discount percent must be between 0 and 100");
        }

        BigDecimal rawDiscount = subtotal.multiply(discountPercent).divide(ONE_HUNDRED);
        return rawDiscount.setScale(2, RoundingMode.DOWN);
    }
}
