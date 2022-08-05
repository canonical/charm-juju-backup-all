#!/usr/bin/env python3

"""
Run backups using jujubackup all and gather output for monitoring.

Copyright 2021 Canonical Ltd.

License: Apache License 2.0
 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at
 .
     http://www.apache.org/licenses/LICENSE-2.0
 .
 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
"""

import argparse
import json
import logging
import os
import pathlib
import subprocess
import sys
import traceback

# The path below is templated in during charm install this is required to load
# the charm code and dependencies from this script.
sys.path.append("REPLACE_CHARMDIR/src")
sys.path.append("REPLACE_CHARMDIR/venv")

import yaml
from jujubackupall import globals  # noqa E402
from jujubackupall.config import Config  # noqa E402
from jujubackupall.process import BackupProcessor  # noqa E402

from config import Paths  # noqa E402
from utils import SSHKeyHelper  # noqa E402

logger = logging.getLogger(__name__)


PID_FILENAME = pathlib.Path("/tmp/auto_backup.pid")
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


class AutoJujuBackupAll:
    """Make backups of all configured controllers/models."""

    def __init__(self):
        """Initialize the class and configure it for juju backups."""
        self.config = Config(args=yaml.safe_load(Paths.CONFIG_YAML.read_text()))

        # configure libjuju to the location of the credentials
        if "JUJUDATA_DIR" not in os.environ:
            os.environ["JUJU_DATA"] = str(Paths.JUJUDATA_DIR)

    def perform_backup(self):
        """Perform backups."""
        # first ensure the ssh key is in all models, then perform the backup
        accounts_yaml = (Paths.JUJUDATA_DIR / "accounts.yaml").read_text()
        accounts = yaml.safe_load(accounts_yaml)["controllers"]
        ssh_helper = SSHKeyHelper(self.config, accounts)
        ssh_helper.push_ssh_keys_to_models()

        backup_processor = BackupProcessor(self.config)
        backup_results = backup_processor.process_backups()
        logger.info("backup results = '{}'".format(backup_results))
        return backup_results

    def purge_old_backups(self, days_old):
        """Purge backup files older than `day_old`."""
        logger.info("purging backup files older than: '{}' days".format(days_old))
        cmd = [
            "find",
            self.config.output_dir,
            "-mtime",
            "+{}".format(days_old),
            "-type",
            "f",
            "-delete",
        ]

        try:
            subprocess.check_output(cmd)
        except subprocess.CalledProcessError as error:
            logger.error(error.output.decode("utf8"))
            raise

        logger.debug("completed purging old backup files")

    def run(self):
        """Call main function."""
        parser = argparse.ArgumentParser(
            description="Companion script to charm-juju-backup-all to collect backups."
        )

        parser.add_argument(
            "--debug",
            action="store_true",
            help="Enable debug logging",
        )

        parser.add_argument(
            "--purge",
            action="store",
            dest="purge_after_days",
            metavar="DAYS_OLD",
            type=int,
            help="Purge backups older than the specified number of days",
        )

        parser.add_argument(
            "--task-timeout",
            action="store",
            dest="task_timeout",
            metavar="SECONDS",
            default=600,
            type=int,
            help="Individual task timeout length",
        )

        args = parser.parse_args()

        log_level = logging.DEBUG if args.debug else logging.ERROR
        self.configure_logging(log_level=log_level)

        if args.task_timeout > 0:
            globals.async_timeout = args.task_timeout

        # Ensure a single instance via a simple pidfile
        pid = str(os.getpid())

        if PID_FILENAME.is_file():
            sys.exit("{} already exists, exiting".format(PID_FILENAME))

        PID_FILENAME.write_text(pid)

        try:
            backup_results = self.perform_backup()
            Paths.AUTO_BACKUP_RESULTS_PATH.write_text(backup_results)

            # purge old backups if requested
            if args.purge_after_days and args.purge_after_days > 0:
                self.purge_old_backups(args.purge_after_days)
        except Exception:
            backup_results = {"ERROR": traceback.format_exc()}
            logger.debug("writing error details to the results file")
            Paths.AUTO_BACKUP_RESULTS_PATH.write_text(json.dumps(backup_results))
            logger.error("backup failed! check log for details")
            raise
        finally:
            PID_FILENAME.unlink()

    def configure_logging(self, log_level):
        """Configure logging for the backup script."""
        logging.basicConfig(format=LOG_FORMAT, level=log_level)
        logging.getLogger("websockets").setLevel(logging.ERROR)
        logging.getLogger("juju").setLevel(logging.ERROR)
        logging.getLogger("connector").setLevel(logging.CRITICAL)
        logging.getLogger("asyncio").setLevel(logging.CRITICAL)


if __name__ == "__main__":
    auto_backup = AutoJujuBackupAll()
    auto_backup.run()
