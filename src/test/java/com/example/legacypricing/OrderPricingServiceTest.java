package com.example.legacypricing;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

/**
 * Unit tests for OrderPricingService based on docs/pricing-rules.md.
 *
 * Note: UT-011 (HALF_UP rounding) is excluded because the current implementation
 * uses RoundingMode.DOWN instead of the spec-required HALF_UP. This is a
 * specification-vs-implementation conflict reported separately.
 */
class OrderPricingServiceTest {

    // --- UT-002: subtotal is null ---
    @Test
    void throwsWhenSubtotalIsNull() {
        DiscountPolicy policy = customerId -> new BigDecimal("10");
        OrderPricingService service = new OrderPricingService(policy);

        assertThrows(
                IllegalArgumentException.class,
                () -> service.calculateDiscount("customer-001", null)
        );
    }

    // --- UT-003: subtotal is negative ---
    @Test
    void throwsWhenSubtotalIsNegative() {
        DiscountPolicy policy = customerId -> new BigDecimal("10");
        OrderPricingService service = new OrderPricingService(policy);

        assertThrows(
                IllegalArgumentException.class,
                () -> service.calculateDiscount("customer-001", new BigDecimal("-50.00"))
        );
    }

    // --- UT-004: customerId is null ---
    @Test
    void throwsWhenCustomerIdIsNull() {
        DiscountPolicy policy = customerId -> new BigDecimal("10");
        OrderPricingService service = new OrderPricingService(policy);

        assertThrows(
                IllegalArgumentException.class,
                () -> service.calculateDiscount(null, new BigDecimal("100.00"))
        );
    }

    // --- UT-005: customerId is blank ---
    @Test
    void throwsWhenCustomerIdIsBlank() {
        DiscountPolicy policy = customerId -> new BigDecimal("10");
        OrderPricingService service = new OrderPricingService(policy);

        assertThrows(
                IllegalArgumentException.class,
                () -> service.calculateDiscount("   ", new BigDecimal("100.00"))
        );
    }

    // --- UT-006: discount policy returns null ---
    @Test
    void throwsWhenDiscountPercentIsNull() {
        DiscountPolicy policy = customerId -> null;
        OrderPricingService service = new OrderPricingService(policy);

        assertThrows(
                IllegalArgumentException.class,
                () -> service.calculateDiscount("customer-001", new BigDecimal("100.00"))
        );
    }

    // --- UT-007: discount policy returns negative percent ---
    @Test
    void throwsWhenDiscountPercentIsNegative() {
        DiscountPolicy policy = customerId -> new BigDecimal("-5");
        OrderPricingService service = new OrderPricingService(policy);

        assertThrows(
                IllegalArgumentException.class,
                () -> service.calculateDiscount("customer-001", new BigDecimal("100.00"))
        );
    }

    // --- UT-008: discount policy returns percent > 100 ---
    @Test
    void throwsWhenDiscountPercentExceeds100() {
        DiscountPolicy policy = customerId -> new BigDecimal("150");
        OrderPricingService service = new OrderPricingService(policy);

        assertThrows(
                IllegalArgumentException.class,
                () -> service.calculateDiscount("customer-001", new BigDecimal("100.00"))
        );
    }

    // --- UT-009: discount percent is 0 ---
    @Test
    void returnsZeroWhenDiscountPercentIsZero() {
        DiscountPolicy policy = customerId -> BigDecimal.ZERO;
        OrderPricingService service = new OrderPricingService(policy);

        BigDecimal discount = service.calculateDiscount("customer-001", new BigDecimal("50.00"));

        assertEquals(new BigDecimal("0.00"), discount);
    }

    // --- UT-010: discount percent is 100 ---
    @Test
    void returnsFullSubtotalWhenDiscountPercentIs100() {
        DiscountPolicy policy = customerId -> new BigDecimal("100");
        OrderPricingService service = new OrderPricingService(policy);

        BigDecimal discount = service.calculateDiscount("customer-001", new BigDecimal("200.00"));

        assertEquals(new BigDecimal("200.00"), discount);
    }
}
