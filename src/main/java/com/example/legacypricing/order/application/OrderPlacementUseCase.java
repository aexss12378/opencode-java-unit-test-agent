package com.example.legacypricing.order.application;

import com.example.legacypricing.order.OrderPlacementCommand;
import com.example.legacypricing.order.OrderPlacementResult;

@FunctionalInterface
public interface OrderPlacementUseCase {

    OrderPlacementResult place(OrderPlacementCommand command);
}
