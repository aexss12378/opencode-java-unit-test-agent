package com.example.legacypricing.pricing.policy;

import com.example.legacypricing.pricing.calculator.ShippingFeeCalculator;
import com.example.legacypricing.pricing.config.PricingProperties;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Objects;
import org.springframework.stereotype.Component;

@Component
public final class ShippingFeePolicy implements ShippingFeeCalculator {

    private final PricingProperties properties;

    public ShippingFeePolicy(PricingProperties properties) {
        this.properties = Objects.requireNonNull(properties, "properties");
    }

    /**
     * Returns zero shipping when the discounted subtotal is greater than or
     * equal to the configured free-shipping threshold. Otherwise returns the
     * configured standard shipping fee. The returned amount always has two
     * decimal places.
     *
     * @throws IllegalArgumentException when the subtotal is null or negative
     */
    @Override
    public BigDecimal calculateShippingFee(BigDecimal discountedSubtotal) {
        if (discountedSubtotal == null || discountedSubtotal.signum() < 0) {
            throw new IllegalArgumentException("discountedSubtotal must not be null or negative");
        }
        if (discountedSubtotal.compareTo(properties.freeShippingThreshold()) >= 0) {
            return BigDecimal.ZERO.setScale(2, RoundingMode.UNNECESSARY);
        }
        return properties.standardShippingFee().setScale(2, RoundingMode.HALF_UP);
    }
}
