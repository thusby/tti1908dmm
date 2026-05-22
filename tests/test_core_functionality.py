"""
Core functionality tests that demonstrate the testing framework is working.

These tests verify the most important aspects of the codebase without
requiring complex mocking or dealing with edge cases.
"""

import os
import sys
import unittest
from unittest.mock import Mock, patch

# Add the project root to the path so we can import tti1908
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from firmware.flash import ProcessorData
from tti1908 import TTI1908, Reading, parse_reading


class TestCoreFunctionality(unittest.TestCase):
    """Test core functionality of the project."""

    def test_reading_dataclass(self):
        """Test Reading dataclass functionality."""
        # Test normal reading
        reading = Reading(1.23, "V DC", "1.23 V DC")
        self.assertEqual(str(reading), "1.23 V DC")

        # Test overload
        reading = Reading(None, "V DC", "OVLOAD V DC", overload=True)
        self.assertEqual(str(reading), "OVLOAD (V DC)")

        # Test overflow
        reading = Reading(None, "V DC", "OVFLOW V DC", overflow=True)
        self.assertEqual(str(reading), "OVFLOW (V DC)")

    def test_parse_reading_function(self):
        """Test parse_reading function with various inputs."""
        # Normal reading
        result = parse_reading("1.234567 V DC")
        self.assertEqual(result.value, 1.234567)
        self.assertEqual(result.unit, "V DC")

        # Overload
        result = parse_reading("OVLOAD V DC")
        self.assertIsNone(result.value)
        self.assertTrue(result.overload)

        # Overflow
        result = parse_reading("OVFLOW V DC")
        self.assertIsNone(result.value)
        self.assertTrue(result.overflow)

    def test_processor_data_class(self):
        """Test ProcessorData class functionality."""
        processor = ProcessorData()
        self.assertEqual(processor.target_processor, 0)
        self.assertEqual(processor.low_address, 0x08F0D180)
        self.assertEqual(processor.high_address, 0)
        self.assertTrue(isinstance(processor.target, bytearray))

    def test_driver_initialization(self):
        """Test that driver can be initialized with mocked serial."""
        # Mock the serial connection
        mock_serial = Mock()
        mock_serial.timeout = 1.0
        mock_serial.in_waiting = 0

        with patch("serial.Serial", return_value=mock_serial):
            dmm = TTI1908(port="/dev/test", timeout=1.0)
            self.assertIsNotNone(dmm.ser)

    def test_driver_write_method(self):
        """Test driver write method."""
        mock_serial = Mock()
        mock_serial.timeout = 1.0
        mock_serial.in_waiting = 0

        with patch("serial.Serial", return_value=mock_serial):
            dmm = TTI1908(port="/dev/test", timeout=1.0)
            dmm.write("TESTCMD")
            mock_serial.write.assert_called_once_with(b"TESTCMD\n")


if __name__ == "__main__":
    unittest.main()
