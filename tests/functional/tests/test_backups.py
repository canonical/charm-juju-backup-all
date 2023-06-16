# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
import json
import logging
import unittest

import zaza.model as model

from config import BACKUP_USERNAME, Paths


class JujuBackupAllTests(unittest.TestCase):
    """Test juju-backup-all related functionality."""

    @classmethod
    def setUpClass(cls):
        """Run setup for tests."""
        cls.app_name = "juju-backup-all"
        cls.unit_name = "juju-backup-all/0"

    def test_01_install(self):
        """Test: Installation completed successfully."""
        # check that the jujubackup user exists
        results = model.run_on_leader(self.app_name, "id {}".format(BACKUP_USERNAME))
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

    def test_02_do_backup_action(self):
        """Test: do-backup action performed successfully."""
        action = model.run_action_on_leader(self.app_name, "do-backup")
        assert action.data["results"].get("Code") == "0", "Backup action failed."

        result_str = action.data["results"].get("result")
        assert result_str is not None, "Action returned None!"
        logging.info(f"Result of action: {result_str}")
        # convert str to dict
        result = json.loads(result_str)

        controller_bkp_path = result["controller_backups"][0].get("download_path")
        config_bkp_path = result["config_backups"][0].get("download_path")
        app_backups_list = result["app_backups"]
        app_names = set()
        expected_app_names = {"postgresql", "mysql", "percona-cluster", "etcd"}
        db_bkp_paths = []

        # each app is a dictionary in the list
        for app_dict in app_backups_list:
            app_names.add(app_dict["app"])
            db_bkp_paths.append(app_dict["download_path"])

        assert app_names == expected_app_names

        expected_bkp_files = [controller_bkp_path, config_bkp_path]
        expected_bkp_files.extend(db_bkp_paths)
        for path in expected_bkp_files:
            self.assert_file_stat(path, "regular file")

        model.block_until_unit_wl_status(self.unit_name, "active")

    def assert_file_stat(self, filename, filetype):
        """Check that a file exists and has the expected type."""
        cmd = "stat -c %F {}".format(filename)
        results = model.run_on_leader(self.app_name, cmd)
        actual_filetype = results["Stdout"].strip()
        assert results["Code"] == "0", "'{}' does not exist".format(filename)
        assert actual_filetype == filetype, "'{}' has the wrong type: '{}'".format(
            filename, actual_filetype
        )
