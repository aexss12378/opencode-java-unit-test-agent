package com.example.legacypricing.checkout.port;

public interface InventoryGateway {

    boolean reserve(String sku, int quantity);
}
