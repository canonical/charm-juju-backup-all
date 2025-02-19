# Copyright 2023 Canotical Ltd.
# See LICENSE file for licensing details.

import pathlib
import unittest
from unittest import mock

import ops
from ops.testing import Harness

from charm import JujuBackupAllCharm
from config import EXPORTER_NAME, EXPORTER_RELATION_NAME
from exporter import Exporter

ops.testing.SIMULATE_CAN_CONNECT = True


def patch_snap_installed():
    mock_snap = mock.Mock()
    mock_snap.present = True
    mock_snap.services = {EXPORTER_NAME: {"active": True}}
    return mock.patch(
        "charms.operator_libs_linux.v1.snap.SnapCache",
        return_value={EXPORTER_NAME: mock_snap},
    )


def patch_snap_not_installed():
    mock_snap = mock.Mock()
    mock_snap.present = False
    mock_snap.services = {EXPORTER_NAME: {"active": False}}
    return mock.patch(
        "charms.operator_libs_linux.v1.snap.SnapCache",
        return_value={EXPORTER_NAME: mock_snap},
    )


def patch_snap_path_exist():
    return mock.patch.object(
        JujuBackupAllCharm,
        "snap_path",
        new_callable=mock.PropertyMock,
        return_value="prometheus-juju-backup-all-exporter.snap",
    )


def patch_snap_path_not_exist():
    return mock.patch.object(
        JujuBackupAllCharm, "snap_path", new_callable=mock.PropertyMock, return_value=""
    )


