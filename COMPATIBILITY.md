# Juju Version Compatibility Guide

## Compatibility Table

| Juju Version | Status | Compatible Charm Revisions |
|--------------|--------|----------------------------|
| Juju 2.9.x     | End of Support | Up to [revision 46](https://github.com/canonical/charm-juju-backup-all/releases?q=latest%2Fedge%2F2.9&expanded=true) |
| Juju 3.x.x     | Actively Supported | Current and future releases |

## Juju 2.9 Legacy Support

> **Note:** Juju 2.9.x is no longer actively supported. Upgrading to Juju 3.x.x is recommended.

The last charm revision compatible with Juju 2.9 is [revision 46](https://github.com/canonical/charm-juju-backup-all/releases?q=latest%2Fedge%2F2.9&expanded=true) in the 'latest/edge/2.9' channel.

### Deploying with Juju 2.9.x

To deploy on Juju 2.9.x environments:

```sh
juju deploy juju-backup-all --channel=latest/edge/2.9
```

### Rolling Back to Juju 2.9.x Compatible Version

If you need to downgrade to a Juju 2.9.x compatible version:

```sh
juju refresh juju-backup-all --channel=latest/edge/2.9
```

## Juju 3.x.x Support

For Juju 3.x.x environments, use the standard deployment:

```sh
juju deploy juju-backup-all
```
