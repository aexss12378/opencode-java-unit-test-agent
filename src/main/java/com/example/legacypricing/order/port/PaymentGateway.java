package com.example.legacypricing.order.port;

import com.example.legacypricing.order.PaymentAuthorization;
import java.math.BigDecimal;

/**
 * Payment authorization port used during order placement.
 *
 * Implementations perform the actual payment authorization step after stock
 * reservation succeeds. The caller expects a non-null result; a declined
 * authorization triggers stock release and a
 * {@link com.example.legacypricing.order.OrderPlacementStatus#PAYMENT_DECLINED} result.
 */
@FunctionalInterface
public interface PaymentGateway {

    /**
     * Authorizes a payment for the given order.
     *
     * Called only after stock reservation succeeds (see
     * {@code docs/order-placement-rules.md} §5). The implementation must not
     * return {@code null}.
     *
     * @param orderId the original order identifier
     * @param total the order total amount
     * @param currency the order currency code
     * @param paymentToken the payment token from the request
     * @return an authorization result; never {@code null}
     */
    PaymentAuthorization authorize(
            String orderId,
            BigDecimal total,
            String currency,
            String paymentToken
    );
}
