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

To run functional tests:

```bash
make functional
```
