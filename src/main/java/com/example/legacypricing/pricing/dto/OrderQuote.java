package com.example.legacypricing.pricing.dto;

import java.math.BigDecimal;

/**
 * API response for the pricing quote endpoint.
 *
 * All monetary values are in the currency of the order.
 *
 * @param subtotal the order subtotal before discount (unit price × quantity)
 * @param discount the discount amount applied to the subtotal
 * @param discountedSubtotal the subtotal after discount is applied
 * @param tax the tax calculated from the discounted subtotal, excluding shipping
 * @param shippingFee the shipping fee calculated from the discounted subtotal
 * @param total the final order total (discounted subtotal + tax + shipping fee)
 */
public record OrderQuote(
        BigDecimal subtotal,
        BigDecimal discount,
        BigDecimal discountedSubtotal,
        BigDecimal tax,
        BigDecimal shippingFee,
        BigDecimal total
) {
}
