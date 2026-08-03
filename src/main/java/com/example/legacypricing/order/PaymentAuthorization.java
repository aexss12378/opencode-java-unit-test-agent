package com.example.legacypricing.order;

public record PaymentAuthorization(boolean approved, String authorizationId) {

    public static PaymentAuthorization approved(String authorizationId) {
        return new PaymentAuthorization(true, authorizationId);
    }

    public static PaymentAuthorization declined() {
        return new PaymentAuthorization(false, null);
    }
}
