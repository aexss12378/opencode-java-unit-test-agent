package com.example.legacypricing.pricing.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.when;

import com.example.legacypricing.pricing.calculator.DiscountCalculator;
import com.example.legacypricing.pricing.calculator.ShippingFeeCalculator;
import com.example.legacypricing.pricing.config.PricingProperties;
import com.example.legacypricing.pricing.dto.OrderQuote;
import java.math.BigDecimal;
import java.time.Duration;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

@ExtendWith(MockitoExtension.class)
class OrderQuoteServiceTest {

    @Mock
    private DiscountCalculator discountCalculator;

    @Mock
    private ShippingFeeCalculator shippingFeeCalculator;

    private PricingProperties properties;

    private OrderQuoteService orderQuoteService;

    @BeforeEach
    void setUp() {
        properties = new PricingProperties(
                new BigDecimal("10.00"),
                new BigDecimal("20.00"),
                new BigDecimal("0.05"),
                new BigDecimal("100.00"),
                new BigDecimal("10.00"),
                Duration.ofHours(24)
        );
        orderQuoteService = new OrderQuoteService(discountCalculator, shippingFeeCalculator, properties);
    }

    // UT-001: Constructor 傳入 null discountCalculator 應拋出 NullPointerException
    @Test
    @DisplayName("Constructor with null discountCalculator throws NullPointerException")
    void constructor_withNullDiscountCalculator_throwsNullPointerException() {
        assertThatThrownBy(() -> new OrderQuoteService(null, shippingFeeCalculator, properties))
                .isInstanceOf(NullPointerException.class)
                .hasMessageContaining("discountCalculator");
    }

    // UT-002: Constructor 傳入 null shippingFeeCalculator 應拋出 NullPointerException
    @Test
    @DisplayName("Constructor with null shippingFeeCalculator throws NullPointerException")
    void constructor_withNullShippingFeeCalculator_throwsNullPointerException() {
        assertThatThrownBy(() -> new OrderQuoteService(discountCalculator, null, properties))
                .isInstanceOf(NullPointerException.class)
                .hasMessageContaining("shippingFeeCalculator");
    }

    // UT-003: Constructor 傳入 null properties 應拋出 NullPointerException
    @Test
    @DisplayName("Constructor with null properties throws NullPointerException")
    void constructor_withNullProperties_throwsNullPointerException() {
        assertThatThrownBy(() -> new OrderQuoteService(discountCalculator, shippingFeeCalculator, null))
                .isInstanceOf(NullPointerException.class)
                .hasMessageContaining("properties");
    }

    // UT-004: quote 傳入 null customerId 應拋出 IllegalArgumentException
    @Test
    @DisplayName("quote with null customerId throws IllegalArgumentException")
    void quote_withNullCustomerId_throwsIllegalArgumentException() {
        assertThatThrownBy(() -> orderQuoteService.quote(null, new BigDecimal("100.00"), 1))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("customerId must not be blank");
    }

    // UT-005: quote 傳入 blank customerId 應拋出 IllegalArgumentException
    @Test
    @DisplayName("quote with blank customerId throws IllegalArgumentException")
    void quote_withBlankCustomerId_throwsIllegalArgumentException() {
        assertThatThrownBy(() -> orderQuoteService.quote("   ", new BigDecimal("100.00"), 1))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("customerId must not be blank");
    }

    // UT-006: quote 傳入 null unitPrice 應拋出 IllegalArgumentException
    @Test
    @DisplayName("quote with null unitPrice throws IllegalArgumentException")
    void quote_withNullUnitPrice_throwsIllegalArgumentException() {
        assertThatThrownBy(() -> orderQuoteService.quote("CUST001", null, 1))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("unitPrice must not be null or negative");
    }

    // UT-007: quote 傳入 negative unitPrice 應拋出 IllegalArgumentException
    @Test
    @DisplayName("quote with negative unitPrice throws IllegalArgumentException")
    void quote_withNegativeUnitPrice_throwsIllegalArgumentException() {
        assertThatThrownBy(() -> orderQuoteService.quote("CUST001", new BigDecimal("-10.00"), 1))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("unitPrice must not be null or negative");
    }

