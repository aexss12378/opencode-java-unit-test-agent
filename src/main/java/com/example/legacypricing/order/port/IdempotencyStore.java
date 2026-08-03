package com.example.legacypricing.order.port;

import com.example.legacypricing.order.StoredOrderPlacement;
import java.util.Optional;

public interface IdempotencyStore {

    Optional<StoredOrderPlacement> find(String idempotencyKey);

    void save(String idempotencyKey, StoredOrderPlacement placement);
}
