package com.example.legacypricing.order.entity;

import com.example.legacypricing.order.OrderPlacementStatus;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import java.time.Instant;
import java.util.Objects;

@Entity
@Table(
        name = "order_placements",
        uniqueConstraints = @UniqueConstraint(
                name = "uk_order_placements_idempotency_key",
                columnNames = "idempotency_key"
        )
)
public class OrderPlacementEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "idempotency_key", nullable = false, length = 128)
    private String idempotencyKey;

    @Column(name = "request_fingerprint", nullable = false, length = 64)
    private String requestFingerprint;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 32)
    private OrderPlacementStatus status;

    @Column(name = "order_id", nullable = false, length = 64)
    private String orderId;

    @Column(name = "reservation_id", length = 64)
    private String reservationId;

    @Column(name = "authorization_id", length = 64)
    private String authorizationId;

    @Column(name = "payment_deadline")
    private Instant paymentDeadline;

    protected OrderPlacementEntity() {
    }

    public OrderPlacementEntity(
            String idempotencyKey,
            String requestFingerprint,
            OrderPlacementStatus status,
            String orderId,
            String reservationId,
            String authorizationId,
            Instant paymentDeadline
    ) {
        this.idempotencyKey = Objects.requireNonNull(idempotencyKey, "idempotencyKey");
        this.requestFingerprint = Objects.requireNonNull(
                requestFingerprint,
                "requestFingerprint"
        );
        this.status = Objects.requireNonNull(status, "status");
        this.orderId = Objects.requireNonNull(orderId, "orderId");
        this.reservationId = reservationId;
        this.authorizationId = authorizationId;
        this.paymentDeadline = paymentDeadline;
    }

    public Long getId() {
        return id;
    }

    public String getIdempotencyKey() {
        return idempotencyKey;
    }

    public String getRequestFingerprint() {
        return requestFingerprint;
    }

    public OrderPlacementStatus getStatus() {
        return status;
    }

    public String getOrderId() {
        return orderId;
    }

    public String getReservationId() {
        return reservationId;
    }

    public String getAuthorizationId() {
        return authorizationId;
    }

    public Instant getPaymentDeadline() {
        return paymentDeadline;
    }
}
