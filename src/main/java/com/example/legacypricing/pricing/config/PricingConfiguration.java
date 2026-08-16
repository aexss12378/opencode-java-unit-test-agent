package com.example.legacypricing.pricing.config;

import java.time.Clock;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration(proxyBeanMethods = false)
class PricingConfiguration {

    /**
     * Provides the system clock used across the pricing module. The clock is fixed to UTC so that all pricing calculations use a consistent, timezone-independent time source.
     */
    @Bean
    Clock systemClock() {
        return Clock.systemUTC();
    }
}
