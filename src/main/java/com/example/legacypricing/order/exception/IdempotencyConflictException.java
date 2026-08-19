package com.example.legacypricing.order.exception;

/**
 * Domain exception thrown when an idempotency key is reused with a different command.
 *
 * Per the order placement rules, this exception is raised during idempotency checking
 * when the same idempotency key matches a previously stored result with a different
 * request fingerprint. No risk, inventory, or payment collaborators are invoked.
 */
public final class IdempotencyConflictException extends RuntimeException {

    /**
     * Creates an instance with the given detail message.
     *
     * @param message the detail message
     */
    public IdempotencyConflictException(String message) {
        super(message);
    }
}
