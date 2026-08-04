package com.example.legacypricing.order.util;

import com.example.legacypricing.order.OrderPlacementCommand;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Objects;

/**
 * Builds a stable fingerprint for exact idempotency comparisons without persisting
 * the payment token.
 */
public final class OrderRequestFingerprint {

    private OrderRequestFingerprint() {
    }

    public static String sha256(OrderPlacementCommand command) {
        Objects.requireNonNull(command, "command");

        StringBuilder canonical = new StringBuilder();
        append(canonical, command.orderId().value());
        append(canonical, command.idempotencyKey().value());
        append(canonical, command.customerId());
        append(canonical, command.sku());
        append(canonical, Integer.toString(command.quantity()));
        append(canonical, command.total().amount().toPlainString());
        append(canonical, command.total().currency());
        append(canonical, command.paymentToken());

        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] bytes = digest.digest(canonical.toString().getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(bytes);
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }

    private static void append(StringBuilder canonical, String value) {
        Objects.requireNonNull(value, "fingerprint field");
        int byteLength = value.getBytes(StandardCharsets.UTF_8).length;
        canonical.append(byteLength).append(':').append(value).append('|');
    }
}
