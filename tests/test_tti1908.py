"""
Unit tests for the TTI1908 driver.

These tests verify the core functionality of the driver without requiring
actual hardware connections.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, Mock, patch

# Add the project root to the path so we can import tti1908
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from tti1908 import TTI1908, Reading, parse_reading


class TestReadingClass(unittest.TestCase):
    """Test the Reading dataclass functionality."""

    def test_reading_str_representation(self):
        """Test string representation of Reading objects."""
        # Normal reading
        reading = Reading(1.23, "V DC", "1.23 V DC")
        self.assertEqual(str(reading), "1.23 V DC")

        # Overload
        reading = Reading(None, "V DC", "OVLOAD V DC", overload=True)
        self.assertEqual(str(reading), "OVLOAD (V DC)")

        # Overflow
        reading = Reading(None, "V DC", "OVFLOW V DC", overflow=True)
        self.assertEqual(str(reading), "OVFLOW (V DC)")

        # Scientific notation
        reading = Reading(1.23e-6, "V DC", "1.23e-06 V DC")
        self.assertEqual(str(reading), "1.23e-06 V DC")


class TestParseReadingFunction(unittest.TestCase):
    """Test the parse_reading function with various inputs."""

    def test_parse_normal_reading(self):
        """Test parsing normal numeric readings."""
        result = parse_reading("1.234567 V DC")
        self.assertEqual(result.value, 1.234567)
        self.assertEqual(result.unit, "V DC")
        self.assertEqual(result.raw, "1.234567 V DC")

    def test_parse_negative_reading(self):
        """Test parsing negative numeric readings."""
        result = parse_reading("-1.234567 V DC")
        self.assertEqual(result.value, -1.234567)
        self.assertEqual(result.unit, "V DC")

    def test_parse_scientific_notation(self):
        """Test parsing scientific notation."""
        result = parse_reading("1.23e-06 V DC")
        self.assertEqual(result.value, 1.23e-06)
        self.assertEqual(result.unit, "V DC")

    def test_parse_overload(self):
        """Test parsing overload readings."""
        result = parse_reading("OVLOAD V DC")
        self.assertIsNone(result.value)
        self.assertEqual(result.unit, "V DC")
        self.assertTrue(result.overload)
        self.assertFalse(result.overflow)

    def test_parse_overflow(self):
        """Test parsing overflow readings."""
        result = parse_reading("OVFLOW V DC")
        self.assertIsNone(result.value)
        self.assertEqual(result.unit, "V DC")
        self.assertFalse(result.overload)
        self.assertTrue(result.overflow)

    def test_parse_empty_string(self):
        """Test parsing empty strings."""
        result = parse_reading("")
        self.assertIsNone(result.value)
        self.assertEqual(result.unit, "")
        self.assertTrue(result.overload)

    def test_parse_whitespace_only(self):
        """Test parsing whitespace-only strings."""
        result = parse_reading("   ")
        self.assertIsNone(result.value)
        self.assertEqual(result.unit, "")
        self.assertTrue(result.overload)


class TestTTI1908Driver(unittest.TestCase):
    """Test the TTI1908 driver class functionality."""

    def setUp(self):
        """Set up test fixtures."""
        # Mock the serial connection
        self.mock_serial = Mock()
        # Mock the timeout attribute to be a float
        self.mock_serial.timeout = 1.0
        # Mock in_waiting to be a property
        self.mock_serial.in_waiting = 0
        with patch("serial.Serial", return_value=self.mock_serial):
            self.dmm = TTI1908(port="/dev/test", timeout=1.0)

    def test_init_with_mocked_serial(self):
        """Test driver initialization with mocked serial."""
        self.assertIsNotNone(self.dmm.ser)
        self.assertTrue(self.dmm.ser.reset_input_buffer.called)

    def test_write_method(self):
        """Test the write method sends correct data."""
        self.dmm.write("TESTCMD")
        self.mock_serial.write.assert_called_once_with(b"TESTCMD\n")

    def test_query_method_success(self):
        """Test successful query execution."""
        self.mock_serial.read_until.return_value = b"TESTRESPONSE\r\n"
        result = self.dmm.query("TESTCMD")
        self.assertEqual(result, "TESTRESPONSE")
        self.mock_serial.write.assert_called_once_with(b"TESTCMD\n")

    def test_query_method_timeout(self):
        """Test query timeout handling."""
        # Simulate timeout by returning empty bytes multiple times
        self.mock_serial.read_until.side_effect = [b"", b"", b"", b"", b""]
        with self.assertRaises(TimeoutError):
            self.dmm.query("TESTCMD")

    def test_idn_method(self):
        """Test IDN query."""
        self.mock_serial.read_until.return_value = b"Test,Vendor,Model,1.00\r\n"
        result = self.dmm.idn()
        self.assertEqual(result, "Test,Vendor,Model,1.00")

    def test_identify_method(self):
        """Test identify method parsing."""
        self.mock_serial.read_until.return_value = b"Test,Vendor,Model,1.00\r\n"
        result = self.dmm.identify()
        self.assertEqual(result, ("Test", "Vendor", "Model", "1.00"))

    def test_firmware_version_method(self):
        """Test firmware version extraction."""
        self.mock_serial.read_until.return_value = b"Test,Vendor,Model,1.00\r\n"
        result = self.dmm.firmware_version()
        self.assertEqual(result, "1.00")


if __name__ == "__main__":
    unittest.main()
