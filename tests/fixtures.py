# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
from unittest.mock import Mock

CONTROLLERS_YAML = """
controllers:
  test-controller:
    uuid: 507e8c03-d300-4fff-9903-2c780541479c
    api-endpoints: ['10.5.0.15:17070', '252.0.15.1:17070']
    ca-cert: |
      -----BEGIN CERTIFICATE-----
      -----END CERTIFICATE-----
    cloud: testcloud
    region: testregion
    type: openstack
    agent-version: 2.8.11
    controller-machine-count: 1
    active-controller-machine-count: 1
    machine-count: 21
current-controller: test-controller
"""

ACCOUNTS_YAML = """
controllers:
  test-controller:
    user: admin
    password: redacted
    last-known-access: superuser
"""

MOCK_CONFIG = {
    "controller-names": "",
    "exclude-controller-backup": False,
    "exclude-juju-client-config-backup": False,
    "exclude-charms": "",
    "backup-dir": "/opt/backups",
    "timeout": 60,
    "crontab": "10 20 * *",
    "backup-retention-period": 7,
    "exclude-models": "",
}

SSH_FINGERPRINT = "a3:fe:56:ca:d8:e8:ea:04:f2:9a:fd:0f:bf:55:7c:17 (jujubackup@juju-deb4b2-tmp-7)"  # noqa E501
RAW_PUBKEY = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDWyOIKxjS6ev/Fn94ULqWFtEjXc9xk0SLR7CNXZI/21dBC2vkqD2rekR6DTeGplIuhpoCTjlW13r2V2LVbR56Ne4+n4BfSU8J+3EgOAck0t5T21anMN8Z6Bj5G1gSfWpvq1Yo1y2vkqbUEA3NECEaPI69hH/afEEBFiKy5z6jmybqdqT7Kmt15GzTiVyPtnZQsAhiSW+fX/mFSp3K3cDMgWN5h5hwmQEmldiDmd5G28rTmSeO1ycvjDPhemNGxFFREm7bkXA7BlxUsBgkOVCHrw88BfZ3oFgIY4arCFmH2HLwhQbBPGpA+0JFuQFQPEVgR+y+K8+NQcuGwdoFN41q1 jujubackup@juju-deb4b2-tmp-7"  # noqa E501


class AsyncMock(Mock):
    """Helper to mock async calls."""

    # https://stackoverflow.com/a/32498408
    async def __call__(self, *args, **kwargs):
        """Mock call method."""
        return super(AsyncMock, self).__call__(*args, **kwargs)


class MockController:
    """Mock controller for testing."""

    list_models = AsyncMock(return_value=["test-model"])


class MockModel:
    """Mock model for testing."""

    get_ssh_keys = AsyncMock(return_value={"results": [{"result": SSH_FINGERPRINT}]})
    add_ssh_keys = AsyncMock(return_value=None)
