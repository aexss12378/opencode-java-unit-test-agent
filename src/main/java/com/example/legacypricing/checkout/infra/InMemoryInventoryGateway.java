package com.example.legacypricing.checkout.infra;

import com.example.legacypricing.checkout.port.InventoryGateway;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.stereotype.Component;

@Component
final class InMemoryInventoryGateway implements InventoryGateway {

    private final Map<String, Integer> stock = new ConcurrentHashMap<>(Map.of(
            "SKU-BOOK", 20,
            "SKU-LAPTOP", 5
    ));

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
