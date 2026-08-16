package com.example.legacypricing.order.infra;

import com.example.legacypricing.order.StockReservation;
import com.example.legacypricing.order.port.StockReservationGateway;
import java.util.HashMap;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicLong;
import org.springframework.stereotype.Component;

/**
 * In-memory implementation of {@code StockReservationGateway} backed by a mutable map.
 *
 * Stock is pre-populated with SKU-BOOK (20), SKU-LAPTOP (5), and SKU-MONITOR (8).
 * Reservation IDs are generated as {@code RSV-} followed by a monotonically increasing sequence number.
 */
@Component
public final class InMemoryStockReservationGateway implements StockReservationGateway {

    private final Map<String, Integer> availableStock = new HashMap<>(Map.of(
            "SKU-BOOK", 20,
            "SKU-LAPTOP", 5,
            "SKU-MONITOR", 8
    ));
    private final Map<String, StockReservation> reservations = new HashMap<>();
    private final AtomicLong sequence = new AtomicLong();

    /**
     * Reserves the requested quantity for the given SKU.
     *
     * If the available stock is less than the requested quantity, returns {@code Optional.empty()}.
     * Otherwise deducts the quantity from available stock, creates a reservation with an
     * auto-generated ID, and returns it wrapped in {@code Optional}.
     *
     * @param sku the stock keeping unit identifier
     * @param quantity the number of units to reserve
     * @return an optional containing the created reservation, or empty if insufficient stock
     */
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

    /**
     * Releases the given reservation and restores its quantity to available stock.
     *
     * Throws {@code IllegalStateException} if the reservation is not currently active.
     *
     * @param reservation the reservation to release
     * @throws IllegalStateException if the reservation is not active
     */
    @Override
    public synchronized void release(StockReservation reservation) {
        StockReservation removed = reservations.remove(reservation.reservationId());
        if (removed == null) {
            throw new IllegalStateException("reservation is not active");
        }
        availableStock.merge(removed.sku(), removed.quantity(), Integer::sum);
    }
}
