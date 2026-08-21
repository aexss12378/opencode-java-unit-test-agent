package com.example.legacypricing.checkout.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.example.legacypricing.checkout.model.CheckoutResult;
import com.example.legacypricing.checkout.model.CheckoutStatus;
import com.example.legacypricing.checkout.port.InventoryGateway;
import com.example.legacypricing.pricing.calculator.PaymentDeadlineCalculator;
import java.time.Instant;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

class CheckoutServiceTest {

    private InventoryGateway inventoryGateway;
    private PaymentDeadlineCalculator paymentDeadlineCalculator;
    private CheckoutService checkoutService;

    @BeforeEach
    void setUp() {
        inventoryGateway = mock(InventoryGateway.class);
        paymentDeadlineCalculator = mock(PaymentDeadlineCalculator.class);
        checkoutService = new CheckoutService(inventoryGateway, paymentDeadlineCalculator);
    }

    // UT-001: 庫存保留成功時應返回 PAYMENT_PENDING 狀態和付款期限
    // Evidence: CheckoutService Javadoc L28-29: "When reservation succeeds, returns
    // PAYMENT_PENDING with the deadline supplied by the deadline policy."
    @Test
    @DisplayName("當庫存保留成功時，應返回 PAYMENT_PENDING 狀態和付款期限")
    void checkout_WhenReservationSucceeds_ShouldReturnPaymentPendingWithDeadline() {
        // Given
        String orderId = "ORDER-001";
        String sku = "SKU-001";
        int quantity = 5;
        Instant expectedDeadline = Instant.parse("2026-08-11T10:00:00Z");

        when(inventoryGateway.reserve(sku, quantity)).thenReturn(true);
        when(paymentDeadlineCalculator.createDeadline()).thenReturn(expectedDeadline);

        // When
        CheckoutResult result = checkoutService.checkout(orderId, sku, quantity);

        // Then
        assertThat(result.status()).isEqualTo(CheckoutStatus.PAYMENT_PENDING);
        assertThat(result.paymentDeadline()).isEqualTo(expectedDeadline);
        verify(inventoryGateway).reserve(sku, quantity);
        verify(paymentDeadlineCalculator).createDeadline();
    }

    // UT-002: 庫存保留失敗時應返回 OUT_OF_STOCK 狀態且不建立付款期限
    // Evidence: CheckoutService Javadoc L26-27: "Reserves inventory and returns
    // OUT_OF_STOCK without creating a payment deadline when reservation fails."
    @Test
    @DisplayName("當庫存保留失敗時，應返回 OUT_OF_STOCK 狀態且不建立付款期限")
    void checkout_WhenReservationFails_ShouldReturnOutOfStockWithoutDeadline() {
        // Given
        String orderId = "ORDER-002";
        String sku = "SKU-002";
        int quantity = 3;

        when(inventoryGateway.reserve(sku, quantity)).thenReturn(false);

        // When
        CheckoutResult result = checkoutService.checkout(orderId, sku, quantity);

        // Then
        assertThat(result.status()).isEqualTo(CheckoutStatus.OUT_OF_STOCK);
        assertThat(result.paymentDeadline()).isNull();
        verify(inventoryGateway).reserve(sku, quantity);
        verify(paymentDeadlineCalculator, never()).createDeadline();
    }

    // UT-003: orderId 為 null 時應拋出 IllegalArgumentException
    // Evidence: CheckoutService Javadoc L31-33: "@throws IllegalArgumentException when
    // order ID or SKU is null or blank, or quantity is not positive"
    @Test
    @DisplayName("當 orderId 為 null 時，應拋出 IllegalArgumentException")
    void checkout_WhenOrderIdIsNull_ShouldThrowIllegalArgumentException() {
        // Given
        String sku = "SKU-003";
        int quantity = 1;

        // When & Then
        assertThatThrownBy(() -> checkoutService.checkout(null, sku, quantity))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("orderId must not be blank");
    }

    // UT-004: orderId 為空白字串時應拋出 IllegalArgumentException
    // Evidence: CheckoutService Javadoc L31-33: "@throws IllegalArgumentException when
    // order ID or SKU is null or blank, or quantity is not positive"
    @Test
    @DisplayName("當 orderId 為空白字串時，應拋出 IllegalArgumentException")
    void checkout_WhenOrderIdIsBlank_ShouldThrowIllegalArgumentException() {
        // Given
        String orderId = "   ";
        String sku = "SKU-004";
        int quantity = 1;

        // When & Then
        assertThatThrownBy(() -> checkoutService.checkout(orderId, sku, quantity))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("orderId must not be blank");
    }

    // UT-005: sku 為 null 時應拋出 IllegalArgumentException
    // Evidence: CheckoutService Javadoc L31-33: "@throws IllegalArgumentException when
    // order ID or SKU is null or blank, or quantity is not positive"
    @Test
    @DisplayName("當 sku 為 null 時，應拋出 IllegalArgumentException")
    void checkout_WhenSkuIsNull_ShouldThrowIllegalArgumentException() {
        // Given
        String orderId = "ORDER-005";
        int quantity = 1;

        // When & Then
        assertThatThrownBy(() -> checkoutService.checkout(orderId, null, quantity))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("sku must not be blank");
    }

    // UT-006: sku 為空白字串時應拋出 IllegalArgumentException
    // Evidence: CheckoutService Javadoc L31-33: "@throws IllegalArgumentException when
    // order ID or SKU is null or blank, or quantity is not positive"
    @Test
    @DisplayName("當 sku 為空白字串時，應拋出 IllegalArgumentException")
    void checkout_WhenSkuIsBlank_ShouldThrowIllegalArgumentException() {
        // Given
        String orderId = "ORDER-006";
        String sku = "   ";
        int quantity = 1;

        // When & Then
        assertThatThrownBy(() -> checkoutService.checkout(orderId, sku, quantity))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("sku must not be blank");
    }

    // UT-007: quantity 為 0 時應拋出 IllegalArgumentException
    // Evidence: CheckoutService Javadoc L31-33: "@throws IllegalArgumentException when
    // order ID or SKU is null or blank, or quantity is not positive"
    @Test
    @DisplayName("當 quantity 為 0 時，應拋出 IllegalArgumentException")
    void checkout_WhenQuantityIsZero_ShouldThrowIllegalArgumentException() {
        // Given
        String orderId = "ORDER-007";
        String sku = "SKU-007";

        // When & Then
        assertThatThrownBy(() -> checkoutService.checkout(orderId, sku, 0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("quantity must be positive");
    }

    // UT-008: quantity 為負數時應拋出 IllegalArgumentException
    // Evidence: CheckoutService Javadoc L31-33: "@throws IllegalArgumentException when
    // order ID or SKU is null or blank, or quantity is not positive"
    @Test
    @DisplayName("當 quantity 為負數時，應拋出 IllegalArgumentException")
    void checkout_WhenQuantityIsNegative_ShouldThrowIllegalArgumentException() {
        // Given
        String orderId = "ORDER-008";
        String sku = "SKU-008";

        // When & Then
        assertThatThrownBy(() -> checkoutService.checkout(orderId, sku, -5))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("quantity must be positive");
    }
}
