package com.example.legacypricing.order.port;

import com.example.legacypricing.order.StockReservation;
import java.util.Optional;

public interface StockReservationGateway {

    Optional<StockReservation> reserve(String sku, int quantity);

    void release(StockReservation reservation);
}
