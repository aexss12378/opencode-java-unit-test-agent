package com.example.legacypricing.checkout.infra;

import com.example.legacypricing.checkout.port.InventoryGateway;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.stereotype.Component;

/**
 * Spring-managed in-memory implementation of {@link InventoryGateway} backed by a ConcurrentHashMap. Stock levels are pre-seeded with SKU-BOOK (20) and SKU-LAPTOP (5) at initialization.
 */
@Component
final class InMemoryInventoryGateway implements InventoryGateway {

    private final Map<String, Integer> stock = new ConcurrentHashMap<>(Map.of(
            "SKU-BOOK", 20,
            "SKU-LAPTOP", 5
    ));

    /**
     * Atomically checks whether the requested quantity is available for the given SKU
     * and deducts it on success. Returns false without modifying stock when the SKU
     * is unknown or the available quantity is less than requested.
     */
    @Override
    public synchronized boolean reserve(String sku, int quantity) {
        Integer available = stock.get(sku);
        if (available == null || available < quantity) {
            return false;
        }
        stock.put(sku, available - quantity);
        return true;
    }
}
