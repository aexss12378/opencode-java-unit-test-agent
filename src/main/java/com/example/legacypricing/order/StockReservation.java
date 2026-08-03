package com.example.legacypricing.order;

public record StockReservation(
        String reservationId,
        String sku,
        int quantity
) {
}
