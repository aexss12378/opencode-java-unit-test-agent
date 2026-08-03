package com.example.legacypricing;

import java.math.BigDecimal;

public interface OrderQuoteUseCase {

    OrderQuote quote(String customerId, BigDecimal unitPrice, int quantity);
}
