package com.example.legacypricing.common.api;

import com.example.legacypricing.order.exception.IdempotencyConflictException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice
public final class ApiExceptionHandler {

    @ExceptionHandler(IllegalArgumentException.class)
    ResponseEntity<ApiError> handleIllegalArgument(IllegalArgumentException exception) {
        return ResponseEntity
                .status(HttpStatus.BAD_REQUEST)
                .body(new ApiError("INVALID_REQUEST", exception.getMessage()));
    }

    /**
     * Returns HTTP 409 when an idempotency key is reused for a different request.
     */
    @ExceptionHandler(IdempotencyConflictException.class)
    ResponseEntity<ApiError> handleIdempotencyConflict(
            IdempotencyConflictException exception
    ) {
        return ResponseEntity
                .status(HttpStatus.CONFLICT)
                .body(new ApiError("IDEMPOTENCY_CONFLICT", exception.getMessage()));
    }
}
