"""
Unit tests for the firmware parser functionality.

These tests verify the HEX parsing logic without requiring actual firmware files.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import mock_open, patch

# Add the project root to the path so we can import firmware modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from firmware.flash import ProcessorData, parse_hex


class TestProcessorDataClass(unittest.TestCase):
    """Test the ProcessorData class functionality."""

    def test_processor_data_initialization(self):
        """Test ProcessorData initialization."""
        processor = ProcessorData()
        self.assertEqual(processor.target_processor, 0)
        self.assertEqual(processor.low_address, 0x08F0D180)
        self.assertEqual(processor.high_address, 0)
        self.assertTrue(isinstance(processor.target, bytearray))
        self.assertFalse(processor._finished)
        self.assertFalse(processor._starts_at_low)

    def test_is_empty_property(self):
        """Test the is_empty property."""
        processor = ProcessorData()
        # Should be empty initially
        self.assertTrue(processor.is_empty)

        # Make it not empty
        processor.target[0] = 0xFF
        processor.high_address = 0
        processor.low_address = 0
        self.assertFalse(processor.is_empty)

    def test_finished_load(self):
        """Test the finished_load method."""
        processor = ProcessorData()
        # Set some data
        processor.target[100] = 0xAA
        processor.high_address = 100
        processor.low_address = 50

        processor.finished_load()
        # Should trim the target to the [low..high] range
        self.assertEqual(len(processor.target), 51)  # 100 - 50 + 1
        self.assertTrue(processor._finished)
        self.assertTrue(processor._starts_at_low)

    def test_byte_at_method(self):
        """Test the byte_at method."""
        processor = ProcessorData()
        processor.target[100] = 0xAA
        processor.high_address = 100
        processor.low_address = 50

        # Test before finished_load
        self.assertEqual(processor.byte_at(100), 0xAA)

        # Test after finished_load
        processor.finished_load()
        self.assertEqual(processor.byte_at(100), 0xAA)


class TestParseHexFunction(unittest.TestCase):
    """Test the parse_hex function with various HEX inputs."""

    def test_parse_empty_hex(self):
        """Test parsing empty HEX content."""
        # Create a temporary file with empty content
        with tempfile.NamedTemporaryFile(mode="w", suffix=".hex", delete=False) as f:
            f.write("")
            temp_filename = f.name

        try:
            # Mock the file reading to return empty content
            with patch("builtins.open", mock_open(read_data="")):
                # This should not raise an exception, but return empty list
                result = parse_hex(temp_filename)
                # Empty file should return empty list, not raise exception
                self.assertEqual(len(result), 0)
        finally:
            os.unlink(temp_filename)

    def test_parse_simple_hex_record(self):
        """Test parsing a simple HEX record."""
        # Create a simple HEX content with one data record
        hex_content = """:020000000102F9
:0400000001020304F0
:00000001FF
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".hex", delete=False) as f:
            f.write(hex_content)
            temp_filename = f.name

        try:
            with patch("builtins.open", mock_open(read_data=hex_content)):
                result = parse_hex(temp_filename)
                self.assertEqual(len(result), 1)
                # Should have one processor with data
                processor = result[0]
                self.assertEqual(processor.target_processor, 0)
                # Check that data was parsed correctly
                self.assertEqual(processor.target[0], 0x01)
                self.assertEqual(processor.target[1], 0x02)
                self.assertEqual(processor.target[2], 0x03)
                self.assertEqual(processor.target[3], 0x04)
        finally:
            os.unlink(temp_filename)

    def test_parse_hex_with_processor_marker(self):
        """Test parsing HEX with processor marker."""
        hex_content = "#TTI#0001\n:0400000001020304F0\n:00000001FF\n"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".hex", delete=False) as f:
            f.write(hex_content)
            temp_filename = f.name

        try:
            with patch("builtins.open", mock_open(read_data=hex_content)):
                result = parse_hex(temp_filename)
                self.assertEqual(len(result), 1)
                processor = result[0]
                self.assertEqual(processor.target_processor, 1)
        finally:
            os.unlink(temp_filename)


if __name__ == "__main__":
    unittest.main()
