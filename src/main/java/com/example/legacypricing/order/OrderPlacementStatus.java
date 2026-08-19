package com.example.legacypricing.order;

/**
 * Results of the order placement flow defined by {@code docs/order-placement-rules.md}.
 * Each constant maps to a distinct HTTP status code returned by the
 * {@code OrderPlacementController}.
 */
public enum OrderPlacementStatus {
    /**
     * Order accepted: risk passed, stock reserved, and payment authorized.
     */
    ACCEPTED,
    /**
     * Risk score is between the manual review threshold and the rejection
     * threshold. No stock or payment calls are made.
     */
    MANUAL_REVIEW,
    /**
     * Risk score is at or above the rejection threshold. No stock or
     * payment calls are made.
     */
    RISK_REJECTED,
    /**
     * Stock reservation failed. Payment is not called.
     */
    OUT_OF_STOCK,
    /**
     * Payment authorization failed; the reserved stock is released.
     */
    PAYMENT_DECLINED
}
