package com.example.legacypricing.checkout.model;

/**
 * Outcome of a checkout operation. {@code PAYMENT_PENDING} means inventory was reserved and a payment deadline was created; {@code OUT_OF_STOCK} means inventory reservation failed.
 */
public enum CheckoutStatus {
    /**
     * Inventory was reserved and a payment deadline was created.
     */
    PAYMENT_PENDING,
    /**
     * Inventory reservation failed.
     */
    OUT_OF_STOCK
}
