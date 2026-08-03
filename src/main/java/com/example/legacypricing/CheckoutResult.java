package com.example.legacypricing;

import java.time.Instant;

public record CheckoutResult(CheckoutStatus status, Instant paymentDeadline) {

    public static CheckoutResult paymentPending(Instant paymentDeadline) {
        return new CheckoutResult(CheckoutStatus.PAYMENT_PENDING, paymentDeadline);
    }

    public static CheckoutResult outOfStock() {
        return new CheckoutResult(CheckoutStatus.OUT_OF_STOCK, null);
    }
}
