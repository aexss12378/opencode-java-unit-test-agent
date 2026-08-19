package com.example.legacypricing.order;

/**
 * Payment authorization result returned by the payment gateway.
 *
 * @param approved whether the payment was authorized
 * @param authorizationId the authorization identifier, or null if declined
 */
public record PaymentAuthorization(boolean approved, String authorizationId) {

    /**
     * Creates a payment authorization representing an approved payment.
     *
     * @param authorizationId the authorization identifier returned by the payment gateway
     * @return a payment authorization with {@code approved} set to {@code true}
     */
    public static PaymentAuthorization approved(String authorizationId) {
        return new PaymentAuthorization(true, authorizationId);
    }

    /**
     * Creates a payment authorization representing a declined payment.
     *
     * @return a payment authorization with {@code approved} set to {@code false} and {@code authorizationId} set to {@code null}
     */
    public static PaymentAuthorization declined() {
        return new PaymentAuthorization(false, null);
    }
}
