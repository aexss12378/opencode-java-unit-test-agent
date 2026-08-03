package com.example.legacypricing;

import jakarta.validation.Valid;
import java.util.Objects;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/checkouts")
public final class CheckoutController {

    private final CheckoutUseCase checkoutUseCase;

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
