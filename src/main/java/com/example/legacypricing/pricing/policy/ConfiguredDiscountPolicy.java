package com.example.legacypricing.pricing.policy;

import com.example.legacypricing.pricing.config.PricingProperties;
import java.math.BigDecimal;
import java.util.Objects;
import org.springframework.stereotype.Component;

@Component
public final class ConfiguredDiscountPolicy implements DiscountPolicy {

    private static final String VIP_PREFIX = "VIP-";

    private final PricingProperties properties;

    public ConfiguredDiscountPolicy(PricingProperties properties) {
        this.properties = Objects.requireNonNull(properties, "properties");
    }

    /**
     * Returns the configured VIP percentage when the customer ID begins with
     * {@code VIP-}; otherwise returns the configured standard percentage.
     *
     * @throws IllegalArgumentException when the customer ID is null or blank
     */
    @Override
    public BigDecimal discountPercentFor(String customerId) {
        if (customerId == null || customerId.isBlank()) {
            throw new IllegalArgumentException("customerId must not be blank");
        }
        return customerId.startsWith(VIP_PREFIX)
                ? properties.vipDiscountPercent()
                : properties.standardDiscountPercent();
    }
}
