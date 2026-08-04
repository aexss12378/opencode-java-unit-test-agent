package com.example.legacypricing.pricing.controller;

import com.example.legacypricing.pricing.dto.OrderQuote;
import com.example.legacypricing.pricing.dto.QuoteRequest;
import com.example.legacypricing.pricing.service.OrderQuoteUseCase;
import jakarta.validation.Valid;
import java.util.Objects;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/quotes")
public final class OrderQuoteController {

    private final OrderQuoteUseCase orderQuoteUseCase;

    public OrderQuoteController(OrderQuoteUseCase orderQuoteUseCase) {
        this.orderQuoteUseCase = Objects.requireNonNull(orderQuoteUseCase, "orderQuoteUseCase");
    }

    @PostMapping
    public OrderQuote quote(@Valid @RequestBody QuoteRequest request) {
        return orderQuoteUseCase.quote(request.customerId(), request.unitPrice(), request.quantity());
    }
}
