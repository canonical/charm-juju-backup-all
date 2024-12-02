#!/usr/bin/env python3

"""
Nagios plugin script to check charm-juju-backup-all results.

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
import os
import pathlib
import sys
from os.path import isfile
from time import time
from typing import Any

JUJUDATA_DIR = pathlib.Path("/var/lib/jujubackupall")
AUTO_BACKUP_RESULTS_PATH = JUJUDATA_DIR / "auto_backup_results.json"

NAGIOS_STATUS_OK = 0
NAGIOS_STATUS_WARNING = 1
NAGIOS_STATUS_CRITICAL = 2
NAGIOS_STATUS_UNKNOWN = 3

NAGIOS_STATUS = {
    NAGIOS_STATUS_OK: "OK",
    NAGIOS_STATUS_WARNING: "WARNING",
    NAGIOS_STATUS_CRITICAL: "CRITICAL",
    NAGIOS_STATUS_UNKNOWN: "UNKNOWN",
}


def nagios_exit(status, message):
    """Test if nagios exists."""
    assert status in NAGIOS_STATUS, "Invalid Nagios status code"
    output = "{}: {}".format(NAGIOS_STATUS[status], message)
    print(output)  # nagios requires print to stdout, no stderr
    sys.exit(status)


def validate_backup_results_file(backup_results_file, max_age):
    """Validate the backup results file."""
    # raise CRITICAL alert if the backup results file is missing
    # which implies cron job is not longer working
    if not backup_results_file.is_file():
        message = "backup results file not found: {}".format(backup_results_file)
        nagios_exit(NAGIOS_STATUS_CRITICAL, message)

    if max_age > 0:
        stat = os.stat(backup_results_file)
        age_sec = int(time() - stat.st_mtime)
        max_age_sec = max_age * 3600
        if age_sec > max_age_sec:
            message = "backup results file {} is older than max age {} hours".format(
                backup_results_file, max_age
            )
            nagios_exit(NAGIOS_STATUS_CRITICAL, message)


def main():
    """Call main function."""
    parser = argparse.ArgumentParser(
        description="check auto_backup scrpt results",
        # show default in help
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "-f",
        "--backup-results-file",
        dest="backup_results_file",
        default=AUTO_BACKUP_RESULTS_PATH,
        help="backups results file to check",
    )

    parser.add_argument(
        "-a",
        "--backup-results-max-age",
        dest="backup_results_max_age",
        type=int,
        default=25,
        help="backup results file max age in hours, 0 to ignore",
    )

    args = parser.parse_args()
    backup_results_file = pathlib.Path(args.backup_results_file)
    max_age = args.backup_results_max_age

    validate_backup_results_file(backup_results_file, max_age)

    raw_backup_results = backup_results_file.read_text()

    # check that the backups are valid:
    # * the results must be a valid json object with the expected structure
    # * each backup entry must have a 'download_path'
    # * the download_path musth be valid
    try:
        backup_results = json.loads(raw_backup_results)
        # "ERROR" will contain a traceback if something crashed during the backups.
        # See scripts/templates/auto_backup.py::AutoJujuBackupAll.run()
        if "ERROR" in backup_results:
            msg = "Detected error when performing backup: '{}'".format(
                backup_results["ERROR"]
            )
            nagios_exit(NAGIOS_STATUS_CRITICAL, msg)

        # This entry is populated by the jujubackupall backup process,
        # and indicates which backups failed. Some backups may have succeeded.
        if "errors" in backup_results:
            errors: list[dict[str, Any]] = backup_results["errors"]
            msg = "Detected error when performing backup: '{}'".format(errors)
            nagios_exit(NAGIOS_STATUS_CRITICAL, msg)

        for backup_type, backup_entries in backup_results.items():
            if not backup_type.endswith("_backups"):
                continue

            for backup_entry in backup_entries:
                if "download_path" not in backup_entry:
                    msg = "Missing backup download_path for: {}, details: {}".format(
                        backup_type, backup_entry
                    )
                    nagios_exit(NAGIOS_STATUS_CRITICAL, msg)
                elif not isfile(backup_entry["download_path"]):
                    msg = "Backup file is missing for: {}, details: {}".format(
                        backup_type, backup_entry
                    )
                    nagios_exit(NAGIOS_STATUS_CRITICAL, msg)

    except Exception:
        nagios_exit(
            NAGIOS_STATUS_CRITICAL,
            "Invalid backup results file: {}".format(backup_results_file),
        )

    # if we haven't found any issues, return OK
    nagios_exit(NAGIOS_STATUS_OK, "backups are OK")


if __name__ == "__main__":
    main()
