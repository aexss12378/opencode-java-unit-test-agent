package com.example.legacypricing.pricing.calculator;

import java.time.Instant;

/**
 * A functional interface that produces the payment deadline for an order.
 *
 * Implementations compute the deadline from the current time and a configured
 * payment window; callers use the returned instant to decide whether a
 * payment or cancellation event is still valid.
 */
@FunctionalInterface
public interface PaymentDeadlineCalculator {

    /**
     * Returns the instant that represents the payment deadline.
     *
     * The returned value is used to determine whether a payment or cancellation
     * event occurring in the PAYMENT_PENDING state is still valid; events at or
     * before the deadline are accepted, while events after it cause the order
     * to transition to EXPIRED.
     */
    Instant createDeadline();
}