    // UT-008: quote 傳入 zero quantity 應拋出 IllegalArgumentException
    @Test
    @DisplayName("quote with zero quantity throws IllegalArgumentException")
    void quote_withZeroQuantity_throwsIllegalArgumentException() {
        assertThatThrownBy(() -> orderQuoteService.quote("CUST001", new BigDecimal("100.00"), 0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("quantity must be positive");
    }

    // UT-009: quote 傳入 negative quantity 應拋出 IllegalArgumentException
    @Test
    @DisplayName("quote with negative quantity throws IllegalArgumentException")
    void quote_withNegativeQuantity_throwsIllegalArgumentException() {
        assertThatThrownBy(() -> orderQuoteService.quote("CUST001", new BigDecimal("100.00"), -1))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("quantity must be positive");
    }

    // UT-010: 正常報價計算流程
    @Test
    @DisplayName("quote calculates order correctly with all components")
    void quote_calculatesOrderCorrectly() {
        // Given
        String customerId = "CUST001";
        BigDecimal unitPrice = new BigDecimal("100.00");
        int quantity = 2;
        BigDecimal expectedSubtotal = new BigDecimal("200.00");
        BigDecimal discount = new BigDecimal("20.00");
        BigDecimal discountedSubtotal = new BigDecimal("180.00");
        BigDecimal expectedTax = new BigDecimal("9.00"); // 180 * 0.05 = 9.00
        BigDecimal shippingFee = new BigDecimal("10.00");
        BigDecimal expectedTotal = new BigDecimal("199.00"); // 180 + 9 + 10 = 199

        when(discountCalculator.calculateDiscount(customerId, expectedSubtotal)).thenReturn(discount);
        when(shippingFeeCalculator.calculateShippingFee(discountedSubtotal)).thenReturn(shippingFee);

        // When
        OrderQuote result = orderQuoteService.quote(customerId, unitPrice, quantity);

        // Then
        assertThat(result.subtotal()).isEqualByComparingTo(expectedSubtotal);
        assertThat(result.discount()).isEqualByComparingTo(discount);
        assertThat(result.discountedSubtotal()).isEqualByComparingTo(discountedSubtotal);
        assertThat(result.tax()).isEqualByComparingTo(expectedTax);
        assertThat(result.shippingFee()).isEqualByComparingTo(shippingFee);
        assertThat(result.total()).isEqualByComparingTo(expectedTotal);
    }

    // UT-011: Tax 計算使用 HALF_UP 進位
    @Test
    @DisplayName("quote rounds tax using HALF_UP to two decimal places")
    void quote_roundsTaxUsingHalfUp() {
        // Given: 設定會產生需要進位的稅額
        String customerId = "CUST001";
        BigDecimal unitPrice = new BigDecimal("33.33");
        int quantity = 3;
        BigDecimal expectedSubtotal = new BigDecimal("99.99");
        BigDecimal discount = BigDecimal.ZERO;
        BigDecimal discountedSubtotal = new BigDecimal("99.99");
        // 99.99 * 0.05 = 4.9995，HALF_UP 進位後應為 5.00
        BigDecimal expectedTax = new BigDecimal("5.00");
        BigDecimal shippingFee = BigDecimal.ZERO;
        BigDecimal expectedTotal = new BigDecimal("104.99");

        when(discountCalculator.calculateDiscount(customerId, expectedSubtotal)).thenReturn(discount);
        when(shippingFeeCalculator.calculateShippingFee(discountedSubtotal)).thenReturn(shippingFee);

        // When
        OrderQuote result = orderQuoteService.quote(customerId, unitPrice, quantity);

        // Then
        assertThat(result.tax()).isEqualByComparingTo(expectedTax);
    }

    // UT-012: Total 計算使用 HALF_UP 進位
    @Test
    @DisplayName("quote rounds total using HALF_UP to two decimal places")
    void quote_roundsTotalUsingHalfUp() {
        // Given: 設定會產生需要進位的總額
        String customerId = "CUST001";
        BigDecimal unitPrice = new BigDecimal("33.33");
        int quantity = 3;
        BigDecimal expectedSubtotal = new BigDecimal("99.99");
        BigDecimal discount = BigDecimal.ZERO;
        BigDecimal discountedSubtotal = new BigDecimal("99.99");
        BigDecimal tax = new BigDecimal("5.00");
        BigDecimal shippingFee = new BigDecimal("0.005"); // 會造成 total 需要進位
        // 99.99 + 5.00 + 0.005 = 104.995，HALF_UP 進位後應為 105.00
        BigDecimal expectedTotal = new BigDecimal("105.00");

        when(discountCalculator.calculateDiscount(customerId, expectedSubtotal)).thenReturn(discount);
        when(shippingFeeCalculator.calculateShippingFee(discountedSubtotal)).thenReturn(shippingFee);

        // When
        OrderQuote result = orderQuoteService.quote(customerId, unitPrice, quantity);

        // Then
        assertThat(result.total()).isEqualByComparingTo(expectedTotal);
    }
}
