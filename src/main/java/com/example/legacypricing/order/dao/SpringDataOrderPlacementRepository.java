package com.example.legacypricing.order.dao;

import com.example.legacypricing.order.entity.OrderPlacementEntity;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

/**
 * Spring Data JPA repository for {@code OrderPlacementEntity}.
 *
 * Provides persistence operations for order placement records, including
 * idempotency-key lookups used during order placement.
 */
public interface SpringDataOrderPlacementRepository
        extends JpaRepository<OrderPlacementEntity, Long> {

    /**
     * Finds an order placement by its idempotency key.
     *
     * This is the primary lookup used for idempotency checks during order
     * placement: if a record with the given key already exists, the previous
     * result is reused instead of re-executing the workflow.
     *
     * @param idempotencyKey the idempotency key to look up
     * @return an optional containing the matching order placement, or empty if none exists
     */
    Optional<OrderPlacementEntity> findByIdempotencyKey(String idempotencyKey);
}
