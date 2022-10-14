# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import pathlib
import tempfile
import unittest
from subprocess import CalledProcessError
from unittest import mock
from unittest.mock import Mock

from ops.testing import Harness

from charm import JujuBackupAllCharm
from config import BACKUP_USERNAME, Paths
from tests.fixtures import ACCOUNTS_YAML, CONTROLLERS_YAML


class TestCharm(unittest.TestCase):
    """Charm test class."""

    @classmethod
    def setUpClass(cls):
        """Set up class fixture."""
        # Setup a tmpdir
        cls.tmpdir = tempfile.TemporaryDirectory()

        # patch relevant modules/methods
        cls.nrpe_support_patcher = mock.patch("utils.NRPE")
        cls.nrpe_support_patcher.start()

        cls.charm_dir_patcher = mock.patch("charmhelpers.core.hookenv.charm_dir")
        patch = cls.charm_dir_patcher.start()
        patch.return_value = str(pathlib.Path(__file__).parents[2].absolute())

        # Create expected dirstructure in our tmpdir
        _reparent(Paths, cls.tmpdir.name)
        tmp = pathlib.Path(cls.tmpdir.name)
        (tmp / "var/lib").mkdir(parents=True)

    @classmethod
    def tearDownClass(cls):
        """Tear down class fixture."""
        _reparent(Paths, "/")
        mock.patch.stopall()
        cls.tmpdir.cleanup()

    def setUp(self):
        """Set up tests."""
        self.harness = Harness(JujuBackupAllCharm)
        self.addCleanup(self.harness.cleanup)

    def test_01_harness(self):
        """Verify harness."""
        self.harness.begin()
        self.assertFalse(self.harness.charm._stored.installed)

    @mock.patch("charmhelpers.core.host.chownr")
    @mock.patch("charmhelpers.core.host.user_exists")
    @mock.patch("charmhelpers.core.host.adduser")
    def test_05_install(
        self,
        mock_adduser,
        mock_user_exists,
        mock_chownr,
    ):
        """Test: charm install."""
        mock_user_exists.return_value = False
        self.harness.begin()
        self.harness.charm.on.install.emit()

        mock_user_exists.assert_called_once()
        mock_adduser.assert_called_once_with(
            BACKUP_USERNAME, home_dir=Paths.JUJUDATA_DIR
        )
        mock_chownr.assert_called_once()

        expected_dirs = [
            Paths.JUJUDATA_DIR,
            Paths.JUJUDATA_SSH_DIR,
            Paths.JUJUDATA_COOKIES_DIR,
        ]

        for path in expected_dirs:
            self.assertTrue(path.is_dir())

        expected_files = [
            Paths.SSH_PRIVATE_KEY,
            Paths.SSH_PUBLIC_KEY,
            Paths.AUTO_BACKUP_SCRIPT_PATH,
        ]

        for path in expected_files:
            self.assertTrue(path.is_file())

        self.assertNotIn("REPLACE_CHARMDIR", Paths.AUTO_BACKUP_SCRIPT_PATH.read_text())
        self.assertEqual(self.harness.charm.unit.status.name, "active")
        self.assertEqual(
            self.harness.charm.unit.status.message,
            "Install complete",
        )
        self.assertTrue(self.harness.charm._stored.installed)

    def test_09_config_changed_defer(self):
        """Test: config changed event is deferred if charm not installed."""
        self.harness.begin()
        self.harness.charm.on.config_changed.emit()
        self.assertEqual(self._get_notice_count("config_changed"), 1)

    def test_10_config_changed_blocked(self):
        """Test: config changed event is deferred if charm not installed."""
        self.harness.begin()
        self.harness.charm._stored.installed = True
        self.harness.charm.on.config_changed.emit()

        self.assertEqual(self.harness.charm.unit.status.name, "blocked")
        self.assertEqual(
            self.harness.charm.unit.status.message,
            "Waiting for controllers/accounts configuration",
        )

    def test_12_config_changed_invalid_config(self):
        """Test: config changed event with invalid config."""
        self.harness.begin()
        self.harness.charm._stored.installed = True
        self.harness.update_config({"controllers": "{}", "accounts": "{}"})
        self.harness.charm.on.config_changed.emit()

        self.assertEqual(self.harness.charm.unit.status.name, "blocked")
        self.assertEqual(
            self.harness.charm.unit.status.message,
            "Invalid controllers/accounts configuration",
        )

    @mock.patch("charmhelpers.core.host.chownr")
    def test_14_config_changed_valid_config(self, mock_chownr):
        """Test: config changed event with invalid config."""
        tmp_dir = pathlib.Path(self.tmpdir.name)
        backup_dir = tmp_dir / "backups"
        (tmp_dir / "etc/cron.d").mkdir(parents=True)
        Paths.JUJUDATA_DIR.mkdir(exist_ok=True)
        Paths.JUJUDATA_COOKIES_DIR.mkdir(exist_ok=True)

        self.harness.begin()
        self.harness.charm._stored.installed = True
        self.harness.update_config(
            {
                "controllers": CONTROLLERS_YAML,
                "accounts": ACCOUNTS_YAML,
                "backup-dir": str(backup_dir),
            }
        )
        self.harness.charm.on.config_changed.emit()

        mock_chownr.assert_called()
        self.assertTrue(backup_dir.is_dir())
        self.assertTrue(Paths.CONFIG_YAML.is_file())
        self.assertTrue((Paths.JUJUDATA_COOKIES_DIR / "test-controller.json").is_file())
        self.assertEqual(
            ACCOUNTS_YAML, (Paths.JUJUDATA_DIR / "accounts.yaml").read_text()
        )
        self.assertEqual(
            CONTROLLERS_YAML, (Paths.JUJUDATA_DIR / "controllers.yaml").read_text()
        )
        self.assertIn(
            self.harness.charm.config["crontab"],
            Paths.AUTO_BACKUP_CRONTAB_PATH.read_text(),
        )
        self.assertEqual(self.harness.charm.unit.status.name, "active")
        self.assertEqual(
            self.harness.charm.unit.status.message,
            "Unit is ready",
        )

    @mock.patch("utils.SSHKeyHelper.push_ssh_keys_to_models")
    @mock.patch("jujubackupall.process.BackupProcessor.process_backups")
    @mock.patch("charmhelpers.core.host.chownr")
    def test_20_do_backup_action_all_models(
        self, mock_chownr, mock_process_backups, mock_push_ssh_keys
    ):
        """Test the do_backup action."""
        self.harness.update_config(
            {
                "controllers": CONTROLLERS_YAML,
                "accounts": ACCOUNTS_YAML,
            }
        )

        action_event = mock.Mock(params={})
        action_event.params = {}
        mock_results = '{"mock_results": true}'
        mock_process_backups.return_value = mock_results

        self.harness.begin()
        self.harness.charm._on_do_backup_action(action_event)

        mock_process_backups.assert_called_once_with(omit_models=[])
        mock_push_ssh_keys.assert_called_once()
        action_event.set_results.assert_called_once_with({"result": mock_results})

    @mock.patch("utils.SSHKeyHelper.push_ssh_keys_to_models")
    @mock.patch("jujubackupall.process.BackupProcessor.process_backups")
    @mock.patch("charmhelpers.core.host.chownr")
    def test_20_do_backup_action_omit_single_model(
        self, mock_chownr, mock_process_backups, mock_push_ssh_keys
    ):
        """Test the do_backup action."""
        self.harness.update_config(
            {
                "controllers": CONTROLLERS_YAML,
                "accounts": ACCOUNTS_YAML,
            }
        )

        action_event = mock.Mock(params={})
        action_event.params = {"omit-models": "omit-me"}
        mock_results = '{"mock_results": true}'
        mock_process_backups.return_value = mock_results

        self.harness.begin()
        self.harness.charm._on_do_backup_action(action_event)

        mock_process_backups.assert_called_once_with(omit_models=["omit-me"])
        mock_push_ssh_keys.assert_called_once()
        action_event.set_results.assert_called_once_with({"result": mock_results})

    @mock.patch("utils.SSHKeyHelper.push_ssh_keys_to_models")
    @mock.patch("jujubackupall.process.BackupProcessor.process_backups")
    @mock.patch("charmhelpers.core.host.chownr")
    def test_20_do_backup_action_omit_models(
        self, mock_chownr, mock_process_backups, mock_push_ssh_keys
    ):
        """Test the do_backup action."""
        self.harness.update_config(
            {
                "controllers": CONTROLLERS_YAML,
                "accounts": ACCOUNTS_YAML,
            }
        )

        action_event = mock.Mock(params={})
        action_event.params = {"omit-models": "omit-me,and-me-too"}
        mock_results = '{"mock_results": true}'
        mock_process_backups.return_value = mock_results

        self.harness.begin()
        self.harness.charm._on_do_backup_action(action_event)

        mock_process_backups.assert_called_once_with(
            omit_models=["omit-me", "and-me-too"]
        )
        mock_push_ssh_keys.assert_called_once()
        action_event.set_results.assert_called_once_with({"result": mock_results})

    @mock.patch("utils.SSHKeyHelper")
    def test_22_push_ssh_keys_action(self, mock_ssh_keys_helper):
        """Test the do_backup action."""
        self.harness.update_config(
            {
                "controllers": CONTROLLERS_YAML,
                "accounts": ACCOUNTS_YAML,
            }
        )

        action_event = mock.Mock(params={})

        self.harness.begin()
        self.harness.charm._on_push_ssh_keys_action(action_event)

        mock_ssh_keys_helper.return_value.push_ssh_keys_to_models.assert_called_once()
        action_event.set_results.assert_called_once_with({"result": "success"})

    # @mock.patch("utils.rsync")
    @mock.patch("utils.NRPE")
    def test_30_nem_relation(self, mock_nrpe):
        """Test the nagios-external-master relation."""
        Paths.NAGIOS_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)

        relation_id = self.harness.add_relation("nrpe-external-master", "nrpe")
        self.harness.add_relation_unit(relation_id, "nrpe/0")
        self.harness.begin()
        self.harness.update_relation_data(
            relation_id, "nrpe/0", {"private-address": "1.2.3.4", "port": "5666"}
        )

        self.assertTrue(
            (Paths.NAGIOS_PLUGINS_DIR / "check_auto_backup_results.py").is_file()
        )
        kwargs = self.harness.charm.helper.nrpe.add_check.call_args[1]
        self.assertEqual(kwargs["shortname"], "juju_backup_all_results")
        self.harness.charm.helper.nrpe.write.assert_called_once()

    @mock.patch("utils.Paths")
    @mock.patch("subprocess.check_output")
    def test_80_init_jujudata_dir_exception(self, mock_check_output, mock_paths):
        """Test a failure of the ssh keygen."""
        mock_paths.SSH_PRIVATE_KEY.exists.return_value = False
        mock_exception = CalledProcessError(1, "cmd")
        mock_exception.output = Mock()
        mock_check_output.side_effect = mock_exception

        self.harness.begin()

        with self.assertRaises(CalledProcessError):
            self.harness.charm.helper.init_jujudata_dir()

    def _get_notice_count(self, hook):
        """Return the notice count for a given charm hook."""
        notice_count = 0
        handle = "JujuBackupAllCharm/on/{}".format(hook)
        for event_path, _, _ in self.harness.charm.framework._storage.notices(None):
            if event_path.startswith(handle):
                notice_count += 1
        return notice_count


def _reparent(pathsobj, name):
    """Patch an object with Path attrs to a new root (inplace)."""
    newroot = pathlib.Path(name)
    for k in dir(pathsobj):
        inst = getattr(pathsobj, k)
        if not isinstance(inst, pathlib.Path):
            continue
        newpath = newroot / inst.relative_to("/")
        setattr(pathsobj, k, newpath)
