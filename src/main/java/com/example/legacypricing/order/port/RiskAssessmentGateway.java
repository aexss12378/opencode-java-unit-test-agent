package com.example.legacypricing.order.port;

import java.math.BigDecimal;

/**
 * 風險評估協作介面。訂單放行流程藉此取得顧客風險分數，據以決定放行、人工審查或拒絕。
 */
@FunctionalInterface
public interface RiskAssessmentGateway {

    /**
     * 評估指定顧客的風險分數。回傳值介於 0 到 100（含端點）；超出範圍時由實作或呼叫端拋出 {@code IllegalStateException}。
     *
     * 分數用於訂單放行流程的風險分流：
     * <ul>
     * <li>大於或等於拒絕門檻 → {@code RISK_REJECTED}</li>
     * <li>大於或等於人工審查門檻、但低於拒絕門檻 → {@code MANUAL_REVIEW}</li>
     * <li>低於人工審查門檻 → 進入庫存保留</li>
     * </ul>
     *
     * @param  customerId 顧客編號，不可為 {@code null}
     * @param  total       訂單總額，不可為 {@code null}
     * @throws IllegalStateException 當回傳分數不在 0 到 100 之間時
     */
    int assess(String customerId, BigDecimal total);
}