class TestExporter(unittest.TestCase):
    """Test Exporter's methods."""

    @classmethod
    def setUpClass(cls):
        """Set up class fixture."""
        # patch builtins open
        cls.open_patcher = mock.patch("builtins.open", new_callable=mock.mock_open)
        cls.open_patcher.start()
        # patch relevant modules/methods
        cls.nrpe_support_patcher = mock.patch("utils.NRPE")
        cls.nrpe_support_patcher.start()

        cls.charm_dir_patcher = mock.patch("charmhelpers.core.hookenv.charm_dir")
        patch = cls.charm_dir_patcher.start()
        patch.return_value = str(pathlib.Path(__file__).parents[2].absolute())

    @classmethod
    def tearDownClass(cls):
        """Tear down class fixture."""
        cls.open_patcher.stop()
        cls.charm_dir_patcher.stop()
        cls.nrpe_support_patcher.stop()

    def setUp(self):
        """Set up harness for each test case."""
        self.harness = Harness(JujuBackupAllCharm)
        self.addCleanup(self.harness.cleanup)

    @patch_snap_installed()
    @mock.patch("exporter.logger")
    def test_00_on_check_snap_installed(self, mock_logger, mock_snap_cache_installed):
        """Test check_snap_installed function - installed case."""
        self.harness.begin()
        self.harness.charm.on.config_changed.emit()
        mock_logger.error.assert_not_called()

    @patch_snap_not_installed()
    @mock.patch("exporter.logger")
    def test_01_on_check_snap_not_installed(self, mock_logger, mock_snap_cache_not_installed):
        """Test check_snap_installed function - not installed case."""
        self.harness.begin()
        self.harness.charm.on.config_changed.emit()
        mock_logger.error.assert_called()

    @mock.patch.object(Exporter, "configure")
    @mock.patch.object(Exporter, "install_or_refresh")
    def test_10_on_initial_config_changed(
        self,
        mock_install_or_refresh,
        mock_configure,
    ):
        """Test on_config_changed calls the right methods."""
        self.harness.begin()
        self.harness.charm.on.config_changed.emit()
        mock_configure.assert_called_once()
        mock_install_or_refresh.assert_called_once()

    @patch_snap_path_exist()
    @patch_snap_not_installed()
    @mock.patch("charms.operator_libs_linux.v1.snap.install_local")
    def test_11_on_config_changed_install_local(
        self, mock_snap_install_local, mock_snap_not_installed, mock_snap_path_exist
    ):
        """Test snap.install_local is called when resources is provided."""
        rid = self.harness.add_relation(EXPORTER_RELATION_NAME, "prometheus-scrape")
        self.harness.begin()
        self.harness.add_relation_unit(rid, "prometheus-scrape/0")
        self.harness.charm.on.config_changed.emit()
        mock_snap_install_local.assert_called_once()

    @patch_snap_path_not_exist()
    @patch_snap_not_installed()
    @mock.patch("charms.operator_libs_linux.v1.snap.add")
    def test_12_on_config_changed_snap_add(
        self, mock_snap_add, mock_snap_not_installed, mock_snap_path_not_exist
    ):
        """Test snap.add is called when resources is not provided."""
        rid = self.harness.add_relation(EXPORTER_RELATION_NAME, "prometheus-scrape")
        self.harness.begin()
        self.harness.add_relation_unit(rid, "prometheus-scrape/0")
        self.harness.charm.on.config_changed.emit()
        mock_snap_add.assert_called_once()

    @patch_snap_path_exist()
    @patch_snap_installed()
    @mock.patch("exporter.logger")
    def test_13_on_config_changed_install_failed(
        self, mock_logger, mock_snap_installed, mock_snap_path_exist
    ):
        """Test install_or_refresh failed when resource is invaild."""
        rid = self.harness.add_relation(EXPORTER_RELATION_NAME, "prometheus-scrape")
        self.harness.begin()
        self.harness.add_relation_unit(rid, "prometheus-scrape/0")
        self.harness.charm.on.config_changed.emit()
        mock_logger.error.assert_called()

    @mock.patch.object(Exporter, "stop")
    @mock.patch.object(Exporter, "start")
    def test_20_on_relation_joined_and_departed(self, mock_start, mock_stop):
        """Test _on_relation_* calls the right methods."""
        rid = self.harness.add_relation(EXPORTER_RELATION_NAME, "prometheus-scrape")
        self.harness.begin()
        self.harness.add_relation_unit(rid, "prometheus-scrape/0")
        mock_start.assert_called_once()
        self.harness.remove_relation_unit(rid, "prometheus-scrape/0")
        mock_stop.assert_called_once()

    @patch_snap_installed()
    @mock.patch("exporter.logger")
    def test_30_health_check_on_update_status_healthy(self, mock_logger, mock_snap_installed):
        """Test check_health reporting healthy status."""
        rid = self.harness.add_relation(EXPORTER_RELATION_NAME, "prometheus-scrape")
        self.harness.begin()
        self.harness.add_relation_unit(rid, "prometheus-scrape/0")
        self.harness.charm.on.config_changed.emit()
        with mock.patch.object(JujuBackupAllCharm, "model") as mock_model:
            mock_model.get_relation.return_value = True
            self.harness.charm.on.update_status.emit()
            mock_logger.info.assert_any_call("Exporter health check - healthy.")

    @patch_snap_installed()
    @mock.patch("exporter.sleep")
    @mock.patch("exporter.logger")
    def test_31_health_check_on_update_status_unhealthy(
        self, mock_logger, mock_sleep, mock_snap_installed
    ):
        """Test check_health restarting unhealthy snap."""
        rid = self.harness.add_relation(EXPORTER_RELATION_NAME, "prometheus-scrape")
        self.harness.begin()
        self.harness.add_relation_unit(rid, "prometheus-scrape/0")
        self.harness.charm.on.config_changed.emit()
        with mock.patch.object(JujuBackupAllCharm, "model") as mock_model:
            mock_model.get_relation.return_value = True
            self.harness.charm.exporter._exporter.services[EXPORTER_NAME]["active"] = False
            self.harness.charm.on.update_status.emit()
            mock_logger.warning.assert_any_call("Exporter health check - unhealthy.")
            mock_sleep.assert_called()

    @patch_snap_installed()
    @mock.patch("exporter.sleep")
    @mock.patch("exporter.logger")
    def test_32_health_check_on_update_status_unknown(
        self, mock_logger, mock_sleep, mock_snap_installed
    ):
        """Test check_health crashes on unknown error."""
        rid = self.harness.add_relation(EXPORTER_RELATION_NAME, "prometheus-scrape")
        self.harness.begin()
        self.harness.add_relation_unit(rid, "prometheus-scrape/0")
        self.harness.charm.on.config_changed.emit()
        with mock.patch.object(JujuBackupAllCharm, "model") as mock_model:
            mock_model.get_relation.return_value = True
            self.harness.charm.exporter._exporter.services = {}
            self.harness.charm.on.update_status.emit()
            mock_logger.error.assert_called()
