package com.example.legacypricing.pricing.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.when;

import com.example.legacypricing.pricing.calculator.DiscountCalculator;
import com.example.legacypricing.pricing.calculator.ShippingFeeCalculator;
import com.example.legacypricing.pricing.config.PricingProperties;
import com.example.legacypricing.pricing.dto.OrderQuote;
import java.math.BigDecimal;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

@ExtendWith(MockitoExtension.class)
class OrderQuoteServiceTest {

    @Mock
    private DiscountCalculator discountCalculator;

    @Mock
    private ShippingFeeCalculator shippingFeeCalculator;

    @Mock
    private PricingProperties properties;

    @InjectMocks
    private OrderQuoteService orderQuoteService;

    // UT-001: customerId 為 null 時應拋出 IllegalArgumentException
    @Test
    void quote_whenCustomerIdIsNull_shouldThrowIllegalArgumentException() {
        // Given
        String customerId = null;
        BigDecimal unitPrice = new BigDecimal("100.00");
        int quantity = 2;

        // When & Then
        assertThatThrownBy(() -> orderQuoteService.quote(customerId, unitPrice, quantity))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("customerId must not be blank");
    }

    // UT-002: customerId 為空白字串時應拋出 IllegalArgumentException
    @Test
    void quote_whenCustomerIdIsBlank_shouldThrowIllegalArgumentException() {
        // Given
        String customerId = "   ";
        BigDecimal unitPrice = new BigDecimal("100.00");
        int quantity = 2;

        // When & Then
        assertThatThrownBy(() -> orderQuoteService.quote(customerId, unitPrice, quantity))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("customerId must not be blank");
    }

    // UT-003: unitPrice 為 null 時應拋出 IllegalArgumentException
    @Test
    void quote_whenUnitPriceIsNull_shouldThrowIllegalArgumentException() {
        // Given
        String customerId = "CUST001";
        BigDecimal unitPrice = null;
        int quantity = 2;

        // When & Then
        assertThatThrownBy(() -> orderQuoteService.quote(customerId, unitPrice, quantity))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("unitPrice must not be null or negative");
    }

    // UT-004: unitPrice 為負數時應拋出 IllegalArgumentException
    @Test
    void quote_whenUnitPriceIsNegative_shouldThrowIllegalArgumentException() {
        // Given
        String customerId = "CUST001";
        BigDecimal unitPrice = new BigDecimal("-10.00");
        int quantity = 2;

        // When & Then
        assertThatThrownBy(() -> orderQuoteService.quote(customerId, unitPrice, quantity))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("unitPrice must not be null or negative");
    }

    // UT-005: quantity 為 0 時應拋出 IllegalArgumentException
    @Test
    void quote_whenQuantityIsZero_shouldThrowIllegalArgumentException() {
        // Given
        String customerId = "CUST001";
        BigDecimal unitPrice = new BigDecimal("100.00");
        int quantity = 0;

        // When & Then
        assertThatThrownBy(() -> orderQuoteService.quote(customerId, unitPrice, quantity))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("quantity must be positive");
    }

    // UT-006: quantity 為負數時應拋出 IllegalArgumentException
    @Test
    void quote_whenQuantityIsNegative_shouldThrowIllegalArgumentException() {
        // Given
        String customerId = "CUST001";
        BigDecimal unitPrice = new BigDecimal("100.00");
        int quantity = -1;

        // When & Then
        assertThatThrownBy(() -> orderQuoteService.quote(customerId, unitPrice, quantity))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("quantity must be positive");
    }

