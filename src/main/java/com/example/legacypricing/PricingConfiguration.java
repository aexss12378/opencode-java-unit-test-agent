package com.example.legacypricing;

import java.time.Clock;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration(proxyBeanMethods = false)
class PricingConfiguration {

    @Bean
    Clock systemClock() {
        return Clock.systemUTC();
    }
}
