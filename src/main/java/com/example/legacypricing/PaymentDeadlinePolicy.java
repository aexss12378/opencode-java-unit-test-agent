package com.example.legacypricing;

import java.time.Clock;
import java.time.Instant;
import java.util.Objects;
import org.springframework.stereotype.Component;

@Component
public final class PaymentDeadlinePolicy implements PaymentDeadlineCalculator {

    private final Clock clock;
    private final PricingProperties properties;

    public PaymentDeadlinePolicy(Clock clock, PricingProperties properties) {
        this.clock = Objects.requireNonNull(clock, "clock");
        this.properties = Objects.requireNonNull(properties, "properties");
    }

    /**
     * Returns the injected clock's current instant plus the configured payment window.
     */
    @Override
    public Instant createDeadline() {
        return clock.instant().plus(properties.paymentWindow());
    }
}