    // UT-007: 正常計算報價
    // subtotal = unitPrice * quantity = 100.00 * 2 = 200.00
    // discountedSubtotal = subtotal - discount = 200.00 - 20.00 = 180.00
    // tax = discountedSubtotal * taxRate = 180.00 * 0.05 = 9.00
    // total = discountedSubtotal + tax + shippingFee = 180.00 + 9.00 + 60.00 = 249.00
    @Test
    void quote_withValidInputs_shouldCalculateQuoteCorrectly() {
        // Given
        String customerId = "CUST001";
        BigDecimal unitPrice = new BigDecimal("100.00");
        int quantity = 2;
        BigDecimal subtotal = new BigDecimal("200.00");
        BigDecimal discount = new BigDecimal("20.00");
        BigDecimal discountedSubtotal = new BigDecimal("180.00");
        BigDecimal tax = new BigDecimal("9.00");
        BigDecimal shippingFee = new BigDecimal("60.00");
        BigDecimal total = new BigDecimal("249.00");

        lenient().when(properties.taxRate()).thenReturn(new BigDecimal("0.05"));
        when(discountCalculator.calculateDiscount(customerId, subtotal)).thenReturn(discount);
        when(shippingFeeCalculator.calculateShippingFee(discountedSubtotal)).thenReturn(shippingFee);

        // When
        OrderQuote result = orderQuoteService.quote(customerId, unitPrice, quantity);

        // Then
        assertThat(result.subtotal()).isEqualByComparingTo(subtotal);
        assertThat(result.discount()).isEqualByComparingTo(discount);
        assertThat(result.discountedSubtotal()).isEqualByComparingTo(discountedSubtotal);
        assertThat(result.tax()).isEqualByComparingTo(tax);
        assertThat(result.shippingFee()).isEqualByComparingTo(shippingFee);
        assertThat(result.total()).isEqualByComparingTo(total);
    }

    // UT-008: 驗證 tax 的 HALF_UP 四捨五入
    // discountedSubtotal = 100.005, taxRate = 0.05
    // tax = 100.005 * 0.05 = 5.00025 → HALF_UP → 5.00
    @Test
    void quote_shouldRoundTaxUsingHalfUp() {
        // Given
        String customerId = "CUST001";
        BigDecimal unitPrice = new BigDecimal("50.0025");
        int quantity = 2;
        BigDecimal discount = BigDecimal.ZERO;
        BigDecimal shippingFee = BigDecimal.ZERO;

        lenient().when(properties.taxRate()).thenReturn(new BigDecimal("0.05"));
        when(discountCalculator.calculateDiscount(any(), any())).thenReturn(discount);
        when(shippingFeeCalculator.calculateShippingFee(any())).thenReturn(shippingFee);

        // When
        OrderQuote result = orderQuoteService.quote(customerId, unitPrice, quantity);

        // Then
        // tax = 100.005 * 0.05 = 5.00025 → HALF_UP to 2 decimal places = 5.00
        assertThat(result.tax()).isEqualByComparingTo(new BigDecimal("5.00"));
    }

    // UT-009: 驗證 total 的 HALF_UP 四捨五入
    // discountedSubtotal = 100.005, tax = 5.00, shippingFee = 0.005
    // total = 100.005 + 5.00 + 0.005 = 105.01 (HALF_UP to 2 decimal places)
    @Test
    void quote_shouldRoundTotalUsingHalfUp() {
        // Given
        String customerId = "CUST001";
        BigDecimal unitPrice = new BigDecimal("50.0025");
        int quantity = 2;
        BigDecimal discount = BigDecimal.ZERO;
        BigDecimal shippingFee = new BigDecimal("0.005");

        lenient().when(properties.taxRate()).thenReturn(new BigDecimal("0.05"));
        when(discountCalculator.calculateDiscount(any(), any())).thenReturn(discount);
        when(shippingFeeCalculator.calculateShippingFee(any())).thenReturn(shippingFee);

        // When
        OrderQuote result = orderQuoteService.quote(customerId, unitPrice, quantity);

        // Then
        // total = 100.005 + 5.00 + 0.005 = 105.01 (HALF_UP to 2 decimal places)
        assertThat(result.total()).isEqualByComparingTo(new BigDecimal("105.01"));
    }
}
