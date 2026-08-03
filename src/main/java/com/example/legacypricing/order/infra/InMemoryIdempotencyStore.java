package com.example.legacypricing.order.infra;

import com.example.legacypricing.order.StoredOrderPlacement;
import com.example.legacypricing.order.port.IdempotencyStore;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.stereotype.Component;

@Component
public final class InMemoryIdempotencyStore implements IdempotencyStore {

    private final Map<String, StoredOrderPlacement> placements = new ConcurrentHashMap<>();

    @Override
    public Optional<StoredOrderPlacement> find(String idempotencyKey) {
        return Optional.ofNullable(placements.get(idempotencyKey));
    }

    @Override
    public void save(String idempotencyKey, StoredOrderPlacement placement) {
        placements.putIfAbsent(idempotencyKey, placement);
    }
}
