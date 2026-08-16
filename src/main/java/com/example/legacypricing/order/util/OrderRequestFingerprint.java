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

    /**
     * Computes a stable SHA-256 fingerprint of the given order placement command.
     *
     * The fingerprint is formed by concatenating the canonical representations of
     * {@code orderId}, {@code idempotencyKey}, {@code customerId}, {@code sku},
     * {@code quantity}, {@code total.amount}, and {@code total.currency}, each
     * prefixed with its UTF-8 byte length followed by a colon and terminated by
     * a pipe character. The payment token is also included in the hash but is
     * never persisted.
     *
     * <p>Returns the fingerprint as a lowercase hexadecimal string.
     *
     * @throws IllegalStateException if SHA-256 is unavailable (should not occur).
     * @param command the order placement command to fingerprint.
     * @return a lowercase hexadecimal SHA-256 digest of the canonical command representation.
     */
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
