package com.example.legacypricing;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

/**
 * Spring Boot entry point for the enterprise order platform.
 *
 * Bootstraps the application context with component scanning and configuration
 * properties auto-discovery.
 */
@SpringBootApplication
@ConfigurationPropertiesScan
public class EnterpriseOrderApplication {

    /**
     * Launches the enterprise order platform.
     *
     * Initializes the Spring application context and starts the embedded web server.
     *
     * @param args command-line arguments passed to the application
     */
    public static void main(String[] args) {
        SpringApplication.run(EnterpriseOrderApplication.class, args);
    }
}
