package com.example.legacypricing;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

@SpringBootApplication
@ConfigurationPropertiesScan
public class EnterpriseOrderApplication {

    public static void main(String[] args) {
        SpringApplication.run(EnterpriseOrderApplication.class, args);
    }
}
