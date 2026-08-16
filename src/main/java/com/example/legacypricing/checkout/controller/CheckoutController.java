package com.example.legacypricing.checkout.controller;

import com.example.legacypricing.checkout.dto.CheckoutRequest;
import com.example.legacypricing.checkout.dto.CheckoutResponse;
import com.example.legacypricing.checkout.model.CheckoutResult;
import com.example.legacypricing.checkout.model.CheckoutStatus;
import com.example.legacypricing.checkout.service.CheckoutUseCase;
import jakarta.validation.Valid;
import java.util.Objects;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Spring controller that handles checkout requests to reserve inventory
 * and create a payment deadline.
 */
@RestController
@RequestMapping("/api/checkouts")
public final class CheckoutController {

    private final CheckoutUseCase checkoutUseCase;

    /**
     * Creates the controller with the given use case.
     *
     * @param checkoutUseCase the checkout use case; must not be null
     */
    public CheckoutController(CheckoutUseCase checkoutUseCase) {
        this.checkoutUseCase = Objects.requireNonNull(checkoutUseCase, "checkoutUseCase");
    }

    /**
     * Returns HTTP 201 for PAYMENT_PENDING and HTTP 409 for OUT_OF_STOCK.
     */
    @PostMapping
    public ResponseEntity<CheckoutResponse> checkout(@Valid @RequestBody CheckoutRequest request) {
        CheckoutResult result = checkoutUseCase.checkout(
                request.orderId(),
                request.sku(),
                request.quantity()
        );
        HttpStatus status = result.status() == CheckoutStatus.PAYMENT_PENDING
                ? HttpStatus.CREATED
                : HttpStatus.CONFLICT;
        return ResponseEntity.status(status).body(CheckoutResponse.from(result));
    }
}
