# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""The deployments under test, what each one deploys, and helpers shared with conftest.py.

Every backup target that the tests deploy is listed here, with the arguments its
``juju deploy`` gets. The tests also deploy infrastructure for the S3 targets: the k8s charm
that hosts the k8s cloud, two MinIOs, and an s3-integrator for each S3 target. That is in
test_backups.py, next to the code that sets it up.
"""

import json

import jubilant
import pytest

# For each step of destroying a model.
DESTROY_TIMEOUT = 10 * 60

# Two units per S3 backup target, so that each has a replica: juju-backup-all tries postgresql's
# replicas before its primary. Three units per target didn't fit on the CI runner next to the k8s
# cloud.
NUM_UNITS = 2
# Except mysql: its create-backup refuses unless the cluster can tolerate losing a member
# ("OK" or "OK_PARTIAL"), and a cluster of fewer than three is "OK_NO_TOLERANCE".
# juju-backup-all backs it up on a non-primary unit.
MYSQL_NUM_UNITS = 3
# mysql and postgresql default to the "production" profile, which sizes their memory from what
# the unit can see: the whole CI runner for an LXD container, or the whole k8s node for a pod.
# Several units at once ran the runner and the k8s node out of memory, so use the "testing"
# profile, which keeps them small.
TESTING_PROFILE = {"profile": "testing"}

# The bundle ported from the old zaza suite, for each release: app name -> its charm and the
# arguments for deploying it, in deploy order, on the release's base. mysql and etcd are the
# backup targets, and so is postgresql on focal. easyrsa is only there to issue etcd's TLS
# certificates.
#
# postgresql is only in focal's bundle. Its latest/stable charm has no create-backup action, so
# juju-backup-all backs it up with pg_dumpall. The newer postgresql charms back up to S3
# instead, so they are in S3_BACKUP_TARGETS.
BUNDLES = {
    "noble": {
        "base": "ubuntu@24.04",
        "apps": {
            "mysql": {"charm": "mysql-innodb-cluster", "channel": "8.0/stable", "num_units": 3},
            "etcd": {"charm": "etcd"},
            "easyrsa": {"charm": "easyrsa"},
        },
    },
    "jammy": {
        "base": "ubuntu@22.04",
        "apps": {
            "mysql": {"charm": "mysql-innodb-cluster", "channel": "8.0/stable", "num_units": 3},
            "etcd": {"charm": "etcd"},
            "easyrsa": {"charm": "easyrsa"},
        },
    },
    "focal": {
        "base": "ubuntu@20.04",
        "apps": {
            "postgresql": {"charm": "postgresql", "channel": "latest/stable"},
            "mysql": {"charm": "mysql-innodb-cluster", "channel": "8.0/stable", "num_units": 3},
            "etcd": {"charm": "etcd"},
            "easyrsa": {"charm": "easyrsa"},
        },
    },
}
# The bundle's relations, the same for every release.
BUNDLE_RELATIONS = [("etcd:certificates", "easyrsa:client")]
# The bundle apps that juju-backup-all doesn't back up.
BUNDLE_NON_TARGETS = {"easyrsa"}

# The machine charms that juju-backup-all backs up through their S3 create-backup action, for
# each release that tests them: app name (which is also the charm name) -> the arguments for
# deploying it. Each app uses the release's base where its channel has one; most channels only
# publish a single base.
S3_BACKUP_TARGETS = {
    "noble": {
        "postgresql": {
            "channel": "16/stable",
            "base": "ubuntu@24.04",
            "num_units": NUM_UNITS,
            "config": TESTING_PROFILE,
        },
        "mysql": {
            "channel": "8.0/stable",
            "base": "ubuntu@22.04",
            "num_units": MYSQL_NUM_UNITS,
            "config": TESTING_PROFILE,
        },
        "mongodb": {"channel": "8/stable", "base": "ubuntu@24.04", "num_units": NUM_UNITS},
        "zookeeper": {"channel": "3/stable", "base": "ubuntu@22.04", "num_units": NUM_UNITS},
    },
    "jammy": {
        "postgresql": {
            "channel": "14/stable",
            "base": "ubuntu@22.04",
            "num_units": NUM_UNITS,
            "config": TESTING_PROFILE,
        },
        "mysql": {
            "channel": "8.0/stable",
            "base": "ubuntu@22.04",
            "num_units": MYSQL_NUM_UNITS,
            "config": TESTING_PROFILE,
        },
        "mongodb": {"channel": "6/stable", "base": "ubuntu@22.04", "num_units": NUM_UNITS},
        "zookeeper": {"channel": "3/stable", "base": "ubuntu@22.04", "num_units": NUM_UNITS},
    },
}

# The k8s charms that back up to S3, in the same form. Every release in S3_BACKUP_TARGETS backs
# up the same ones.
K8S_S3_BACKUP_TARGETS = {
    "mysql-k8s": {
        "channel": "8.0/stable",
        "base": "ubuntu@22.04",
        "trust": True,
        "num_units": MYSQL_NUM_UNITS,
        "config": TESTING_PROFILE,
    },
    "postgresql-k8s": {
        "channel": "16/stable",
        "base": "ubuntu@24.04",
        "trust": True,
        "num_units": NUM_UNITS,
        "config": TESTING_PROFILE,
    },
    "mongodb-k8s": {
        "channel": "6/stable",
        "base": "ubuntu@22.04",
        "trust": True,
        "num_units": NUM_UNITS,
    },
    "zookeeper-k8s": {
        "channel": "3/stable",
        "base": "ubuntu@22.04",
        "trust": False,
        "num_units": NUM_UNITS,
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
