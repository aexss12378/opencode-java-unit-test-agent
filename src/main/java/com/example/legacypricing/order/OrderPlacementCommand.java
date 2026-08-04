package com.example.legacypricing.order;

import com.example.legacypricing.order.vo.IdempotencyKey;
import com.example.legacypricing.order.vo.Money;
import com.example.legacypricing.order.vo.OrderId;

public record OrderPlacementCommand(
        OrderId orderId,
        IdempotencyKey idempotencyKey,
        String customerId,
        String sku,
        int quantity,
        Money total,
        String paymentToken
) {
}
