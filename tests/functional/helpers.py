# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""The deployments under test, and helpers shared by conftest.py and the tests."""

import json

import jubilant
import pytest

# For each step of destroying a model.
DESTROY_TIMEOUT = 10 * 60

# postgresql is only in focal's bundle. Its latest/stable charm has no create-backup action, so
# juju-backup-all backs it up with pg_dumpall. The newer postgresql charms back up to S3
# instead, so they are in S3_BACKUP_TARGETS.
BUNDLES = {
    "noble": {
        "base": "ubuntu@24.04",
        "mysql-innodb-cluster-channel": "8.0/stable",
    },
    "jammy": {
        "base": "ubuntu@22.04",
        "mysql-innodb-cluster-channel": "8.0/stable",
    },
    "focal": {
        "base": "ubuntu@20.04",
        "postgresql-channel": "latest/stable",
        "mysql-innodb-cluster-channel": "8.0/stable",
    },
}

# The machine charms that juju-backup-all backs up through their S3 create-backup action, for
# each release that tests them. Each app uses the release's base where its channel has one;
# most channels only publish a single base.
S3_BACKUP_TARGETS = {
    "noble": {
        "postgresql": {"channel": "16/stable", "base": "ubuntu@24.04"},
        "mysql": {"channel": "8.0/stable", "base": "ubuntu@22.04"},
        "mongodb": {"channel": "8/stable", "base": "ubuntu@24.04"},
        "zookeeper": {"channel": "3/stable", "base": "ubuntu@22.04"},
    },
    "jammy": {
        "postgresql": {"channel": "14/stable", "base": "ubuntu@22.04"},
        "mysql": {"channel": "8.0/stable", "base": "ubuntu@22.04"},
        "mongodb": {"channel": "6/stable", "base": "ubuntu@22.04"},
        "zookeeper": {"channel": "3/stable", "base": "ubuntu@22.04"},
    },
}

K8S_CLOUD = "juju-backup-all-charm-k8s"

BUNDLE = "bundle"
K8S_BUNDLE = "k8s-bundle"
DEPLOYMENTS = [f"{series}-{BUNDLE}" for series in BUNDLES] + [
    f"{series}-{K8S_BUNDLE}" for series in S3_BACKUP_TARGETS
]


def controller_name(request: pytest.FixtureRequest) -> str:
    """Return --juju-controller if given, else the current controller."""
    controllers = json.loads(
        jubilant.Juju().cli("controllers", "--format", "json", include_model=False)
    )
    return request.config.getoption("--juju-controller") or controllers["current-controller"]
