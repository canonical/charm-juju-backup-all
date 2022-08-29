# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import pathlib
import unittest
from unittest import mock

import yaml
from jujubackupall.config import Config

from tests.fixtures import (
    ACCOUNTS_YAML,
    MOCK_CONFIG,
    RAW_PUBKEY,
    SSH_FINGERPRINT,
    MockController,
    MockModel,
)
from utils import JujuBackupAllHelper, SSHKeyHelper


logging.basicConfig(level=logging.DEBUG)

class TestJujuBackupAllHelper(unittest.TestCase):
    """Test JujuBackupAllHelper's methods."""

    @classmethod
    def setUpClass(cls):
        """Set up class fixture."""
        # patch relevant modules/methods
        nrpe_support_patcher = mock.patch("utils.NRPE")
        nrpe_support_patcher.start()
        cls.addClassCleanup(nrpe_support_patcher.stop)

        charm_dir_patcher = mock.patch("charmhelpers.core.hookenv.charm_dir")
        patch = charm_dir_patcher.start()
        cls.addClassCleanup(charm_dir_patcher.stop)
        patch.return_value = "/a/directory/"

    @mock.patch("utils.BackupProcessor.process_backups")
    @mock.patch("utils.JujuBackupAllHelper._update_dir_owner")
    @mock.patch("utils.JujuBackupAllHelper.push_ssh_keys")
    def test_perform_backup(self, process_backups, update_dir_owner, push_ssh_keys):
        """Test perform_backup calls the right methods."""
        model = mock.MagicMock()
        model.config = MOCK_CONFIG
        backup_helper = JujuBackupAllHelper(model)

        backup_helper.perform_backup()

        push_ssh_keys.assert_called_once()
        process_backups.assert_called_once()
        update_dir_owner.assert_called_once_with(MOCK_CONFIG["backup-dir"])

    @mock.patch("pathlib.Path.write_text")
    def test_update_crontab(self, cronjob_write_text):
        """Test update_crontab properly renders the cronjob."""
        import config

        model = mock.MagicMock()
        model.config = MOCK_CONFIG
        backup_helper = JujuBackupAllHelper(model)

        backup_helper.update_crontab()

        expected_cron_job = "PATH=/usr/bin:/bin:/snap/bin\n{} {} {} --debug --purge {} --task-timeout {} >> {} 2>&1\n".format(  # noqa E501
            MOCK_CONFIG["crontab"],
            config.BACKUP_USERNAME,
            config.Paths.AUTO_BACKUP_SCRIPT_PATH,
            MOCK_CONFIG["backup-retention-period"],
            MOCK_CONFIG["timeout"],
            config.Paths.AUTO_BACKUP_LOG_PATH,
        )
        cronjob_write_text.assert_called_once_with(expected_cron_job)


class TestSSHKeyHelper(unittest.TestCase):
    """Test SSHKeyHelper's methods."""

    @classmethod
    def setUpClass(cls):
        """Set up class fixture."""
        # patch relevant modules/methods
        cls.nrpe_support_patcher = mock.patch("utils.NRPE")
        cls.nrpe_support_patcher.start()
        cls.addClassCleanup(cls.nrpe_support_patcher.stop)

        cls.charm_dir_patcher = mock.patch("charmhelpers.core.hookenv.charm_dir")
        patch = cls.charm_dir_patcher.start()
        patch.return_value = str(pathlib.Path(__file__).parents[2].absolute())

    def setUp(self):
        """Set up tests."""
        self.model = mock.MagicMock()
        self.helper = SSHKeyHelper(
            Config(args=MOCK_CONFIG),
            yaml.safe_load(ACCOUNTS_YAML)["controllers"],
        )

    def test_gen_libjuju_ssh_key_fingerprint_invalid(self):
        """Test the ssh fingerprint generation."""
        with self.assertRaises(ValueError):
            self.helper._gen_libjuju_ssh_key_fingerprint(raw_pubkey="")

    def test_gen_libjuju_ssh_key_fingerprint_valid(self):
        """Test the ssh fingerprint generation."""
        self.assertEqual(
            self.helper._gen_libjuju_ssh_key_fingerprint(raw_pubkey=RAW_PUBKEY),
            SSH_FINGERPRINT,
        )

    @mock.patch("utils.run_async")
    def test_get_model_ssh_key_fingeprints(self, mock_run_async):
        """Test the getting the model ssh fingerprint."""
        result = "mock fingerprint"
        mock_run_async.return_value = {"results": [{"result": result}]}
        self.assertEqual(
            self.helper._get_model_ssh_key_fingeprints(self.model),
            result,
        )

    @mock.patch("utils.BackupProcessor")
    @mock.patch("utils.connect_model")
    @mock.patch("utils.connect_controller")
    @mock.patch("utils.Paths.SSH_PUBLIC_KEY")
    def test_push_ssh_keys_to_models(
        self,
        mock_pubkey_path,
        mock_connect_controller,
        mock_connect_model,
        mock_backup_processor,
    ):
        """Parameterized test for ssh key push."""
        new_key = RAW_PUBKEY.replace("jujubackup", "newuser")
        params = [
            (
                "test with existing key (should not add)",
                RAW_PUBKEY,
                lambda mock_obj: mock_obj.assert_not_called(),
            ),
            (
                "test with missing key (should add)",
                new_key,
                lambda mock_obj: mock_obj.assert_called_with("admin", new_key),
            ),
        ]

        test_controller_name = "test-controller"
        mock_backup_processor.return_value.controller_names = [test_controller_name]
        mock_connect_controller.return_value.__enter__.return_value = MockController
        mock_connect_model.return_value.__enter__.return_value = MockModel

        for msg, ssh_pubkey, add_ssh_keys_test in params:
            with self.subTest(msg):
                mock_pubkey_path.read_text.return_value = ssh_pubkey
                self.helper.push_ssh_keys_to_models()

                mock_pubkey_path.read_text.assert_called()
                mock_backup_processor.assert_called_once()
                mock_connect_controller.assert_called_once_with(test_controller_name)
                MockController.list_models.assert_called_once()
                mock_connect_model.assert_called_once_with(MockController, "test-model")
                MockModel.get_ssh_keys.assert_called_once()
                add_ssh_keys_test(MockModel.add_ssh_keys)

            # reset mocks at the end of each test iteration
            mock_connect_controller.reset_mock()
            mock_connect_model.reset_mock()
            mock_backup_processor.reset_mock()
            MockController.list_models.reset_mock()
            MockModel.get_ssh_keys.reset_mock()
            MockModel.add_ssh_keys.reset_mock()
