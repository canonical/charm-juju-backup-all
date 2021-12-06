# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
import unittest

import zaza.model

from config import BACKUP_USERNAME, Paths
from tests.fixtures import ACCOUNTS_YAML, CONTROLLERS_YAML


class JujuBackupAllTests(unittest.TestCase):
    """Test juju-backup-all related functionality."""

    @classmethod
    def setUpClass(cls):
        """Run setup for tests."""
        cls.model_name = zaza.model.get_juju_model()
        cls.app_name = "juju-backup-all"
        cls.unit_name = "juju-backup-all/0"

    def test_01_install(self):
        """Test: Installation completed successfully."""
        # check that the jujubackup user exists
        results = zaza.model.run_on_leader(
            self.app_name, "id {}".format(BACKUP_USERNAME)
        )
        assert results["Code"] == "0", "user '{}' does not exist".format(
            BACKUP_USERNAME
        )

        # check that the expected files/directories are in place
        expected_dirs = [
            Paths.JUJUDATA_DIR,
            Paths.JUJUDATA_SSH_DIR,
            Paths.JUJUDATA_COOKIES_DIR,
        ]

        for path in expected_dirs:
            self.assert_file_stat(path, "directory")

        expected_files = [
            Paths.SSH_PRIVATE_KEY,
            Paths.SSH_PUBLIC_KEY,
            Paths.AUTO_BACKUP_SCRIPT_PATH,
        ]

        for path in expected_files:
            self.assert_file_stat(path, "regular file")

    def test_02_configuration(self):
        """Test: Charm is fully configured."""
        zaza.model.set_application_config(
            self.app_name,
            {
                "accounts": ACCOUNTS_YAML,
                "controllers": CONTROLLERS_YAML,
            },
        )

        zaza.model.block_until_unit_wl_status(self.unit_name, "active")
        zaza.model.block_until_all_units_idle()

    def assert_file_stat(self, filename, filetype):
        """Check that a file exists and has the expected type."""
        cmd = "stat -c %F {}".format(filename)
        results = zaza.model.run_on_leader(self.app_name, cmd)
        actual_filetype = results["Stdout"].strip()
        assert results["Code"] == "0", "'{}' does not exist".format(filename)
        assert actual_filetype == filetype, "'{}' has the wrong type: '{}'".format(
            filename, actual_filetype
        )
