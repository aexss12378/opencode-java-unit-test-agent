package com.example.legacypricing.order.port;

import com.example.legacypricing.order.StockReservation;
import java.util.Optional;

/**
 * 庫存保留外部服務邊界。
 *
 * 訂單放行流程中，先呼叫 {@link #reserve} 保留庫存；若後續付款授權被拒絕，
 * 則呼叫 {@link #release} 釋放該保留。
 */
public interface StockReservationGateway {

    /**
     * 為指定商品保留指定數量。
     *
     * 若庫存不足則回傳 {@code Optional.empty()}，且不得呼叫付款協作者。
     * 保留成功時回傳的 {@link StockReservation} 必須包含與參數相同的 {@code sku} 與 {@code quantity}。
     */
    Optional<StockReservation> reserve(String sku, int quantity);

    /**
     * 釋放指定的庫存保留，並恢復對應商品的可售庫存。
     *
     * 若指定的保留不存在或已釋放，則拋出 {@link IllegalStateException}。
     */
    void release(StockReservation reservation);
}
