package com.example.legacypricing.order;

/**
 * Domain events that drive order lifecycle transitions as defined by the order lifecycle specification.
 */
public enum OrderEvent {
    /**
     * Transition from DRAFT to PAYMENT_PENDING.
     */
    SUBMIT,
    /**
     * Transition from PAYMENT_PENDING to CONFIRMED when payment is authorized before the deadline.
     */
    AUTHORIZE_PAYMENT,
    /**
     * Transition from PAYMENT_PENDING to CANCELLED.
     */
    CANCEL,
    /**
     * Transition from CONFIRMED to FULFILLING.
     */
    START_FULFILLMENT,
    /**
     * Transition from FULFILLING to SHIPPED.
     */
    SHIP
}
