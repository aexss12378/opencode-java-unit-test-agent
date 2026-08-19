package com.example.legacypricing.order.infra;

import com.example.legacypricing.order.PaymentAuthorization;
import com.example.legacypricing.order.port.PaymentGateway;
import java.math.BigDecimal;
import org.springframework.stereotype.Component;

/**
 * Rule-based payment gateway that declines tokens starting with "DECLINE-"
 * and approves all others with an authorization ID derived from the order ID.
 */
@Component
public final class RuleBasedPaymentGateway implements PaymentGateway {

    /**
     * Local profile rule: a token beginning exactly with DECLINE- is declined;
     * every other token is approved with authorization ID AUTH-{orderId}.
     *
     * @param orderId the order identifier, used to construct the authorization ID
     * @param total the payment amount (not used by this rule)
     * @param currency the payment currency (not used by this rule)
     * @param paymentToken the payment token to evaluate
     * @return an approved authorization with ID AUTH-{orderId}, or a declined authorization
     */
    @Override
    public PaymentAuthorization authorize(
            String orderId,
            BigDecimal total,
            String currency,
            String paymentToken
    ) {
        if (paymentToken.startsWith("DECLINE-")) {
            return PaymentAuthorization.declined();
        }
        return PaymentAuthorization.approved("AUTH-" + orderId);
    }
}
