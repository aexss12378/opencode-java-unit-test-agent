package com.example.legacypricing.order.application;

import com.example.legacypricing.order.IdempotencyConflictException;
import com.example.legacypricing.order.OrderPlacementCommand;
import com.example.legacypricing.order.OrderPlacementResult;
import com.example.legacypricing.order.OrderPlacementStatus;
import com.example.legacypricing.order.PaymentAuthorization;
import com.example.legacypricing.order.StockReservation;
import com.example.legacypricing.order.StoredOrderPlacement;
import com.example.legacypricing.order.config.OrderWorkflowProperties;
import com.example.legacypricing.order.port.IdempotencyStore;
import com.example.legacypricing.order.port.PaymentGateway;
import com.example.legacypricing.order.port.RiskAssessmentGateway;
import com.example.legacypricing.order.port.StockReservationGateway;
import java.math.BigDecimal;
import java.time.Clock;
import java.time.Instant;
import java.util.Objects;
import java.util.Optional;
import org.springframework.stereotype.Service;

@Service
public final class OrderPlacementService implements OrderPlacementUseCase {

    private final RiskAssessmentGateway riskAssessmentGateway;
    private final StockReservationGateway stockReservationGateway;
    private final PaymentGateway paymentGateway;
    private final IdempotencyStore idempotencyStore;
    private final OrderWorkflowProperties properties;
    private final Clock clock;

    public OrderPlacementService(
            RiskAssessmentGateway riskAssessmentGateway,
            StockReservationGateway stockReservationGateway,
            PaymentGateway paymentGateway,
            IdempotencyStore idempotencyStore,
            OrderWorkflowProperties properties,
            Clock clock
    ) {
        this.riskAssessmentGateway = Objects.requireNonNull(
                riskAssessmentGateway,
                "riskAssessmentGateway"
        );
        this.stockReservationGateway = Objects.requireNonNull(
                stockReservationGateway,
                "stockReservationGateway"
        );
        this.paymentGateway = Objects.requireNonNull(paymentGateway, "paymentGateway");
        this.idempotencyStore = Objects.requireNonNull(idempotencyStore, "idempotencyStore");
        this.properties = Objects.requireNonNull(properties, "properties");
        this.clock = Objects.requireNonNull(clock, "clock");
    }

    /**
     * Places an order according to the validation, idempotency, risk, stock,
     * payment, compensation and deadline rules in
     * {@code docs/order-placement-rules.md}.
     */
    @Override
    public OrderPlacementResult place(OrderPlacementCommand command) {
        validate(command);

        Optional<StoredOrderPlacement> previous = idempotencyStore.find(
                command.idempotencyKey()
        );
        if (previous.isPresent()) {
            return reusePrevious(command, previous.orElseThrow());
        }

        int riskScore = riskAssessmentGateway.assess(command.customerId(), command.total());
        if (riskScore < 0 || riskScore > 100) {
            throw new IllegalStateException("risk score must be between 0 and 100");
        }
        if (riskScore >= properties.rejectionScore()) {
            return persist(
                    command,
                    OrderPlacementResult.withoutReservation(
                            OrderPlacementStatus.RISK_REJECTED,
                            command.orderId()
                    )
            );
        }
        if (riskScore >= properties.manualReviewScore()) {
            return persist(
                    command,
                    OrderPlacementResult.withoutReservation(
                            OrderPlacementStatus.MANUAL_REVIEW,
                            command.orderId()
                    )
            );
        }

        Optional<StockReservation> reservation = Objects.requireNonNull(
                stockReservationGateway.reserve(command.sku(), command.quantity()),
                "reservation"
        );
        if (reservation.isEmpty()) {
            return persist(
                    command,
                    OrderPlacementResult.withoutReservation(
                            OrderPlacementStatus.OUT_OF_STOCK,
                            command.orderId()
                    )
            );
        }

        StockReservation reserved = reservation.orElseThrow();
        PaymentAuthorization authorization = Objects.requireNonNull(
                paymentGateway.authorize(
                        command.orderId(),
                        command.total(),
                        command.currency(),
                        command.paymentToken()
                ),
                "authorization"
        );
        if (!authorization.approved()) {
            stockReservationGateway.release(reserved);
            return persist(
                    command,
                    OrderPlacementResult.withoutReservation(
                            OrderPlacementStatus.PAYMENT_DECLINED,
                            command.orderId()
                    )
            );
        }
        if (authorization.authorizationId() == null
                || authorization.authorizationId().isBlank()) {
            throw new IllegalStateException("approved payment must have an authorization ID");
        }

        Instant paymentDeadline = clock.instant().plus(properties.paymentAuthorizationWindow());
        return persist(
                command,
                OrderPlacementResult.accepted(
                        command.orderId(),
                        reserved.reservationId(),
                        authorization.authorizationId(),
                        paymentDeadline
                )
        );
    }

    private OrderPlacementResult reusePrevious(
            OrderPlacementCommand command,
            StoredOrderPlacement previous
    ) {
        if (!previous.command().equals(command)) {
            throw new IdempotencyConflictException(
                    "idempotency key was already used for another request"
            );
        }
        return previous.result();
    }

    private OrderPlacementResult persist(
            OrderPlacementCommand command,
            OrderPlacementResult result
    ) {
        idempotencyStore.save(
                command.idempotencyKey(),
                new StoredOrderPlacement(command, result)
        );
        return result;
    }

    private void validate(OrderPlacementCommand command) {
        Objects.requireNonNull(command, "command");
        requireText(command.orderId(), "orderId");
        requireText(command.idempotencyKey(), "idempotencyKey");
        requireText(command.customerId(), "customerId");
        requireText(command.sku(), "sku");
        requireText(command.currency(), "currency");
        requireText(command.paymentToken(), "paymentToken");
        if (command.quantity() <= 0) {
            throw new IllegalArgumentException("quantity must be positive");
        }
        BigDecimal total = Objects.requireNonNull(command.total(), "total");
        if (total.signum() <= 0) {
            throw new IllegalArgumentException("total must be positive");
        }
        if (total.compareTo(properties.maximumOrderTotal()) > 0) {
            throw new IllegalArgumentException("total exceeds the configured maximum");
        }
        if (!properties.supportedCurrency().equals(command.currency())) {
            throw new IllegalArgumentException("unsupported currency");
        }
    }

    private void requireText(String value, String field) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(field + " must not be blank");
        }
    }
}
