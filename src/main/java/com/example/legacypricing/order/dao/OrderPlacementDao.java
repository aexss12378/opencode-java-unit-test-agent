package com.example.legacypricing.order.dao;

import com.example.legacypricing.order.entity.OrderPlacementEntity;
import java.util.Optional;

public interface OrderPlacementDao {

    Optional<OrderPlacementEntity> findByIdempotencyKey(String idempotencyKey);

    OrderPlacementEntity save(OrderPlacementEntity placement);
}
