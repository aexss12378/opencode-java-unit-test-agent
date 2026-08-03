package com.example.legacypricing;

import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import java.math.BigDecimal;

public record QuoteRequest(
        @NotBlank String customerId,
        @NotNull @DecimalMin("0.00") BigDecimal unitPrice,
        @Min(1) int quantity
) {
}
