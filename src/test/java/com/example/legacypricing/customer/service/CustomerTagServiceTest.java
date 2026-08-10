package com.example.legacypricing.customer.service;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullSource;
import org.junit.jupiter.params.provider.ValueSource;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class CustomerTagServiceTest {

    // UT-001: 建構子收到 null prefix 應拋出 NullPointerException
    @Test
    @DisplayName("Constructor with null prefix should throw NullPointerException")
    void constructor_withNullPrefix_shouldThrowNullPointerException() {
        // evidence: 目前實作：建構子第12行 Objects.requireNonNull(prefix, "prefix")
        assertThatThrownBy(() -> new CustomerTagService(null))
                .isInstanceOf(NullPointerException.class)
                .hasMessageContaining("prefix");
    }

    // UT-002: 建構子收到有效 prefix 應成功建立實例
    @Test
    @DisplayName("Constructor with valid prefix should create instance successfully")
    void constructor_withValidPrefix_shouldCreateInstanceSuccessfully() {
        // evidence: 目前實作：建構子正常初始化
        CustomerTagService service = new CustomerTagService("VIP");

        assertThat(service).isNotNull();
    }

    // UT-003: createTag 收到 null customerId 應拋出 IllegalArgumentException
    @Test
    @DisplayName("createTag with null customerId should throw IllegalArgumentException")
    void createTag_withNullCustomerId_shouldThrowIllegalArgumentException() {
        // evidence: 目前實作：第16-18行
        CustomerTagService service = new CustomerTagService("VIP");

        assertThatThrownBy(() -> service.createTag(null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("customerId must not be blank");
    }

    // UT-004, UT-005: createTag 收到空白字串應拋出 IllegalArgumentException
    @ParameterizedTest
    @ValueSource(strings = {"", "   ", "\t", "\n", "  \t  "})
    @DisplayName("createTag with blank customerId should throw IllegalArgumentException")
    void createTag_withBlankCustomerId_shouldThrowIllegalArgumentException(String customerId) {
        // evidence: 目前實作：第16-18行 isBlank()
        CustomerTagService service = new CustomerTagService("VIP");

        assertThatThrownBy(() -> service.createTag(customerId))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("customerId must not be blank");
    }

    // UT-006: createTag 收到長度超過12的字串應拋出 IllegalArgumentException
    @Test
    @DisplayName("createTag with customerId longer than 12 characters should throw IllegalArgumentException")
    void createTag_withCustomerIdLongerThan12_shouldThrowIllegalArgumentException() {
        // evidence: 目前實作：第21-25行
        CustomerTagService service = new CustomerTagService("VIP");
        String longCustomerId = "1234567890123"; // 13 characters

        assertThatThrownBy(() -> service.createTag(longCustomerId))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("customerId must be at most 12 characters");
    }

    // UT-007: createTag 收到長度恰好12的字串應成功回傳
    @Test
    @DisplayName("createTag with customerId exactly 12 characters should return formatted tag")
    void createTag_withCustomerIdExactly12_shouldReturnFormattedTag() {
        // evidence: 目前實作：邊界條件，第27行
        CustomerTagService service = new CustomerTagService("VIP");
        String customerId = "123456789012"; // exactly 12 characters

        String result = service.createTag(customerId);

        assertThat(result).isEqualTo("VIP-123456789012");
    }

    // UT-008: createTag 收到前後有空格的字串應去除空格後回傳
    @Test
    @DisplayName("createTag with customerId having leading and trailing spaces should trim and return formatted tag")
    void createTag_withCustomerIdHavingSpaces_shouldTrimAndReturnFormattedTag() {
        // evidence: 目前實作：第20行 trim()
        CustomerTagService service = new CustomerTagService("VIP");

        String result = service.createTag("  CUST001  ");

        assertThat(result).isEqualTo("VIP-CUST001");
    }

    // UT-009: createTag 收到有效 customerId 應回傳正確格式
    @Test
    @DisplayName("createTag with valid customerId should return formatted tag")
    void createTag_withValidCustomerId_shouldReturnFormattedTag() {
        // evidence: 目前實作：第27行
        CustomerTagService service = new CustomerTagService("VIP");

        String result = service.createTag("CUST001");

        assertThat(result).isEqualTo("VIP-CUST001");
    }

    // 額外測試：驗證不同 prefix 也能正確運作
    @Test
    @DisplayName("createTag with different prefix should return correctly formatted tag")
    void createTag_withDifferentPrefix_shouldReturnCorrectlyFormattedTag() {
        // evidence: 目前實作：第27行 prefix + "-" + normalizedCustomerId
        CustomerTagService service = new CustomerTagService("PREMIUM");

        String result = service.createTag("ABC123");

        assertThat(result).isEqualTo("PREMIUM-ABC123");
    }
}
