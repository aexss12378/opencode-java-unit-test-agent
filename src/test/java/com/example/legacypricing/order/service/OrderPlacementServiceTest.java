package com.example.legacypricing.order.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.when;

import com.example.legacypricing.order.OrderPlacementCommand;
import com.example.legacypricing.order.OrderPlacementResult;
import com.example.legacypricing.order.OrderPlacementStatus;
import com.example.legacypricing.order.PaymentAuthorization;
import com.example.legacypricing.order.StockReservation;
import com.example.legacypricing.order.config.OrderWorkflowProperties;
import com.example.legacypricing.order.dao.OrderPlacementDao;
import com.example.legacypricing.order.entity.OrderPlacementEntity;
import com.example.legacypricing.order.exception.IdempotencyConflictException;
import com.example.legacypricing.order.mapper.OrderPlacementPersistenceMapper;
import com.example.legacypricing.order.port.PaymentGateway;
import com.example.legacypricing.order.port.RiskAssessmentGateway;
import com.example.legacypricing.order.port.StockReservationGateway;
import com.example.legacypricing.order.vo.IdempotencyKey;
import com.example.legacypricing.order.vo.Money;
import com.example.legacypricing.order.vo.OrderId;
import java.math.BigDecimal;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

@ExtendWith(MockitoExtension.class)
class OrderPlacementServiceTest {

    @Mock
    private RiskAssessmentGateway riskAssessmentGateway;

    @Mock
    private StockReservationGateway stockReservationGateway;

    @Mock
    private PaymentGateway paymentGateway;

    @Mock
    private OrderPlacementDao orderPlacementDao;

    @Mock
    private OrderPlacementPersistenceMapper persistenceMapper;

    @Mock
    private OrderWorkflowProperties properties;

    @Mock
    private Clock clock;

    @InjectMocks
    private OrderPlacementService orderPlacementService;

    private static final String ORDER_ID = "ORD-20260804-001";
    private static final String IDEMPOTENCY_KEY = "req-001";
    private static final String CUSTOMER_ID = "CUST-001";
    private static final String SKU = "SKU-BOOK";
    private static final int QUANTITY = 2;
    private static final BigDecimal AMOUNT = new BigDecimal("1200.00");
    private static final String CURRENCY = "TWD";
    private static final String PAYMENT_TOKEN = "TOKEN-OK-001";
    private static final int MANUAL_REVIEW_SCORE = 50;
    private static final int REJECTION_SCORE = 80;
    private static final BigDecimal MAXIMUM_ORDER_TOTAL = new BigDecimal("100000.00");
    private static final Duration PAYMENT_WINDOW = Duration.ofHours(24);
    private static final Instant NOW = Instant.parse("2026-08-04T10:00:00Z");

    @BeforeEach
    void setUp() {
        lenient().when(properties.manualReviewScore()).thenReturn(MANUAL_REVIEW_SCORE);
        lenient().when(properties.rejectionScore()).thenReturn(REJECTION_SCORE);
        lenient().when(properties.maximumOrderTotal()).thenReturn(MAXIMUM_ORDER_TOTAL);
        lenient().when(properties.supportedCurrency()).thenReturn(CURRENCY);
        lenient().when(properties.paymentAuthorizationWindow()).thenReturn(PAYMENT_WINDOW);
        lenient().when(clock.instant()).thenReturn(NOW);
    }

    private OrderPlacementCommand createValidCommand() {
        return new OrderPlacementCommand(
                new OrderId(ORDER_ID),
                new IdempotencyKey(IDEMPOTENCY_KEY),
                CUSTOMER_ID,
                SKU,
                QUANTITY,
                new Money(AMOUNT, CURRENCY),
                PAYMENT_TOKEN
        );
    }

