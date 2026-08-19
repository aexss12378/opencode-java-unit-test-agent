package com.example.legacypricing.pricing.calculator;

import java.math.BigDecimal;

/**
 * 計算並回傳指定顧客與小計對應的折扣金額。
 */
@FunctionalInterface
public interface DiscountCalculator {

    /**
     * 回傳折扣金額，固定為小數點後兩位，使用 HALF_UP 四捨五入。
     *
     * @throws IllegalArgumentException when customerId is null or blank, subtotal is null or negative, or the discount percentage from the policy is outside the range [0, 100]
     */
    BigDecimal calculateDiscount(String customerId, BigDecimal subtotal);
}
