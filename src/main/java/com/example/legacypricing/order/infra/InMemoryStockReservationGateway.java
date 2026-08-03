package com.example.legacypricing.order.infra;

import com.example.legacypricing.order.StockReservation;
import com.example.legacypricing.order.port.StockReservationGateway;
import java.util.HashMap;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicLong;
import org.springframework.stereotype.Component;

@Component
public final class InMemoryStockReservationGateway implements StockReservationGateway {

    private final Map<String, Integer> availableStock = new HashMap<>(Map.of(
            "SKU-BOOK", 20,
            "SKU-LAPTOP", 5,
            "SKU-MONITOR", 8
    ));
    private final Map<String, StockReservation> reservations = new HashMap<>();
    private final AtomicLong sequence = new AtomicLong();

    @Override
    public synchronized Optional<StockReservation> reserve(String sku, int quantity) {
        int available = availableStock.getOrDefault(sku, 0);
        if (available < quantity) {
            return Optional.empty();
        }
        availableStock.put(sku, available - quantity);
        String reservationId = "RSV-" + sequence.incrementAndGet();
        StockReservation reservation = new StockReservation(reservationId, sku, quantity);
        reservations.put(reservationId, reservation);
        return Optional.of(reservation);
    }

    @Override
    public synchronized void release(StockReservation reservation) {
        StockReservation removed = reservations.remove(reservation.reservationId());
        if (removed == null) {
            throw new IllegalStateException("reservation is not active");
        }
        availableStock.merge(removed.sku(), removed.quantity(), Integer::sum);
    }
}
