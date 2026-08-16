package com.example.legacypricing.checkout.model;

/**
 * Outcome of a checkout attempt.
 *
 * <p>Returned by {@link com.example.legacypricing.checkout.model.CheckoutResult} to indicate
 * whether inventory was successfully reserved and a payment deadline was created
 * ({@link #PAYMENT_PENDING}), or whether the reservation failed due to insufficient stock
 * ({@link #OUT_OF_STOCK}).
 */
public enum CheckoutStatus {
    /**
     * Inventory was reserved and a payment deadline was created.
     */
    PAYMENT_PENDING,
    /**
     * Inventory reservation failed due to insufficient stock; no payment deadline is associated.
     */
    OUT_OF_STOCK
}
