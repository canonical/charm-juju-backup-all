# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""Functional tests for the juju-backup-all charm.

This is a jubilant port of the zaza suite (tests.yaml, bundles/ and configure.py) and runs the
same steps in the same order. For each Ubuntu release in BUNDLES, in a model of its own:

1. test_deploy: deploy the bundle and wait for zaza's target_deploy_status.
2. test_configure: give the charm the credentials of the controller the tests run on.
3. test_01_install and test_02_do_backup_action: the checks themselves.

The charm under test comes from CHARM_PATH_<RELEASE>, for example CHARM_PATH_NOBLE. CI already
sets these variables for the zaza overlays.
"""

import json
import logging
import os
import re
from pathlib import Path

import jubilant
import pytest
from pytest_jubilant import JujuFactory

from config import BACKUP_USERNAME, Paths

APP_NAME = "juju-backup-all"
UNIT_NAME = "juju-backup-all/0"
LEADER = f"{APP_NAME}/leader"

# zaza's default timeouts: 60 minutes for the deploy, 45 minutes for every other wait.
DEPLOY_TIMEOUT = 60 * 60
WAIT_TIMEOUT = 45 * 60

# The old bundles/<release>.yaml. A channel of None means the bundle didn't set one.
BUNDLES = {
    "noble": {
        "base": "ubuntu@24.04",
        "postgresql-channel": "16/stable",
        "mysql-channel": "latest/edge",
    },
    "jammy": {
        "base": "ubuntu@22.04",
        "postgresql-channel": None,
        "mysql-channel": None,
    },
    "focal": {
        "base": "ubuntu@20.04",
        "postgresql-channel": "latest/stable",
        "mysql-channel": None,
    },
}

# The model config zaza applies to every test model. zaza's "default-series" is left out
# because every app here sets its own base.
MODEL_CONFIG = {
    "image-stream": "daily",
    "test-mode": True,
    "transmit-vendor-metrics": False,
    "enable-os-upgrade": False,
    "automatically-retry-hooks": False,
    "use-default-secgroup": True,
}

# The old target_deploy_status from tests.yaml. As in zaza, "active" is always allowed, and a
# listed status or message prefix adds to the defaults below. A regex replaces the prefixes.
# Apps that aren't listed (mysql) use the defaults alone.
TARGET_DEPLOY_STATUS = {
    APP_NAME: {
        "workload-status": "blocked",
        "workload-status-message-prefix": "Waiting for controllers/accounts configuration",
    },
    "postgresql": {
        "workload-status": "active",
        "workload-status-message-regex": "^Primary.*|^Live master.*",
    },
    "etcd": {
        "workload-status": "active",
        "workload-status-message-prefix": "Healthy with 1 known peer",
    },
    "easyrsa": {
        "workload-status": "active",
        "workload-status-message-prefix": "Certificate Authority connected.",
    },
}
APPROVED_STATUSES = ["active"]
APPROVED_MESSAGE_PREFIXES = ["ready", "Ready", "Unit is ready"]


@pytest.fixture(scope="module", params=list(BUNDLES))
def series(request: pytest.FixtureRequest) -> str:
    """Ubuntu release under test. pytest runs every test for one release before the next."""
    return request.param


@pytest.fixture(scope="module")
def juju(juju_factory: JujuFactory, series: str) -> jubilant.Juju:
    """Model for the current release, replacing pytest-jubilant's single-model ``juju``."""
    return juju_factory.get_juju(series)


def charm_path(series: str) -> Path:
    """Return the path of the charm built for ``series``, read from CHARM_PATH_<RELEASE>."""
    env_var = f"CHARM_PATH_{series.upper()}"
    path = os.environ.get(env_var)
    if not path:
        pytest.fail(f"{env_var} must be set to the path of the charm built for {series}")
    return Path(path)


def any_unit_error(status: jubilant.Status) -> bool:
    """Report whether any unit's workload is in error."""
    return any(
        unit.workload_status.current == "error"
        for app in status.apps
        for unit in status.get_units(app).values()
    )


def any_hard_error(status: jubilant.Status) -> bool:
    """Port of zaza's check_model_for_hard_errors."""
    return any_unit_error(status) or any(
        machine.machine_status.current == "provisioning error"
        for machine in status.machines.values()
    )


