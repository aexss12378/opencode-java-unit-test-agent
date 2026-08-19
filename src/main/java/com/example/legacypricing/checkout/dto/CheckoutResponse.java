package com.example.legacypricing.checkout.dto;

import com.example.legacypricing.checkout.model.CheckoutResult;
import com.example.legacypricing.checkout.model.CheckoutStatus;
import java.time.Instant;

/**
 * API response for the checkout endpoint.
 *
 * @param status the checkout outcome: {@code PAYMENT_PENDING} or {@code OUT_OF_STOCK}
 * @param paymentDeadline the deadline for the customer to complete payment; {@code null} when {@code status} is {@code OUT_OF_STOCK}
 */
public record CheckoutResponse(CheckoutStatus status, Instant paymentDeadline) {

    /**
     * Creates a {@code CheckoutResponse} from a {@link CheckoutResult}.
     */
    public static CheckoutResponse from(CheckoutResult result) {
        return new CheckoutResponse(result.status(), result.paymentDeadline());
    }
}
