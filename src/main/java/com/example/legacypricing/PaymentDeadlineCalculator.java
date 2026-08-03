package com.example.legacypricing;

import java.time.Instant;

@FunctionalInterface
public interface PaymentDeadlineCalculator {

    Instant createDeadline();
}
