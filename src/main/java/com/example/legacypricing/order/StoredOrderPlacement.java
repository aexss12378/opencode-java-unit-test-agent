package com.example.legacypricing.order;

public record StoredOrderPlacement(
        OrderPlacementCommand command,
        OrderPlacementResult result
) {
}
