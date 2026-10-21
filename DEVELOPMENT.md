# charm-juju-backup-all

## Developing

Create and activate a virtualenv with the development requirements:

    make dev-environment

## Code overview

This charm leverages [juju-backup-all](https://launchpad.net/juju-backup-all) to
automatically discover models and perform backups. The tool in turn depends on
[libjuju](https://github.com/juju/python-libjuju)

## Testing

### Unit tests

To run unit tests:

```sh
make unittests
```

### Functional tests

The functional tests use [jubilant](https://github.com/canonical/jubilant). They deploy the
charm on noble, jammy and focal, each in its own model on the current Juju controller. Build
the charm, point the tests at each built charm file, then run them:

```bash
charmcraft pack
export CHARM_PATH_NOBLE=$PWD/$(ls | grep '.*24.04.*\.charm$')
export CHARM_PATH_JAMMY=$PWD/$(ls | grep '.*22.04.*\.charm$')
export CHARM_PATH_FOCAL=$PWD/$(ls | grep '.*20.04.*\.charm$')
tox -e func
```

To test only one release, pass `-- -k noble`. Testing only focal skips the k8s cloud. To keep
the models afterwards for debugging, pass `-- --no-juju-teardown`.