def app_reached_target(status: jubilant.Status, app: str) -> bool:
    """Report whether every unit of ``app`` is idle and matches its target deploy status."""
    target = TARGET_DEPLOY_STATUS.get(app, {})
    statuses = list(APPROVED_STATUSES)
    if "workload-status" in target:
        statuses.append(target["workload-status"])
    prefixes = list(APPROVED_MESSAGE_PREFIXES)
    if "workload-status-message-prefix" in target:
        prefixes.append(target["workload-status-message-prefix"])
    regex = target.get("workload-status-message-regex")

    units = status.apps[app].units
    if not units:
        return False
    for unit in units.values():
        message = unit.workload_status.message
        if regex is not None:
            message_ok = re.search(regex, message) is not None
        else:
            message_ok = message.startswith(tuple(prefixes))
        if not (
            unit.juju_status.current == "idle"
            and unit.workload_status.current in statuses
            and message_ok
        ):
            return False
    return True


def wait_for_target_deploy_status(juju: jubilant.Juju):
    """Port of zaza's wait_for_application_states.

    As in zaza, an app that reaches its target once is done, even if its status changes later.
    """
    pending = set(juju.status().apps)

    def ready(status: jubilant.Status) -> bool:
        pending.difference_update([app for app in pending if app_reached_target(status, app)])
        return not pending

    juju.wait(ready, error=any_hard_error, timeout=DEPLOY_TIMEOUT)


def wait_for_unit_workload_status(juju: jubilant.Juju, unit: str, workload_status: str):
    """Port of zaza's block_until_unit_wl_status."""
    app = unit.split("/")[0]
    juju.wait(
        lambda status: status.apps[app].units[unit].workload_status.current == workload_status,
        timeout=WAIT_TIMEOUT,
    )


def run_on_leader(juju: jubilant.Juju, command: str) -> jubilant.Task:
    """Run ``command`` on the leader unit, and return the task even if the command fails.

    zaza returned failed commands instead of raising, so the tests check the return code.
    """
    try:
        return juju.exec(command, unit=LEADER)
    except jubilant.TaskError as e:
        return e.task


def run_action_on_leader(juju: jubilant.Juju, action: str) -> jubilant.Task:
    """Run ``action`` on the leader unit, and return the task even if the action fails.

    zaza waited as long as the action took. Juju's own default wait is too short for a full
    backup, so wait up to WAIT_TIMEOUT.
    """
    try:
        return juju.run(LEADER, action, wait=WAIT_TIMEOUT)
    except jubilant.TaskError as e:
        return e.task


def assert_file_stat(juju: jubilant.Juju, filename, filetype: str):
    """Check that a file exists and has the expected type."""
    # `stat -c %F` prints the file type ("directory", "regular file", ...)
    # and exits non-zero if the path doesn't exist. It runs on the unit, not locally.
    task = run_on_leader(juju, "stat -c %F {}".format(filename))
    actual_filetype = task.stdout.strip()
    assert task.return_code == 0, "'{}' does not exist".format(filename)
    assert actual_filetype == filetype, "'{}' has the wrong type: '{}'".format(
        filename, actual_filetype
    )


def get_controller_name(controllers_file_contents: str) -> str:
    """Obtain the current controller name from controllers.yaml."""
    controller_name = re.findall(r"current-controller:\s(.*)$", controllers_file_contents)[0]
    logging.info(f"Current controller name: {controller_name}")
    return controller_name


@pytest.mark.juju_setup
def test_deploy(juju: jubilant.Juju, series: str):
    """Deploy the bundle for this release and wait for the target deploy status."""
    bundle = BUNDLES[series]
    base = bundle["base"]

    juju.model_config(MODEL_CONFIG)

    # postgresql, mysql and etcd are the backup targets. easyrsa is only there to issue
    # etcd's TLS certificates.
    juju.deploy(charm_path(series), APP_NAME, base=base)
    juju.deploy("postgresql", base=base, channel=bundle["postgresql-channel"])
    juju.deploy(
        "mysql-innodb-cluster",
        "mysql",
        base=base,
        channel=bundle["mysql-channel"],
        num_units=3,
    )
    juju.deploy("etcd", base=base)
    juju.deploy("easyrsa", base=base)
    juju.integrate("etcd:certificates", "easyrsa:client")

    # juju-backup-all is expected to be blocked here, because it has no credentials yet.
    wait_for_target_deploy_status(juju)


