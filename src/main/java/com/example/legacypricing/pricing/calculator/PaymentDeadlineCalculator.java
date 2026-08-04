package com.example.legacypricing.pricing.calculator;

import java.time.Instant;

@FunctionalInterface
public interface PaymentDeadlineCalculator {

    Instant createDeadline();
}
