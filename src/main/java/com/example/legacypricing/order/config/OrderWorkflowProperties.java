package com.example.legacypricing.order.config;

import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import java.math.BigDecimal;
import java.time.Duration;
import java.util.Objects;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

@Validated
@ConfigurationProperties("order-workflow")
public record OrderWorkflowProperties(
        @Min(0) @Max(100) int manualReviewScore,
        @Min(0) @Max(100) int rejectionScore,
        @NotNull @DecimalMin(value = "0.00", inclusive = false) BigDecimal maximumOrderTotal,
        @NotBlank @Pattern(regexp = "[A-Z]{3}") String supportedCurrency,
        @NotNull Duration paymentAuthorizationWindow
) {

    public OrderWorkflowProperties {
        Objects.requireNonNull(maximumOrderTotal, "maximumOrderTotal");
        Objects.requireNonNull(supportedCurrency, "supportedCurrency");
        Objects.requireNonNull(paymentAuthorizationWindow, "paymentAuthorizationWindow");
        if (manualReviewScore >= rejectionScore) {
            throw new IllegalArgumentException("manual review score must be below rejection score");
        }
        if (paymentAuthorizationWindow.isZero() || paymentAuthorizationWindow.isNegative()) {
            throw new IllegalArgumentException("payment authorization window must be positive");
        }
    }
}
