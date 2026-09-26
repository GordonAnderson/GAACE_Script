#include <unity.h>
#include <string.h>
#include "GAACEScriptHex.h"

using namespace GAACEScript;

void setUp(void) {}
void tearDown(void) {}

static void test_decodes_known_bytes(void) {
  uint8_t out[8];
  uint16_t len = 0;
  bool ok = hexDecode("18010005", out, sizeof(out), len);
  TEST_ASSERT_TRUE(ok);
  TEST_ASSERT_EQUAL(4, len);
  uint8_t expected[] = {0x18, 0x01, 0x00, 0x05};
  TEST_ASSERT_EQUAL_MEMORY(expected, out, 4);
}

static void test_mixed_case_accepted(void) {
  uint8_t out[8];
  uint16_t len = 0;
  TEST_ASSERT_TRUE(hexDecode("aB", out, sizeof(out), len));
  TEST_ASSERT_EQUAL(1, len);
  TEST_ASSERT_EQUAL_HEX8(0xAB, out[0]);
}

static void test_empty_string_decodes_to_zero_bytes(void) {
  uint8_t out[8];
  uint16_t len = 99;
  TEST_ASSERT_TRUE(hexDecode("", out, sizeof(out), len));
  TEST_ASSERT_EQUAL(0, len);
}

static void test_odd_length_rejected(void) {
  uint8_t out[8];
  uint16_t len = 0;
  TEST_ASSERT_FALSE(hexDecode("abc", out, sizeof(out), len));
}

static void test_non_hex_char_rejected(void) {
  uint8_t out[8];
  uint16_t len = 0;
  TEST_ASSERT_FALSE(hexDecode("zz", out, sizeof(out), len));
}

static void test_too_long_for_buffer_rejected(void) {
  uint8_t out[2];
  uint16_t len = 0;
  TEST_ASSERT_FALSE(hexDecode("aabbcc", out, sizeof(out), len));  // 3 bytes, cap 2
}

int main(int argc, char **argv) {
  UNITY_BEGIN();
  RUN_TEST(test_decodes_known_bytes);
  RUN_TEST(test_mixed_case_accepted);
  RUN_TEST(test_empty_string_decodes_to_zero_bytes);
  RUN_TEST(test_odd_length_rejected);
  RUN_TEST(test_non_hex_char_rejected);
  RUN_TEST(test_too_long_for_buffer_rejected);
  return UNITY_END();
}
