package com.example.legacypricing;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Objects;
import org.springframework.stereotype.Service;

@Service
public final class OrderQuoteService implements OrderQuoteUseCase {

    private final DiscountCalculator discountCalculator;
    private final ShippingFeeCalculator shippingFeeCalculator;
    private final PricingProperties properties;

    public OrderQuoteService(
            DiscountCalculator discountCalculator,
            ShippingFeeCalculator shippingFeeCalculator,
            PricingProperties properties
    ) {
        this.discountCalculator = Objects.requireNonNull(discountCalculator, "discountCalculator");
        this.shippingFeeCalculator = Objects.requireNonNull(shippingFeeCalculator, "shippingFeeCalculator");
        this.properties = Objects.requireNonNull(properties, "properties");
    }

    /**
     * Calculates an order quote using these rules:
     * <ul>
     *   <li>quantity must be positive and unit price must be non-null and non-negative;</li>
     *   <li>subtotal is unit price multiplied by quantity;</li>
     *   <li>tax is calculated from the subtotal after discount, excluding shipping;</li>
     *   <li>tax and total use HALF_UP rounding to two decimal places;</li>
     *   <li>total is discounted subtotal plus tax plus shipping.</li>
     * </ul>
     *
     * @throws IllegalArgumentException when customer ID is null or blank,
     *                                  unit price is null or negative, or quantity is not positive
     */
    @Override
    public OrderQuote quote(String customerId, BigDecimal unitPrice, int quantity) {
        if (customerId == null || customerId.isBlank()) {
            throw new IllegalArgumentException("customerId must not be blank");
        }
        if (unitPrice == null || unitPrice.signum() < 0) {
            throw new IllegalArgumentException("unitPrice must not be null or negative");
        }
        if (quantity <= 0) {
            throw new IllegalArgumentException("quantity must be positive");
        }

        BigDecimal subtotal = unitPrice.multiply(BigDecimal.valueOf(quantity));
        BigDecimal discount = discountCalculator.calculateDiscount(customerId, subtotal);
        BigDecimal discountedSubtotal = subtotal.subtract(discount);
        BigDecimal tax = discountedSubtotal
                .multiply(properties.taxRate())
                .setScale(2, RoundingMode.HALF_UP);
        BigDecimal shippingFee = shippingFeeCalculator.calculateShippingFee(discountedSubtotal);
        BigDecimal total = discountedSubtotal
                .add(tax)
                .add(shippingFee)
                .setScale(2, RoundingMode.HALF_UP);

        return new OrderQuote(subtotal, discount, discountedSubtotal, tax, shippingFee, total);
    }
}
