package com.example.legacypricing.order.service;

import com.example.legacypricing.order.OrderPlacementCommand;
import com.example.legacypricing.order.OrderPlacementResult;

/**
 * Contract for placing orders, including input validation, idempotency,
 * risk assessment, stock reservation, payment authorization, compensation,
 * and payment deadline calculation.
 */
public interface OrderPlacementUseCase {

    /**
     * Validates the command, checks idempotency, assesses risk, reserves stock,
     * authorizes payment, compensates on failure, and persists the result.
     *
     * <p>Validation failures throw {@code IllegalArgumentException} without
     * calling any collaborators. Risk scores outside 0–100 throw
     * {@code IllegalStateException}. Idempotency conflicts throw
     * {@code IdempotencyConflictException}.
     *
     * <p>Returns one of: {@code ACCEPTED}, {@code MANUAL_REVIEW},
     * {@code OUT_OF_STOCK}, {@code RISK_REJECTED}, or {@code PAYMENT_DECLINED}.
     */
    OrderPlacementResult place(OrderPlacementCommand command);
}
