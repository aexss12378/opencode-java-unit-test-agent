package com.example.legacypricing.order.infra;

import com.example.legacypricing.order.port.RiskAssessmentGateway;
import java.math.BigDecimal;
import org.springframework.stereotype.Component;

@Component
public final class RuleBasedRiskAssessmentGateway implements RiskAssessmentGateway {

    /**
     * Local profile rule: exact prefixes RISK- and REVIEW- produce scores 95
     * and 70 respectively; every other customer produces score 10.
     */
    @Override
    public int assess(String customerId, BigDecimal total) {
        if (customerId.startsWith("RISK-")) {
            return 95;
        }
        if (customerId.startsWith("REVIEW-")) {
            return 70;
        }
        return 10;
    }
}
