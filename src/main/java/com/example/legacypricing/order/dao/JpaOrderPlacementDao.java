package com.example.legacypricing.order.dao;

import com.example.legacypricing.order.entity.OrderPlacementEntity;
import java.util.Objects;
import java.util.Optional;
import org.springframework.stereotype.Repository;

/**
 * JPA-based implementation of {@link OrderPlacementDao} backed by Spring Data JPA.
 */
@Repository
public class JpaOrderPlacementDao implements OrderPlacementDao {

    private final SpringDataOrderPlacementRepository repository;

    /**
     * @param repository the Spring Data repository; must not be null
     */
    public JpaOrderPlacementDao(SpringDataOrderPlacementRepository repository) {
        this.repository = Objects.requireNonNull(repository, "repository");
    }

    /**
     * Finds an order placement by its idempotency key through the underlying Spring Data repository.
     *
     * @param idempotencyKey the idempotency key to look up
     * @return the optional order placement entity, empty if not found
     */
    @Override
    public Optional<OrderPlacementEntity> findByIdempotencyKey(String idempotencyKey) {
        return repository.findByIdempotencyKey(idempotencyKey);
    }

    /**
     * Persists the given order placement through the underlying Spring Data repository.
     *
     * @param placement the order placement to save; must not be null
     * @return the saved order placement entity
     * @throws IllegalArgumentException when placement is null
     */
    @Override
    public OrderPlacementEntity save(OrderPlacementEntity placement) {
        return repository.save(placement);
    }
}
