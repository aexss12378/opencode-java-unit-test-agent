package com.example.legacypricing;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class OrderPricingServiceBaselineTest {

    @Test
    void calculatesExactDiscountForRegularCustomer() {
        DiscountPolicy policy = customerId -> new BigDecimal("10");
        OrderPricingService service = new OrderPricingService(policy);

        BigDecimal discount = service.calculateDiscount("customer-001", new BigDecimal("100.00"));

        assertEquals(new BigDecimal("10.00"), discount);
    }
}
