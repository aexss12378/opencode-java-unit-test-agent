package com.example.legacypricing.order.dao;

import com.example.legacypricing.order.entity.OrderPlacementEntity;
import java.util.Objects;
import java.util.Optional;
import org.springframework.stereotype.Repository;

@Repository
public class JpaOrderPlacementDao implements OrderPlacementDao {

    private final SpringDataOrderPlacementRepository repository;

    public JpaOrderPlacementDao(SpringDataOrderPlacementRepository repository) {
        this.repository = Objects.requireNonNull(repository, "repository");
    }

    @Override
    public Optional<OrderPlacementEntity> findByIdempotencyKey(String idempotencyKey) {
        return repository.findByIdempotencyKey(idempotencyKey);
    }

    @Override
    public OrderPlacementEntity save(OrderPlacementEntity placement) {
        return repository.save(placement);
    }
}
