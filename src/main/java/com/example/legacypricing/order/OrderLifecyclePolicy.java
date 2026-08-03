package com.example.legacypricing.order;

import java.time.Clock;
import java.time.Instant;
import java.util.Objects;
import org.springframework.stereotype.Component;

@Component
public final class OrderLifecyclePolicy {

    private final Clock clock;

    public OrderLifecyclePolicy(Clock clock) {
        this.clock = Objects.requireNonNull(clock, "clock");
    }

    /**
     * Applies the transition table defined by {@code docs/order-lifecycle-rules.md}.
     * A payment event occurring exactly at the deadline is still on time.
     */
    public OrderStatus transition(
            OrderStatus current,
            OrderEvent event,
            Instant paymentDeadline
    ) {
        Objects.requireNonNull(current, "current");
        Objects.requireNonNull(event, "event");

        return switch (current) {
            case DRAFT -> requireEvent(
                    current,
                    event,
                    OrderEvent.SUBMIT,
                    OrderStatus.PAYMENT_PENDING
            );
            case PAYMENT_PENDING -> transitionPending(event, paymentDeadline);
            case CONFIRMED -> requireEvent(
                    current,
                    event,
                    OrderEvent.START_FULFILLMENT,
                    OrderStatus.FULFILLING
            );
            case FULFILLING -> requireEvent(
                    current,
                    event,
                    OrderEvent.SHIP,
                    OrderStatus.SHIPPED
            );
            case SHIPPED, CANCELLED, EXPIRED -> throw invalidTransition(current, event);
        };
    }

    private OrderStatus transitionPending(OrderEvent event, Instant paymentDeadline) {
        Objects.requireNonNull(paymentDeadline, "paymentDeadline");
        if (event != OrderEvent.AUTHORIZE_PAYMENT && event != OrderEvent.CANCEL) {
            throw invalidTransition(OrderStatus.PAYMENT_PENDING, event);
        }
        if (clock.instant().isAfter(paymentDeadline)) {
            return OrderStatus.EXPIRED;
        }
        return switch (event) {
            case AUTHORIZE_PAYMENT -> OrderStatus.CONFIRMED;
            case CANCEL -> OrderStatus.CANCELLED;
            default -> throw invalidTransition(OrderStatus.PAYMENT_PENDING, event);
        };
    }

    private OrderStatus requireEvent(
            OrderStatus current,
            OrderEvent actual,
            OrderEvent expected,
            OrderStatus target
    ) {
        if (actual != expected) {
            throw invalidTransition(current, actual);
        }
        return target;
    }

    private IllegalStateException invalidTransition(OrderStatus current, OrderEvent event) {
        return new IllegalStateException("invalid transition: " + current + " / " + event);
    }
}