@pytest.mark.juju_setup
def test_configure(juju: jubilant.Juju):
    """Configure juju-backup-all with the credentials of the controller the tests run on.

    juju-backup-all stays blocked until it has credentials for a controller. Pass it the test
    runner's own juju client files, the way libjuju's juju_config_dir() finds them.
    """
    juju_data = Path(os.environ.get("JUJU_DATA", "~/.local/share/juju")).expanduser()
    controllers_file_contents = (juju_data / "controllers.yaml").read_text()
    accounts_file_contents = (juju_data / "accounts.yaml").read_text()
    logging.info(f"Controllers file contents:\n{controllers_file_contents}")
    logging.info(f"Accounts file contents:\n{accounts_file_contents}")
    controller_name = get_controller_name(controllers_file_contents)

    # Limit backups to the current controller, in case the runner knows about other
    # controllers too.
    juju.config(
        APP_NAME,
        {
            "accounts": accounts_file_contents,
            "controllers": controllers_file_contents,
            "controller-names": controller_name,
        },
    )

    # With valid credentials the charm should go from "blocked" to "active".
    wait_for_unit_workload_status(juju, UNIT_NAME, "active")
    juju.wait(jubilant.all_agents_idle, error=any_unit_error, timeout=WAIT_TIMEOUT)


def test_01_install(juju: jubilant.Juju):
    """Test: Installation completed successfully."""
    # The install hook creates a dedicated system user that owns the juju
    # client data and the backups. `id <user>` exits non-zero if it is missing.
    task = run_on_leader(juju, "id {}".format(BACKUP_USERNAME))
    assert task.return_code == 0, "user '{}' does not exist".format(BACKUP_USERNAME)

    # The install hook also lays out the juju client data dir
    # (/var/lib/jujubackupall) with ssh/ and cookies/ subdirectories...
    expected_dirs = [
        Paths.JUJUDATA_DIR,
        Paths.JUJUDATA_SSH_DIR,
        Paths.JUJUDATA_COOKIES_DIR,
    ]

    for path in expected_dirs:
        assert_file_stat(juju, path, "directory")

    # ...plus the SSH keypair the charm uses to reach units in other
    # models, and the auto_backup.py script that the cron job runs.
    expected_files = [
        Paths.SSH_PRIVATE_KEY,
        Paths.SSH_PUBLIC_KEY,
        Paths.AUTO_BACKUP_SCRIPT_PATH,
    ]

    for path in expected_files:
        assert_file_stat(juju, path, "regular file")


def test_02_do_backup_action(juju: jubilant.Juju):
    """Test: do-backup action performed successfully."""
    # Run a full backup on demand, the same thing the cron job does on schedule.
    task = run_action_on_leader(juju, "do-backup")
    assert task.return_code == 0, "Backup action failed."

    # The action puts a JSON summary of everything it backed up into the
    # "result" key, e.g.:
    #   {"controller_backups": [{"download_path": ...}],
    #    "config_backups":     [{"download_path": ...}],
    #    "app_backups":        [{"app": "mysql", "download_path": ...}, ...]}
    result_str = task.results.get("result")
    assert result_str is not None, "Action returned None!"
    logging.info(f"Result of action: {result_str}")
    result = json.loads(result_str)

    # Gather the reported backup paths: the controller backup, the local
    # juju client config backup, and one database backup per supported app.
    controller_bkp_path = result["controller_backups"][0].get("download_path")
    config_bkp_path = result["config_backups"][0].get("download_path")
    app_backups_list = result["app_backups"]
    app_names = set()
    expected_app_names = {"postgresql", "mysql", "etcd"}
    db_bkp_paths = []

    # each app is a dictionary in the list
    for app_dict in app_backups_list:
        app_names.add(app_dict["app"])
        db_bkp_paths.append(app_dict["download_path"])

    # Every supported app in the bundle must have been backed up.
    # easyrsa is in the bundle too, but it is not a backup target.
    assert app_names == expected_app_names

    # Backups are downloaded onto the juju-backup-all unit, so check that
    # every reported path is a real file there.
    expected_bkp_files = [controller_bkp_path, config_bkp_path]
    expected_bkp_files.extend(db_bkp_paths)
    for path in expected_bkp_files:
        assert_file_stat(juju, path, "regular file")

    # The charm must go back to active once the backup is done.
    wait_for_unit_workload_status(juju, UNIT_NAME, "active")
