package com.example.legacypricing.order;

import java.math.BigDecimal;

public record OrderPlacementCommand(
        String orderId,
        String idempotencyKey,
        String customerId,
        String sku,
        int quantity,
        BigDecimal total,
        String currency,
        String paymentToken
) {
}
