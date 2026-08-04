package com.example.legacypricing.pricing.service;

import com.example.legacypricing.pricing.dto.OrderQuote;
import java.math.BigDecimal;

public interface OrderQuoteUseCase {

    OrderQuote quote(String customerId, BigDecimal unitPrice, int quantity);
}
