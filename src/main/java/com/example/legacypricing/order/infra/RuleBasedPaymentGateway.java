package com.example.legacypricing.order.infra;

import com.example.legacypricing.order.PaymentAuthorization;
import com.example.legacypricing.order.port.PaymentGateway;
import java.math.BigDecimal;
import org.springframework.stereotype.Component;

@Component
public final class RuleBasedPaymentGateway implements PaymentGateway {

    /**
     * Local profile rule: a token beginning exactly with DECLINE- is declined;
     * every other non-blank token is approved with authorization ID AUTH-{orderId}.
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
