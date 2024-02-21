# charm-juju-backup-all

## Description

Juju Backup All - a charm to perform Juju and database backups

This charm deploys [juju-backup-all](https://launchpad.net/juju-backup-all),
along with a script to automate its execution, `auto_backup.py`. In addition,
a crontab is deployed to run `auto_backup.py`, as well as a Nagios check which
alerts on any issues when performing backups.

## Usage

Deploy this charm with:
```sh
juju deploy juju-backup-all
```

The following Juju connection config values **must** be set to allow libjuju to
connect controllers and models to perform the backups. It is **HIGHLY**
recommended that a new user be created for backups (will need `admin`
permissions so it can run actions as well as ssh to the units to gather the
backup files) on all the model defined:

* controllers - YAML defining the configuration for controllers, same syntax as
  used by the juju client configuration (usually located at
  `~/.local/share/juju/controllers.yaml`)
* accounts - YAML defining the configuration for juju accounts, same syntax as
  used by the juju client configuration (usually located at
  ~/.local/share/juju/accounts.yaml)

The following options are available:

* backup-dir - The directory to be used for the backups. Will be created if it
  does not exist.
* backup-retention-period - Retention period for backups in days. Backup files
  older than this will be purged during the next backup run.
* controller-names - A comma delimited list of controller names to be backed
  up. An empty list means that all configured controllers will backed up.
* crontab - Specifies when to run the backups. Uses standard crontab syntax.
* exclude-charms - A comma delimited list of charms to be excluded. An empty
  list means that all charms supported by juju-backup-all will be backed up.
  Note that this setting uses the name of the *charm* and not the name of the
  application.
* exclude-controller-backup - Whether to backup the controller as part of the
  backup operations
* exclude-juju-client-config-backup - Whether to backup the juju client data
  (accounts, controllers, ssh keys) as part of the backup operations.
* timeout - Timeout in seconds for long running commands. This setting is used
  for each task and not for the whole backup operation.

## Relations

`charm-juju-backup-all` supports the `nagios-external-master` relation and
provides a NRPE check to ensure that backups are working properly.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
`CONTRIBUTING.md` for developer guidance.

## juju-backup-all (upstream)

* Website: https://launchpad.net/juju-backup-all
* Bug tracker: https://bugs.launchpad.net/juju-backup-all
