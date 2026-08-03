package com.example.legacypricing;

public interface InventoryGateway {

    boolean reserve(String sku, int quantity);
}
