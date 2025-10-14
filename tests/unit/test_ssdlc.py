# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for SSDLC logging functionality."""

import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

from ssdlc import Service, SSDLCSysEvent, log_ssdlc_system_event

# Add src directory to path
sys.path.append("src")


class TestSSDLCLogging(unittest.TestCase):
    """Test SSDLC logging functions."""

    @mock.patch("ssdlc.logger")
    @mock.patch("ssdlc.datetime")
    def test_log_ssdlc_system_event_with_default_service(self, mock_datetime, mock_logger):
        """Test logging with default service."""
        # Setup mock datetime
        mock_now = mock.MagicMock()
        mock_now.isoformat.return_value = "2025-01-01T12:00:00+00:00"
        mock_datetime.now.return_value.astimezone.return_value = mock_now

        # Call the function without specifying service (uses default)
        log_ssdlc_system_event(SSDLCSysEvent.STARTUP)

        # Verify logger was called correctly
        mock_logger.warning.assert_called_once()
        logged_data = mock_logger.warning.call_args[0][0]

        self.assertEqual(logged_data["datetime"], "2025-01-01T12:00:00+00:00")
        self.assertEqual(logged_data["appid"], "service.juju-backup-all-exporter")
        self.assertEqual(logged_data["event"], "sys_startup:juju-backup-all-exporter")
        self.assertEqual(logged_data["level"], "WARN")
        self.assertIn("juju-backup-all start service", logged_data["description"])

    @mock.patch("ssdlc.logger")
    @mock.patch("ssdlc.datetime")
    def test_log_ssdlc_system_event_startup(self, mock_datetime, mock_logger):
        """Test logging STARTUP event."""
        # Setup mock datetime
        mock_now = mock.MagicMock()
        mock_now.isoformat.return_value = "2025-01-01T12:00:00+00:00"
        mock_datetime.now.return_value.astimezone.return_value = mock_now

        # Call the function
        log_ssdlc_system_event(SSDLCSysEvent.STARTUP, Service.JUJU_BACKUP_ALL_EXPORTER, "")

        # Verify logger was called
        mock_logger.warning.assert_called_once()
        logged_data = mock_logger.warning.call_args[0][0]

        self.assertEqual(logged_data["datetime"], "2025-01-01T12:00:00+00:00")
        self.assertEqual(logged_data["appid"], "service.juju-backup-all-exporter")
        self.assertEqual(logged_data["event"], "sys_startup:juju-backup-all-exporter")
        self.assertEqual(logged_data["level"], "WARN")
        self.assertIsInstance(logged_data["description"], str)

    @mock.patch("ssdlc.logger")
    @mock.patch("ssdlc.datetime")
    def test_log_ssdlc_system_event_shutdown(self, mock_datetime, mock_logger):
        """Test logging SHUTDOWN event."""
        # Setup mock datetime
        mock_now = mock.MagicMock()
        mock_now.isoformat.return_value = "2025-01-01T12:00:00+00:00"
        mock_datetime.now.return_value.astimezone.return_value = mock_now

        # Call the function
        log_ssdlc_system_event(SSDLCSysEvent.SHUTDOWN, Service.JUJU_BACKUP_ALL_EXPORTER, "")

        # Verify logger was called
        mock_logger.warning.assert_called_once()
        logged_data = mock_logger.warning.call_args[0][0]

        self.assertEqual(logged_data["datetime"], "2025-01-01T12:00:00+00:00")
        self.assertEqual(logged_data["appid"], "service.juju-backup-all-exporter")
        self.assertEqual(logged_data["event"], "sys_shutdown:juju-backup-all-exporter")
        self.assertEqual(logged_data["level"], "WARN")

    @mock.patch("ssdlc.logger")
    @mock.patch("ssdlc.datetime")
    def test_log_ssdlc_system_event_restart(self, mock_datetime, mock_logger):
        """Test logging RESTART event."""
        # Setup mock datetime
        mock_now = mock.MagicMock()
        mock_now.isoformat.return_value = "2025-01-01T12:00:00+00:00"
        mock_datetime.now.return_value.astimezone.return_value = mock_now

        # Call the function
        log_ssdlc_system_event(SSDLCSysEvent.RESTART, Service.JUJU_BACKUP_ALL_EXPORTER, "")

        # Verify logger was called
        mock_logger.warning.assert_called_once()
        logged_data = mock_logger.warning.call_args[0][0]

        self.assertEqual(logged_data["datetime"], "2025-01-01T12:00:00+00:00")
        self.assertEqual(logged_data["appid"], "service.juju-backup-all-exporter")
        self.assertEqual(logged_data["event"], "sys_restart:juju-backup-all-exporter")
        self.assertEqual(logged_data["level"], "WARN")

    @mock.patch("ssdlc.logger")
    @mock.patch("ssdlc.datetime")
    def test_log_ssdlc_system_event_crash_with_message(self, mock_datetime, mock_logger):
        """Test logging CRASH event with message."""
        # Setup mock datetime
        mock_now = mock.MagicMock()
        mock_now.isoformat.return_value = "2025-01-01T12:00:00+00:00"
        mock_datetime.now.return_value.astimezone.return_value = mock_now

        # Call the function
        log_ssdlc_system_event(
            SSDLCSysEvent.CRASH, Service.JUJU_BACKUP_ALL_EXPORTER, "Connection timeout"
        )

        # Verify logger was called
        mock_logger.warning.assert_called_once()
        logged_data = mock_logger.warning.call_args[0][0]

        self.assertEqual(logged_data["datetime"], "2025-01-01T12:00:00+00:00")
        self.assertEqual(logged_data["appid"], "service.juju-backup-all-exporter")
        self.assertEqual(logged_data["event"], "sys_crash:juju-backup-all-exporter")
        self.assertEqual(logged_data["level"], "WARN")
        self.assertIn("Connection timeout", logged_data["description"])

    @mock.patch("ssdlc.logger")
    @mock.patch("ssdlc.datetime")
    def test_log_ssdlc_system_event_with_additional_message(self, mock_datetime, mock_logger):
        """Test logging with additional message."""
        # Setup mock datetime
        mock_now = mock.MagicMock()
        mock_now.isoformat.return_value = "2025-01-01T12:00:00+00:00"
        mock_datetime.now.return_value.astimezone.return_value = mock_now

        # Call with additional message
        additional_msg = "Service failed due to network error"
        log_ssdlc_system_event(
            SSDLCSysEvent.CRASH, Service.JUJU_BACKUP_ALL_EXPORTER, additional_msg
        )

        # Verify the additional message is included
        logged_data = mock_logger.warning.call_args[0][0]
        self.assertIn(additional_msg, logged_data["description"])

    @mock.patch("ssdlc.logger")
    @mock.patch("ssdlc.datetime")
    def test_log_ssdlc_system_event_datetime_format(self, mock_datetime, mock_logger):
        """Test that datetime is in ISO 8601 format with timezone."""
        # Use a real datetime to test formatting
        test_time = datetime(2025, 1, 15, 14, 30, 45, tzinfo=timezone.utc)
        mock_datetime.now.return_value.astimezone.return_value = test_time

        log_ssdlc_system_event(SSDLCSysEvent.STARTUP, Service.JUJU_BACKUP_ALL_EXPORTER)

        logged_data = mock_logger.warning.call_args[0][0]
        # Verify ISO 8601 format with timezone
        self.assertRegex(
            logged_data["datetime"],
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}",
        )


if __name__ == "__main__":
    unittest.main()
