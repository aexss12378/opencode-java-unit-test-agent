package com.example.legacypricing.order.dao;

import com.example.legacypricing.order.entity.OrderPlacementEntity;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface SpringDataOrderPlacementRepository
        extends JpaRepository<OrderPlacementEntity, Long> {

    Optional<OrderPlacementEntity> findByIdempotencyKey(String idempotencyKey);
}
