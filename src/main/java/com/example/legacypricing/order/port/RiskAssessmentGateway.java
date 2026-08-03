package com.example.legacypricing.order.port;

import java.math.BigDecimal;

@FunctionalInterface
public interface RiskAssessmentGateway {

    int assess(String customerId, BigDecimal total);
}
