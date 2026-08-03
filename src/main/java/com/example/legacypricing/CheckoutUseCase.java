package com.example.legacypricing;

public interface CheckoutUseCase {

    CheckoutResult checkout(String orderId, String sku, int quantity);
}
