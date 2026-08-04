package com.example.legacypricing.pricing.config;

import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotNull;
import java.math.BigDecimal;
import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

@Validated
@ConfigurationProperties("pricing")
public record PricingProperties(
        @NotNull @DecimalMin("0.00") @DecimalMax("100.00")
        BigDecimal standardDiscountPercent,
        @NotNull @DecimalMin("0.00") @DecimalMax("100.00")
        BigDecimal vipDiscountPercent,
        @NotNull @DecimalMin("0.00") @DecimalMax("1.00")
        BigDecimal taxRate,
        @NotNull @DecimalMin("0.00")
        BigDecimal freeShippingThreshold,
        @NotNull @DecimalMin("0.00")
        BigDecimal standardShippingFee,
        @NotNull
        Duration paymentWindow
) {
}