    // UT-001: command 為 null 時拋出 IllegalArgumentException
    @Test
    void place_nullCommand_shouldThrowIllegalArgumentException() {
        assertThatThrownBy(() -> orderPlacementService.place(null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("command");

        verify(orderPlacementDao, never()).findByIdempotencyKey(anyString());
        verify(riskAssessmentGateway, never()).assess(anyString(), any());
    }

    // UT-002: orderId 為 null 時拋出 IllegalArgumentException
    @Test
    void place_nullOrderId_shouldThrowIllegalArgumentException() {
        OrderPlacementCommand command = new OrderPlacementCommand(
                null,
                new IdempotencyKey(IDEMPOTENCY_KEY),
                CUSTOMER_ID,
                SKU,
                QUANTITY,
                new Money(AMOUNT, CURRENCY),
                PAYMENT_TOKEN
        );

        assertThatThrownBy(() -> orderPlacementService.place(command))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("orderId");

        verify(orderPlacementDao, never()).findByIdempotencyKey(anyString());
        verify(riskAssessmentGateway, never()).assess(anyString(), any());
    }

    // UT-003: idempotencyKey 為 null 時拋出 IllegalArgumentException
    @Test
    void place_nullIdempotencyKey_shouldThrowIllegalArgumentException() {
        OrderPlacementCommand command = new OrderPlacementCommand(
                new OrderId(ORDER_ID),
                null,
                CUSTOMER_ID,
                SKU,
                QUANTITY,
                new Money(AMOUNT, CURRENCY),
                PAYMENT_TOKEN
        );

        assertThatThrownBy(() -> orderPlacementService.place(command))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("idempotencyKey");

        verify(orderPlacementDao, never()).findByIdempotencyKey(anyString());
        verify(riskAssessmentGateway, never()).assess(anyString(), any());
    }

    // UT-004: customerId 為 null 時拋出 IllegalArgumentException
    @Test
    void place_nullCustomerId_shouldThrowIllegalArgumentException() {
        OrderPlacementCommand command = new OrderPlacementCommand(
                new OrderId(ORDER_ID),
                new IdempotencyKey(IDEMPOTENCY_KEY),
                null,
                SKU,
                QUANTITY,
                new Money(AMOUNT, CURRENCY),
                PAYMENT_TOKEN
        );

        assertThatThrownBy(() -> orderPlacementService.place(command))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("customerId");

        verify(orderPlacementDao, never()).findByIdempotencyKey(anyString());
        verify(riskAssessmentGateway, never()).assess(anyString(), any());
    }

    // UT-005: customerId 為空白時拋出 IllegalArgumentException
    @Test
    void place_blankCustomerId_shouldThrowIllegalArgumentException() {
        OrderPlacementCommand command = new OrderPlacementCommand(
                new OrderId(ORDER_ID),
                new IdempotencyKey(IDEMPOTENCY_KEY),
                "   ",
                SKU,
                QUANTITY,
                new Money(AMOUNT, CURRENCY),
                PAYMENT_TOKEN
        );

        assertThatThrownBy(() -> orderPlacementService.place(command))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("customerId");

        verify(orderPlacementDao, never()).findByIdempotencyKey(anyString());
        verify(riskAssessmentGateway, never()).assess(anyString(), any());
    }

    // UT-006: sku 為 null 時拋出 IllegalArgumentException
    @Test
    void place_nullSku_shouldThrowIllegalArgumentException() {
        OrderPlacementCommand command = new OrderPlacementCommand(
                new OrderId(ORDER_ID),
                new IdempotencyKey(IDEMPOTENCY_KEY),
                CUSTOMER_ID,
                null,
                QUANTITY,
                new Money(AMOUNT, CURRENCY),
                PAYMENT_TOKEN
        );

        assertThatThrownBy(() -> orderPlacementService.place(command))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("sku");

        verify(orderPlacementDao, never()).findByIdempotencyKey(anyString());
        verify(riskAssessmentGateway, never()).assess(anyString(), any());
    }

    // UT-007: sku 為空白時拋出 IllegalArgumentException
    @Test
    void place_blankSku_shouldThrowIllegalArgumentException() {
        OrderPlacementCommand command = new OrderPlacementCommand(
                new OrderId(ORDER_ID),
                new IdempotencyKey(IDEMPOTENCY_KEY),
                CUSTOMER_ID,
                "",
                QUANTITY,
                new Money(AMOUNT, CURRENCY),
                PAYMENT_TOKEN
        );

        assertThatThrownBy(() -> orderPlacementService.place(command))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("sku");

        verify(orderPlacementDao, never()).findByIdempotencyKey(anyString());
        verify(riskAssessmentGateway, never()).assess(anyString(), any());
    }

    // UT-008: paymentToken 為 null 時拋出 IllegalArgumentException
    @Test
    void place_nullPaymentToken_shouldThrowIllegalArgumentException() {
        OrderPlacementCommand command = new OrderPlacementCommand(
                new OrderId(ORDER_ID),
                new IdempotencyKey(IDEMPOTENCY_KEY),
                CUSTOMER_ID,
                SKU,
                QUANTITY,
                new Money(AMOUNT, CURRENCY),
                null
        );

        assertThatThrownBy(() -> orderPlacementService.place(command))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("paymentToken");

        verify(orderPlacementDao, never()).findByIdempotencyKey(anyString());
        verify(riskAssessmentGateway, never()).assess(anyString(), any());
    }

    // UT-009: paymentToken 為空白時拋出 IllegalArgumentException
    @Test
    void place_blankPaymentToken_shouldThrowIllegalArgumentException() {
        OrderPlacementCommand command = new OrderPlacementCommand(
                new OrderId(ORDER_ID),
                new IdempotencyKey(IDEMPOTENCY_KEY),
                CUSTOMER_ID,
                SKU,
                QUANTITY,
                new Money(AMOUNT, CURRENCY),
                "\t"
        );

        assertThatThrownBy(() -> orderPlacementService.place(command))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("paymentToken");

        verify(orderPlacementDao, never()).findByIdempotencyKey(anyString());
        verify(riskAssessmentGateway, never()).assess(anyString(), any());
    }

    // UT-010: quantity <= 0 時拋出 IllegalArgumentException
    @Test
    void place_zeroQuantity_shouldThrowIllegalArgumentException() {
        OrderPlacementCommand command = new OrderPlacementCommand(
                new OrderId(ORDER_ID),
                new IdempotencyKey(IDEMPOTENCY_KEY),
                CUSTOMER_ID,
                SKU,
                0,
                new Money(AMOUNT, CURRENCY),
                PAYMENT_TOKEN
        );

        assertThatThrownBy(() -> orderPlacementService.place(command))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("quantity");

        verify(orderPlacementDao, never()).findByIdempotencyKey(anyString());
        verify(riskAssessmentGateway, never()).assess(anyString(), any());
    }

    // UT-011: total 為 null 時拋出 IllegalArgumentException
    @Test
    void place_nullTotal_shouldThrowIllegalArgumentException() {
        OrderPlacementCommand command = new OrderPlacementCommand(
                new OrderId(ORDER_ID),
                new IdempotencyKey(IDEMPOTENCY_KEY),
                CUSTOMER_ID,
                SKU,
                QUANTITY,
                null,
                PAYMENT_TOKEN
        );

        assertThatThrownBy(() -> orderPlacementService.place(command))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("total");

        verify(orderPlacementDao, never()).findByIdempotencyKey(anyString());
        verify(riskAssessmentGateway, never()).assess(anyString(), any());
    }

    // UT-012: total.amount <= 0 時拋出 IllegalArgumentException
    @Test
    void place_zeroAmount_shouldThrowIllegalArgumentException() {
        OrderPlacementCommand command = new OrderPlacementCommand(
                new OrderId(ORDER_ID),
                new IdempotencyKey(IDEMPOTENCY_KEY),
                CUSTOMER_ID,
                SKU,
                QUANTITY,
                new Money(BigDecimal.ZERO, CURRENCY),
                PAYMENT_TOKEN
        );

        assertThatThrownBy(() -> orderPlacementService.place(command))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("total");

        verify(orderPlacementDao, never()).findByIdempotencyKey(anyString());
        verify(riskAssessmentGateway, never()).assess(anyString(), any());
    }

    // UT-013: total.amount > maximumOrderTotal 時拋出 IllegalArgumentException
    @Test
    void place_amountExceedsMaximum_shouldThrowIllegalArgumentException() {
        OrderPlacementCommand command = new OrderPlacementCommand(
                new OrderId(ORDER_ID),
                new IdempotencyKey(IDEMPOTENCY_KEY),
                CUSTOMER_ID,
                SKU,
                QUANTITY,
                new Money(MAXIMUM_ORDER_TOTAL.add(BigDecimal.ONE), CURRENCY),
                PAYMENT_TOKEN
        );

        assertThatThrownBy(() -> orderPlacementService.place(command))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("maximum");

        verify(orderPlacementDao, never()).findByIdempotencyKey(anyString());
        verify(riskAssessmentGateway, never()).assess(anyString(), any());
    }

    // UT-014: total.currency 與 supportedCurrency 不符時拋出 IllegalArgumentException
    @Test
    void place_unsupportedCurrency_shouldThrowIllegalArgumentException() {
        OrderPlacementCommand command = new OrderPlacementCommand(
                new OrderId(ORDER_ID),
                new IdempotencyKey(IDEMPOTENCY_KEY),
                CUSTOMER_ID,
                SKU,
                QUANTITY,
                new Money(AMOUNT, "USD"),
                PAYMENT_TOKEN
        );

        assertThatThrownBy(() -> orderPlacementService.place(command))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("currency");

        verify(orderPlacementDao, never()).findByIdempotencyKey(anyString());
        verify(riskAssessmentGateway, never()).assess(anyString(), any());
    }

    // UT-015: 相同冪等鍵和相同命令時，直接回傳先前結果，不呼叫風險/庫存/付款
    @Test
    void place_sameIdempotencyKeyAndSameCommand_shouldReturnPreviousResult() {
        OrderPlacementCommand command = createValidCommand();
        OrderPlacementEntity previousEntity = new OrderPlacementEntity(
                IDEMPOTENCY_KEY,
                "739474f440337ffafa048fc9b0ef9f43c8826b58f89be8e6c14559db0a3d89b4",
                OrderPlacementStatus.ACCEPTED,
                ORDER_ID,
                "RES-001",
                "AUTH-001",
                NOW.plus(PAYMENT_WINDOW)
        );
        OrderPlacementResult expectedResult = OrderPlacementResult.accepted(
                ORDER_ID, "RES-001", "AUTH-001", NOW.plus(PAYMENT_WINDOW)
        );

        when(orderPlacementDao.findByIdempotencyKey(IDEMPOTENCY_KEY))
                .thenReturn(Optional.of(previousEntity));
        when(persistenceMapper.toResult(previousEntity)).thenReturn(expectedResult);

        OrderPlacementResult result = orderPlacementService.place(command);

        assertThat(result).isEqualTo(expectedResult);
        verify(riskAssessmentGateway, never()).assess(anyString(), any());
        verify(stockReservationGateway, never()).reserve(anyString(), anyInt());
        verify(paymentGateway, never()).authorize(anyString(), any(), anyString(), anyString());
    }

    // UT-016: 相同冪等鍵但不同命令時拋出 IdempotencyConflictException
    @Test
    void place_sameIdempotencyKeyButDifferentCommand_shouldThrowIdempotencyConflictException() {
        OrderPlacementCommand command = createValidCommand();
        OrderPlacementEntity previousEntity = new OrderPlacementEntity(
                IDEMPOTENCY_KEY,
                "different-fingerprint",
                OrderPlacementStatus.ACCEPTED,
                ORDER_ID,
                "RES-001",
                "AUTH-001",
                NOW.plus(PAYMENT_WINDOW)
        );

        when(orderPlacementDao.findByIdempotencyKey(IDEMPOTENCY_KEY))
                .thenReturn(Optional.of(previousEntity));

        assertThatThrownBy(() -> orderPlacementService.place(command))
                .isInstanceOf(IdempotencyConflictException.class)
                .hasMessageContaining("idempotency key was already used");

        verify(riskAssessmentGateway, never()).assess(anyString(), any());
        verify(stockReservationGateway, never()).reserve(anyString(), anyInt());
        verify(paymentGateway, never()).authorize(anyString(), any(), anyString(), anyString());
    }

    // UT-017: 風險分數 >= rejectionScore 時回傳 RISK_REJECTED，不呼叫庫存/付款
    @Test
    void place_riskScoreAboveRejectionThreshold_shouldReturnRiskRejected() {
        OrderPlacementCommand command = createValidCommand();
        OrderPlacementResult expectedResult = OrderPlacementResult.withoutReservation(
                OrderPlacementStatus.RISK_REJECTED, ORDER_ID
        );
        OrderPlacementEntity entity = new OrderPlacementEntity(
                IDEMPOTENCY_KEY,
                "any-fingerprint",
                OrderPlacementStatus.RISK_REJECTED,
                ORDER_ID,
                null,
                null,
                null
        );

        when(orderPlacementDao.findByIdempotencyKey(IDEMPOTENCY_KEY))
                .thenReturn(Optional.empty());
        when(riskAssessmentGateway.assess(CUSTOMER_ID, AMOUNT))
                .thenReturn(REJECTION_SCORE);
        when(persistenceMapper.toEntity(anyString(), anyString(), any()))
                .thenReturn(entity);
        when(orderPlacementDao.save(any())).thenReturn(entity);

        OrderPlacementResult result = orderPlacementService.place(command);

        assertThat(result.status()).isEqualTo(OrderPlacementStatus.RISK_REJECTED);
        assertThat(result.orderId()).isEqualTo(ORDER_ID);
        assertThat(result.reservationId()).isNull();
        assertThat(result.authorizationId()).isNull();
        verify(stockReservationGateway, never()).reserve(anyString(), anyInt());
        verify(paymentGateway, never()).authorize(anyString(), any(), anyString(), anyString());
    }

    // UT-018: 風險分數 >= manualReviewScore 但 < rejectionScore 時回傳 MANUAL_REVIEW，不呼叫庫存/付款
    @Test
    void place_riskScoreAboveManualReviewButBelowRejection_shouldReturnManualReview() {
        OrderPlacementCommand command = createValidCommand();
        OrderPlacementResult expectedResult = OrderPlacementResult.withoutReservation(
                OrderPlacementStatus.MANUAL_REVIEW, ORDER_ID
        );
        OrderPlacementEntity entity = new OrderPlacementEntity(
                IDEMPOTENCY_KEY,
                "any-fingerprint",
                OrderPlacementStatus.MANUAL_REVIEW,
                ORDER_ID,
                null,
                null,
                null
        );

        when(orderPlacementDao.findByIdempotencyKey(IDEMPOTENCY_KEY))
                .thenReturn(Optional.empty());
        when(riskAssessmentGateway.assess(CUSTOMER_ID, AMOUNT))
                .thenReturn(MANUAL_REVIEW_SCORE);
        when(persistenceMapper.toEntity(anyString(), anyString(), any()))
                .thenReturn(entity);
        when(orderPlacementDao.save(any())).thenReturn(entity);

        OrderPlacementResult result = orderPlacementService.place(command);

        assertThat(result.status()).isEqualTo(OrderPlacementStatus.MANUAL_REVIEW);
        assertThat(result.orderId()).isEqualTo(ORDER_ID);
        assertThat(result.reservationId()).isNull();
        assertThat(result.authorizationId()).isNull();
        verify(stockReservationGateway, never()).reserve(anyString(), anyInt());
        verify(paymentGateway, never()).authorize(anyString(), any(), anyString(), anyString());
    }

    // UT-019: 風險分數 < manualReviewScore 時進入庫存保留流程
    @Test
    void place_riskScoreBelowManualReview_shouldProceedToStockReservation() {
        OrderPlacementCommand command = createValidCommand();
        StockReservation reservation = new StockReservation("RES-001", SKU, QUANTITY);
        PaymentAuthorization authorization = PaymentAuthorization.approved("AUTH-001");
        OrderPlacementEntity entity = new OrderPlacementEntity(
                IDEMPOTENCY_KEY,
                "any-fingerprint",
                OrderPlacementStatus.ACCEPTED,
                ORDER_ID,
                "RES-001",
                "AUTH-001",
                NOW.plus(PAYMENT_WINDOW)
        );

        when(orderPlacementDao.findByIdempotencyKey(IDEMPOTENCY_KEY))
                .thenReturn(Optional.empty());
        when(riskAssessmentGateway.assess(CUSTOMER_ID, AMOUNT))
                .thenReturn(MANUAL_REVIEW_SCORE - 1);
        when(stockReservationGateway.reserve(SKU, QUANTITY))
                .thenReturn(Optional.of(reservation));
        when(paymentGateway.authorize(ORDER_ID, AMOUNT, CURRENCY, PAYMENT_TOKEN))
                .thenReturn(authorization);
        when(persistenceMapper.toEntity(anyString(), anyString(), any()))
                .thenReturn(entity);
        when(orderPlacementDao.save(any())).thenReturn(entity);

        OrderPlacementResult result = orderPlacementService.place(command);

        assertThat(result.status()).isEqualTo(OrderPlacementStatus.ACCEPTED);
        verify(stockReservationGateway).reserve(SKU, QUANTITY);
        verify(paymentGateway).authorize(ORDER_ID, AMOUNT, CURRENCY, PAYMENT_TOKEN);
    }

    // UT-020: 庫存保留失敗時回傳 OUT_OF_STOCK，不呼叫付款
    @Test
    void place_stockReservationFailed_shouldReturnOutOfStock() {
        OrderPlacementCommand command = createValidCommand();
        OrderPlacementEntity entity = new OrderPlacementEntity(
                IDEMPOTENCY_KEY,
                "any-fingerprint",
                OrderPlacementStatus.OUT_OF_STOCK,
                ORDER_ID,
                null,
                null,
                null
        );

        when(orderPlacementDao.findByIdempotencyKey(IDEMPOTENCY_KEY))
                .thenReturn(Optional.empty());
        when(riskAssessmentGateway.assess(CUSTOMER_ID, AMOUNT))
                .thenReturn(MANUAL_REVIEW_SCORE - 1);
        when(stockReservationGateway.reserve(SKU, QUANTITY))
                .thenReturn(Optional.empty());
        when(persistenceMapper.toEntity(anyString(), anyString(), any()))
                .thenReturn(entity);
        when(orderPlacementDao.save(any())).thenReturn(entity);

        OrderPlacementResult result = orderPlacementService.place(command);

        assertThat(result.status()).isEqualTo(OrderPlacementStatus.OUT_OF_STOCK);
        assertThat(result.orderId()).isEqualTo(ORDER_ID);
        assertThat(result.reservationId()).isNull();
        assertThat(result.authorizationId()).isNull();
        verify(paymentGateway, never()).authorize(anyString(), any(), anyString(), anyString());
    }

    // UT-021: 庫存保留成功但付款拒絕時，釋放庫存並回傳 PAYMENT_DECLINED
    @Test
    void place_paymentDeclined_shouldReleaseStockAndReturnPaymentDeclined() {
        OrderPlacementCommand command = createValidCommand();
        StockReservation reservation = new StockReservation("RES-001", SKU, QUANTITY);
        PaymentAuthorization authorization = PaymentAuthorization.declined();
        OrderPlacementEntity entity = new OrderPlacementEntity(
                IDEMPOTENCY_KEY,
                "any-fingerprint",
                OrderPlacementStatus.PAYMENT_DECLINED,
                ORDER_ID,
                null,
                null,
                null
        );

        when(orderPlacementDao.findByIdempotencyKey(IDEMPOTENCY_KEY))
                .thenReturn(Optional.empty());
        when(riskAssessmentGateway.assess(CUSTOMER_ID, AMOUNT))
                .thenReturn(MANUAL_REVIEW_SCORE - 1);
        when(stockReservationGateway.reserve(SKU, QUANTITY))
                .thenReturn(Optional.of(reservation));
        when(paymentGateway.authorize(ORDER_ID, AMOUNT, CURRENCY, PAYMENT_TOKEN))
                .thenReturn(authorization);
        when(persistenceMapper.toEntity(anyString(), anyString(), any()))
                .thenReturn(entity);
        when(orderPlacementDao.save(any())).thenReturn(entity);

        OrderPlacementResult result = orderPlacementService.place(command);

        assertThat(result.status()).isEqualTo(OrderPlacementStatus.PAYMENT_DECLINED);
        assertThat(result.orderId()).isEqualTo(ORDER_ID);
        assertThat(result.reservationId()).isNull();
        assertThat(result.authorizationId()).isNull();
        verify(stockReservationGateway).release(reservation);
    }

    // UT-022: 庫存保留成功、付款成功時回傳 ACCEPTED，包含庫存編號/授權編號/付款期限
    @Test
    void place_paymentApproved_shouldReturnAcceptedWithReservationAndAuthIds() {
        OrderPlacementCommand command = createValidCommand();
        StockReservation reservation = new StockReservation("RES-001", SKU, QUANTITY);
        PaymentAuthorization authorization = PaymentAuthorization.approved("AUTH-001");
        OrderPlacementEntity entity = new OrderPlacementEntity(
                IDEMPOTENCY_KEY,
                "any-fingerprint",
                OrderPlacementStatus.ACCEPTED,
                ORDER_ID,
                "RES-001",
                "AUTH-001",
                NOW.plus(PAYMENT_WINDOW)
        );

        when(orderPlacementDao.findByIdempotencyKey(IDEMPOTENCY_KEY))
                .thenReturn(Optional.empty());
        when(riskAssessmentGateway.assess(CUSTOMER_ID, AMOUNT))
                .thenReturn(MANUAL_REVIEW_SCORE - 1);
        when(stockReservationGateway.reserve(SKU, QUANTITY))
                .thenReturn(Optional.of(reservation));
        when(paymentGateway.authorize(ORDER_ID, AMOUNT, CURRENCY, PAYMENT_TOKEN))
                .thenReturn(authorization);
        when(persistenceMapper.toEntity(anyString(), anyString(), any()))
                .thenReturn(entity);
        when(orderPlacementDao.save(any())).thenReturn(entity);

        OrderPlacementResult result = orderPlacementService.place(command);

        assertThat(result.status()).isEqualTo(OrderPlacementStatus.ACCEPTED);
        assertThat(result.orderId()).isEqualTo(ORDER_ID);
        assertThat(result.reservationId()).isEqualTo("RES-001");
        assertThat(result.authorizationId()).isEqualTo("AUTH-001");
        assertThat(result.paymentDeadline()).isEqualTo(NOW.plus(PAYMENT_WINDOW));
    }

    // UT-023: 風險分數 < 0 時拋出 IllegalStateException
    @Test
    void place_riskScoreBelowZero_shouldThrowIllegalStateException() {
        OrderPlacementCommand command = createValidCommand();

        when(orderPlacementDao.findByIdempotencyKey(IDEMPOTENCY_KEY))
                .thenReturn(Optional.empty());
        when(riskAssessmentGateway.assess(CUSTOMER_ID, AMOUNT))
                .thenReturn(-1);

        assertThatThrownBy(() -> orderPlacementService.place(command))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("risk score must be between 0 and 100");
    }

    // UT-024: 風險分數 > 100 時拋出 IllegalStateException
    @Test
    void place_riskScoreAbove100_shouldThrowIllegalStateException() {
        OrderPlacementCommand command = createValidCommand();

        when(orderPlacementDao.findByIdempotencyKey(IDEMPOTENCY_KEY))
                .thenReturn(Optional.empty());
        when(riskAssessmentGateway.assess(CUSTOMER_ID, AMOUNT))
                .thenReturn(101);

        assertThatThrownBy(() -> orderPlacementService.place(command))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("risk score must be between 0 and 100");
    }

    // UT-025: 付款授權 approved 但 authorizationId 為 null 時拋出 IllegalStateException
    @Test
    void place_approvedPaymentWithNullAuthId_shouldThrowIllegalStateException() {
        OrderPlacementCommand command = createValidCommand();
        StockReservation reservation = new StockReservation("RES-001", SKU, QUANTITY);
        PaymentAuthorization authorization = new PaymentAuthorization(true, null);

        when(orderPlacementDao.findByIdempotencyKey(IDEMPOTENCY_KEY))
                .thenReturn(Optional.empty());
        when(riskAssessmentGateway.assess(CUSTOMER_ID, AMOUNT))
                .thenReturn(MANUAL_REVIEW_SCORE - 1);
        when(stockReservationGateway.reserve(SKU, QUANTITY))
                .thenReturn(Optional.of(reservation));
        when(paymentGateway.authorize(ORDER_ID, AMOUNT, CURRENCY, PAYMENT_TOKEN))
                .thenReturn(authorization);

        assertThatThrownBy(() -> orderPlacementService.place(command))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("approved payment must have an authorization ID");
    }

    // UT-026: 付款授權 approved 但 authorizationId 為空白時拋出 IllegalStateException
    @Test
    void place_approvedPaymentWithBlankAuthId_shouldThrowIllegalStateException() {
        OrderPlacementCommand command = createValidCommand();
        StockReservation reservation = new StockReservation("RES-001", SKU, QUANTITY);
        PaymentAuthorization authorization = new PaymentAuthorization(true, "   ");

        when(orderPlacementDao.findByIdempotencyKey(IDEMPOTENCY_KEY))
                .thenReturn(Optional.empty());
        when(riskAssessmentGateway.assess(CUSTOMER_ID, AMOUNT))
                .thenReturn(MANUAL_REVIEW_SCORE - 1);
        when(stockReservationGateway.reserve(SKU, QUANTITY))
                .thenReturn(Optional.of(reservation));
        when(paymentGateway.authorize(ORDER_ID, AMOUNT, CURRENCY, PAYMENT_TOKEN))
                .thenReturn(authorization);

        assertThatThrownBy(() -> orderPlacementService.place(command))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("approved payment must have an authorization ID");
    }
}
