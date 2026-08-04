package com.example.legacypricing.order.service;

import com.example.legacypricing.order.OrderPlacementCommand;
import com.example.legacypricing.order.OrderPlacementResult;

public interface OrderPlacementUseCase {

    OrderPlacementResult place(OrderPlacementCommand command);
}
