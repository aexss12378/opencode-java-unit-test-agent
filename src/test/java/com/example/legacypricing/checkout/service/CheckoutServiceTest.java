package com.example.legacypricing.checkout.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.example.legacypricing.checkout.model.CheckoutResult;
import com.example.legacypricing.checkout.model.CheckoutStatus;
import com.example.legacypricing.checkout.port.InventoryGateway;
import com.example.legacypricing.pricing.calculator.PaymentDeadlineCalculator;
import java.time.Instant;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;

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

    // UT-001: 庫存預留成功時應返回 PAYMENT_PENDING 狀態與付款期限
    @Test
    void shouldReturnPaymentPendingWhenReservationSucceeds() {
        // given
        String orderId = "order-001";
        String sku = "SKU-123";
        int quantity = 5;
        Instant expectedDeadline = Instant.parse("2025-01-15T10:00:00Z");

        when(inventoryGateway.reserve(sku, quantity)).thenReturn(true);
        when(paymentDeadlineCalculator.createDeadline()).thenReturn(expectedDeadline);

        // when
        CheckoutResult result = checkoutService.checkout(orderId, sku, quantity);

        // then
        assertEquals(CheckoutStatus.PAYMENT_PENDING, result.status());
        assertEquals(expectedDeadline, result.paymentDeadline());
        verify(inventoryGateway).reserve(sku, quantity);
        verify(paymentDeadlineCalculator).createDeadline();
    }

    // UT-002: 庫存預留失敗時應返回 OUT_OF_STOCK 狀態且無付款期限
    @Test
    void shouldReturnOutOfStockWhenReservationFails() {
        // given
        String orderId = "order-002";
        String sku = "SKU-456";
        int quantity = 3;

        when(inventoryGateway.reserve(sku, quantity)).thenReturn(false);

        // when
        CheckoutResult result = checkoutService.checkout(orderId, sku, quantity);

        // then
        assertEquals(CheckoutStatus.OUT_OF_STOCK, result.status());
        assertNull(result.paymentDeadline());
        verify(inventoryGateway).reserve(sku, quantity);
        Mockito.verifyNoInteractions(paymentDeadlineCalculator);
    }

    // UT-003: orderId 為 null 時應拋出 IllegalArgumentException
    @Test
    void shouldThrowExceptionWhenOrderIdIsNull() {
        // given
        String sku = "SKU-123";
        int quantity = 1;

        // when & then
        IllegalArgumentException exception = assertThrows(
            IllegalArgumentException.class,
            () -> checkoutService.checkout(null, sku, quantity)
        );
        assertEquals("orderId must not be blank", exception.getMessage());
        Mockito.verifyNoInteractions(inventoryGateway);
        Mockito.verifyNoInteractions(paymentDeadlineCalculator);
    }

    // UT-004: orderId 為空白字串時應拋出 IllegalArgumentException
    @Test
    void shouldThrowExceptionWhenOrderIdIsBlank() {
        // given
        String sku = "SKU-123";
        int quantity = 1;

        // when & then
        IllegalArgumentException exception = assertThrows(
            IllegalArgumentException.class,
            () -> checkoutService.checkout("   ", sku, quantity)
        );
        assertEquals("orderId must not be blank", exception.getMessage());
        Mockito.verifyNoInteractions(inventoryGateway);
        Mockito.verifyNoInteractions(paymentDeadlineCalculator);
    }

    // UT-005: orderId 為空字串時應拋出 IllegalArgumentException
    @Test
    void shouldThrowExceptionWhenOrderIdIsEmpty() {
        // given
        String sku = "SKU-123";
        int quantity = 1;

        // when & then
        IllegalArgumentException exception = assertThrows(
            IllegalArgumentException.class,
            () -> checkoutService.checkout("", sku, quantity)
        );
        assertEquals("orderId must not be blank", exception.getMessage());
        Mockito.verifyNoInteractions(inventoryGateway);
        Mockito.verifyNoInteractions(paymentDeadlineCalculator);
    }

    // UT-006: sku 為 null 時應拋出 IllegalArgumentException
    @Test
    void shouldThrowExceptionWhenSkuIsNull() {
        // given
        String orderId = "order-001";
        int quantity = 1;

        // when & then
        IllegalArgumentException exception = assertThrows(
            IllegalArgumentException.class,
            () -> checkoutService.checkout(orderId, null, quantity)
        );
        assertEquals("sku must not be blank", exception.getMessage());
        Mockito.verifyNoInteractions(inventoryGateway);
        Mockito.verifyNoInteractions(paymentDeadlineCalculator);
    }

    // UT-007: sku 為空白字串時應拋出 IllegalArgumentException
    @Test
    void shouldThrowExceptionWhenSkuIsBlank() {
        // given
        String orderId = "order-001";
        int quantity = 1;

        // when & then
        IllegalArgumentException exception = assertThrows(
            IllegalArgumentException.class,
            () -> checkoutService.checkout(orderId, "   ", quantity)
        );
        assertEquals("sku must not be blank", exception.getMessage());
        Mockito.verifyNoInteractions(inventoryGateway);
        Mockito.verifyNoInteractions(paymentDeadlineCalculator);
    }

    // UT-008: sku 為空字串時應拋出 IllegalArgumentException
    @Test
    void shouldThrowExceptionWhenSkuIsEmpty() {
        // given
        String orderId = "order-001";
        int quantity = 1;

        // when & then
        IllegalArgumentException exception = assertThrows(
            IllegalArgumentException.class,
            () -> checkoutService.checkout(orderId, "", quantity)
        );
        assertEquals("sku must not be blank", exception.getMessage());
        Mockito.verifyNoInteractions(inventoryGateway);
        Mockito.verifyNoInteractions(paymentDeadlineCalculator);
    }

    // UT-009: quantity 為 0 時應拋出 IllegalArgumentException
    @Test
    void shouldThrowExceptionWhenQuantityIsZero() {
        // given
        String orderId = "order-001";
        String sku = "SKU-123";

        // when & then
        IllegalArgumentException exception = assertThrows(
            IllegalArgumentException.class,
            () -> checkoutService.checkout(orderId, sku, 0)
        );
        assertEquals("quantity must be positive", exception.getMessage());
        Mockito.verifyNoInteractions(inventoryGateway);
        Mockito.verifyNoInteractions(paymentDeadlineCalculator);
    }

    // UT-010: quantity 為負數時應拋出 IllegalArgumentException
    @Test
    void shouldThrowExceptionWhenQuantityIsNegative() {
        // given
        String orderId = "order-001";
        String sku = "SKU-123";

        // when & then
        IllegalArgumentException exception = assertThrows(
            IllegalArgumentException.class,
            () -> checkoutService.checkout(orderId, sku, -5)
        );
        assertEquals("quantity must be positive", exception.getMessage());
        Mockito.verifyNoInteractions(inventoryGateway);
        Mockito.verifyNoInteractions(paymentDeadlineCalculator);
    }

    // UT-011: 建構子於 inventoryGateway 為 null 時應拋出 NullPointerException
    @Test
    void shouldThrowExceptionWhenInventoryGatewayIsNull() {
        // when & then
        NullPointerException exception = assertThrows(
            NullPointerException.class,
            () -> new CheckoutService(null, paymentDeadlineCalculator)
        );
        assertEquals("inventoryGateway", exception.getMessage());
    }

    // UT-012: 建構子於 paymentDeadlineCalculator 為 null 時應拋出 NullPointerException
    @Test
    void shouldThrowExceptionWhenPaymentDeadlineCalculatorIsNull() {
        // when & then
        NullPointerException exception = assertThrows(
            NullPointerException.class,
            () -> new CheckoutService(inventoryGateway, null)
        );
        assertEquals("paymentDeadlineCalculator", exception.getMessage());
    }
}
