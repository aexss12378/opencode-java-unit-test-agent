package com.example.legacypricing.order.service;

import com.example.legacypricing.order.OrderPlacementCommand;
import com.example.legacypricing.order.OrderPlacementResult;
import com.example.legacypricing.order.OrderPlacementStatus;
import com.example.legacypricing.order.PaymentAuthorization;
import com.example.legacypricing.order.StockReservation;
import com.example.legacypricing.order.config.OrderWorkflowProperties;
import com.example.legacypricing.order.dao.OrderPlacementDao;
import com.example.legacypricing.order.entity.OrderPlacementEntity;
import com.example.legacypricing.order.exception.IdempotencyConflictException;
import com.example.legacypricing.order.mapper.OrderPlacementPersistenceMapper;
import com.example.legacypricing.order.port.PaymentGateway;
import com.example.legacypricing.order.port.RiskAssessmentGateway;
import com.example.legacypricing.order.port.StockReservationGateway;
import com.example.legacypricing.order.util.OrderRequestFingerprint;
import com.example.legacypricing.order.vo.Money;
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
    private final OrderPlacementDao orderPlacementDao;
    private final OrderPlacementPersistenceMapper persistenceMapper;
    private final OrderWorkflowProperties properties;
    private final Clock clock;

    public OrderPlacementService(
            RiskAssessmentGateway riskAssessmentGateway,
            StockReservationGateway stockReservationGateway,
            PaymentGateway paymentGateway,
            OrderPlacementDao orderPlacementDao,
            OrderPlacementPersistenceMapper persistenceMapper,
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
        this.orderPlacementDao = Objects.requireNonNull(orderPlacementDao, "orderPlacementDao");
        this.persistenceMapper = Objects.requireNonNull(
                persistenceMapper,
                "persistenceMapper"
        );
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

        String requestFingerprint = OrderRequestFingerprint.sha256(command);
        Optional<OrderPlacementEntity> previous = orderPlacementDao.findByIdempotencyKey(
                command.idempotencyKey().value()
        );
        if (previous.isPresent()) {
            return reusePrevious(requestFingerprint, previous.orElseThrow());
        }

        Money total = command.total();
        int riskScore = riskAssessmentGateway.assess(command.customerId(), total.amount());
        if (riskScore < 0 || riskScore > 100) {
            throw new IllegalStateException("risk score must be between 0 and 100");
        }
        if (riskScore >= properties.rejectionScore()) {
            return persist(
                    command,
                    requestFingerprint,
                    OrderPlacementResult.withoutReservation(
                            OrderPlacementStatus.RISK_REJECTED,
                            command.orderId().value()
                    )
            );
        }
        if (riskScore >= properties.manualReviewScore()) {
            return persist(
                    command,
                    requestFingerprint,
                    OrderPlacementResult.withoutReservation(
                            OrderPlacementStatus.MANUAL_REVIEW,
                            command.orderId().value()
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
                    requestFingerprint,
                    OrderPlacementResult.withoutReservation(
                            OrderPlacementStatus.OUT_OF_STOCK,
                            command.orderId().value()
                    )
            );
        }

        StockReservation reserved = reservation.orElseThrow();
        PaymentAuthorization authorization = Objects.requireNonNull(
                paymentGateway.authorize(
                        command.orderId().value(),
                        total.amount(),
                        total.currency(),
                        command.paymentToken()
                ),
                "authorization"
        );
        if (!authorization.approved()) {
            stockReservationGateway.release(reserved);
            return persist(
                    command,
                    requestFingerprint,
                    OrderPlacementResult.withoutReservation(
                            OrderPlacementStatus.PAYMENT_DECLINED,
                            command.orderId().value()
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
                requestFingerprint,
                OrderPlacementResult.accepted(
                        command.orderId().value(),
                        reserved.reservationId(),
                        authorization.authorizationId(),
                        paymentDeadline
                )
        );
    }

    private OrderPlacementResult reusePrevious(
            String requestFingerprint,
            OrderPlacementEntity previous
    ) {
        if (!previous.getRequestFingerprint().equals(requestFingerprint)) {
            throw new IdempotencyConflictException(
                    "idempotency key was already used for another request"
            );
        }
        return persistenceMapper.toResult(previous);
    }

    private OrderPlacementResult persist(
            OrderPlacementCommand command,
            String requestFingerprint,
            OrderPlacementResult result
    ) {
        OrderPlacementEntity entity = persistenceMapper.toEntity(
                command.idempotencyKey().value(),
                requestFingerprint,
                result
        );
        orderPlacementDao.save(entity);
        return result;
    }

    private void validate(OrderPlacementCommand command) {
        if (command == null) {
            throw new IllegalArgumentException("command must not be null");
        }
        if (command.orderId() == null) {
            throw new IllegalArgumentException("orderId must not be null");
        }
        if (command.idempotencyKey() == null) {
            throw new IllegalArgumentException("idempotencyKey must not be null");
        }
        requireText(command.customerId(), "customerId");
        requireText(command.sku(), "sku");
        requireText(command.paymentToken(), "paymentToken");
        if (command.quantity() <= 0) {
            throw new IllegalArgumentException("quantity must be positive");
        }
        Money total = command.total();
        if (total == null) {
            throw new IllegalArgumentException("total must not be null");
        }
        BigDecimal amount = total.amount();
        if (amount.signum() <= 0) {
            throw new IllegalArgumentException("total must be positive");
        }
        if (amount.compareTo(properties.maximumOrderTotal()) > 0) {
            throw new IllegalArgumentException("total exceeds the configured maximum");
        }
        if (!properties.supportedCurrency().equals(total.currency())) {
            throw new IllegalArgumentException("unsupported currency");
        }
    }

    private void requireText(String value, String field) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(field + " must not be blank");
        }
    }
}
