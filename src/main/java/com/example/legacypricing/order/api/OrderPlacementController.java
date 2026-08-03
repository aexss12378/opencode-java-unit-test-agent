package com.example.legacypricing.order.api;

import com.example.legacypricing.order.OrderPlacementResult;
import com.example.legacypricing.order.application.OrderPlacementUseCase;
import jakarta.validation.Valid;
import java.util.Objects;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/order-placements")
public final class OrderPlacementController {

    private final OrderPlacementUseCase orderPlacementUseCase;

    public OrderPlacementController(OrderPlacementUseCase orderPlacementUseCase) {
        this.orderPlacementUseCase = Objects.requireNonNull(
                orderPlacementUseCase,
                "orderPlacementUseCase"
        );
    }

    /**
     * Maps ACCEPTED to 201, MANUAL_REVIEW to 202, OUT_OF_STOCK to 409,
     * and both rejection outcomes to 422.
     */
    @PostMapping
    public ResponseEntity<OrderPlacementResponse> place(
            @Valid @RequestBody OrderPlacementRequest request
    ) {
        OrderPlacementResult result = orderPlacementUseCase.place(request.toCommand());
        HttpStatus status = switch (result.status()) {
            case ACCEPTED -> HttpStatus.CREATED;
            case MANUAL_REVIEW -> HttpStatus.ACCEPTED;
            case OUT_OF_STOCK -> HttpStatus.CONFLICT;
            case RISK_REJECTED, PAYMENT_DECLINED -> HttpStatus.UNPROCESSABLE_ENTITY;
        };
        return ResponseEntity.status(status).body(OrderPlacementResponse.from(result));
    }
}
