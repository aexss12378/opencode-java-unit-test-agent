package com.example.legacypricing.checkout.dto;

import com.example.legacypricing.checkout.model.CheckoutResult;
import com.example.legacypricing.checkout.model.CheckoutStatus;
import java.time.Instant;

public record CheckoutResponse(CheckoutStatus status, Instant paymentDeadline) {

    public static CheckoutResponse from(CheckoutResult result) {
        return new CheckoutResponse(result.status(), result.paymentDeadline());
    }
}
