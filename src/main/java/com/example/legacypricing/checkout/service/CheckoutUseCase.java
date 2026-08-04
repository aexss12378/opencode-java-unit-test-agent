package com.example.legacypricing.checkout.service;

import com.example.legacypricing.checkout.model.CheckoutResult;

public interface CheckoutUseCase {

    CheckoutResult checkout(String orderId, String sku, int quantity);
}
