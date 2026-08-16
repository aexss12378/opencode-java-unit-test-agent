package com.example.legacypricing.order.dao;

import com.example.legacypricing.order.entity.OrderPlacementEntity;
import java.util.Optional;

/**
 * 訂單放行資料存取介面。
 *
 * 負責冪等鍵查詢與訂單放行結果的持久化。冪等檢查時以冪等鍵精確查找先前結果；
 * 儲存時冪等鍵、指紋與業務結果欄位不得被改寫。
 */
public interface OrderPlacementDao {

    /**
     * 根據冪等鍵查找先前儲存的訂單放行記錄。
     *
     * 用於訂單放行流程的冪等檢查。冪等鍵採完全相同比對，區分大小寫且不做正規化。
     * 若找不到對應記錄則回傳 {@code Optional.empty()}。
     *
     * @param idempotencyKey 冪等鍵
     * @return 找到的訂單放行實體，若不存在則為空
     */
    Optional<OrderPlacementEntity> findByIdempotencyKey(String idempotencyKey);

    /**
     * 儲存訂單放行實體。
     *
     * 新增記錄時冪等鍵、指紋與業務結果欄位不得被改寫。
     *
     * @param placement 要儲存的訂單放行實體
     * @return 儲存後的實體（可能包含資料庫產生的 ID）
     */
    OrderPlacementEntity save(OrderPlacementEntity placement);
}
