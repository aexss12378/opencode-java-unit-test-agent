package com.example.legacypricing.checkout.service;

import com.example.legacypricing.checkout.model.CheckoutResult;
import com.example.legacypricing.checkout.port.InventoryGateway;
import com.example.legacypricing.pricing.calculator.PaymentDeadlineCalculator;
import java.util.Objects;
import org.springframework.stereotype.Service;

@Service
public final class CheckoutService implements CheckoutUseCase {

    private final InventoryGateway inventoryGateway;
    private final PaymentDeadlineCalculator paymentDeadlineCalculator;

    public CheckoutService(
            InventoryGateway inventoryGateway,
            PaymentDeadlineCalculator paymentDeadlineCalculator
    ) {
        this.inventoryGateway = Objects.requireNonNull(inventoryGateway, "inventoryGateway");
        this.paymentDeadlineCalculator = Objects.requireNonNull(
                paymentDeadlineCalculator,
                "paymentDeadlineCalculator"
        );
    }

    /**
     * Reserves inventory and returns OUT_OF_STOCK without creating a payment
     * deadline when reservation fails. When reservation succeeds, returns
     * PAYMENT_PENDING with the deadline supplied by the deadline policy.
     *
     * @throws IllegalArgumentException when order ID or SKU is null or blank,
     *                                  or quantity is not positive
     */
    @Override
    public CheckoutResult checkout(String orderId, String sku, int quantity) {
        if (orderId == null || orderId.isBlank()) {
            throw new IllegalArgumentException("orderId must not be blank");
        }
        if (sku == null || sku.isBlank()) {
            throw new IllegalArgumentException("sku must not be blank");
        }
        if (quantity <= 0) {
            throw new IllegalArgumentException("quantity must be positive");
        }

        if (!inventoryGateway.reserve(sku, quantity)) {
            return CheckoutResult.outOfStock();
        }
        return CheckoutResult.paymentPending(paymentDeadlineCalculator.createDeadline());
    }
}
