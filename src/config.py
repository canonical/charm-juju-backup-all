# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
import pathlib

BACKUP_USERNAME = "jujubackup"


class Paths:
    """Namespace for path constants."""

    JUJUDATA_DIR = pathlib.Path("/var/lib/jujubackupall")
    JUJUDATA_SSH_DIR = JUJUDATA_DIR / "ssh"
    CONFIG_YAML = JUJUDATA_DIR / "config.yaml"
    JUJUDATA_COOKIES_DIR = JUJUDATA_DIR / "cookies"
    SSH_PRIVATE_KEY = JUJUDATA_SSH_DIR / "juju_id_rsa"
    SSH_PUBLIC_KEY = SSH_PRIVATE_KEY.with_suffix(".pub")
    AUTO_BACKUP_SCRIPT_PATH = JUJUDATA_DIR / "auto_backup.py"
    AUTO_BACKUP_LOG_PATH = JUJUDATA_DIR / "auto_backup.log"
    AUTO_BACKUP_RESULTS_PATH = JUJUDATA_DIR / "auto_backup_results.json"
    AUTO_BACKUP_CRONTAB_PATH = pathlib.Path("/etc/cron.d/juju-backup-all")
    NAGIOS_PLUGINS_DIR = pathlib.Path("/usr/local/lib/nagios/plugins/")
