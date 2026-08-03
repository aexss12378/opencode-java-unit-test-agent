package com.example.legacypricing;

import java.time.Instant;

public record CheckoutResponse(CheckoutStatus status, Instant paymentDeadline) {

    static CheckoutResponse from(CheckoutResult result) {
        return new CheckoutResponse(result.status(), result.paymentDeadline());
    }
}
