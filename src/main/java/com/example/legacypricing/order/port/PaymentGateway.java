package com.example.legacypricing.order.port;

import com.example.legacypricing.order.PaymentAuthorization;
import java.math.BigDecimal;

@FunctionalInterface
public interface PaymentGateway {

    PaymentAuthorization authorize(
            String orderId,
            BigDecimal total,
            String currency,
            String paymentToken
    );
}
