# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""Functional tests for the juju-backup-all charm.

For each Ubuntu release in BUNDLES, in a model of its own:

1. test_deploy: deploy the bundle ported from the old zaza suite (tests.yaml, bundles/ and
   configure.py) and wait for zaza's target_deploy_status.
2. test_deploy_s3_backup_targets: for the releases in S3_BACKUP_TARGETS, deploy the charms that
   juju-backup-all backs up to S3, ported from juju-backup-all's own functional suite. The
   machine charms go in a second model for the release, and the k8s charms go in a model on a
   k8s cloud deployed for the release.
3. test_configure: give the charm the credentials of the controller the tests run on.
4. test_01_install and test_02_do_backup_action: the checks themselves.

CI runs each release in a job of its own, with `tox -e func -- -k <release>`. When one run
covers several releases, they share one controller, so each release's models are destroyed
before the next release starts. With every release's models up at once, the controller ran out
of resources and died.

The charm under test comes from CHARM_PATH_<RELEASE>, for example CHARM_PATH_NOBLE. CI already
sets these variables for the zaza overlays.
"""

import base64
import datetime
import ipaddress
import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, Set, Tuple

import jubilant
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from pytest_jubilant import JujuFactory

from config import BACKUP_USERNAME, Paths

APP_NAME = "juju-backup-all"
UNIT_NAME = "juju-backup-all/0"
LEADER = f"{APP_NAME}/leader"

# Each release's CI job has a 2 hour limit, so no single wait may take most of it.
# A wait that times out fails the test with the status it saw, instead of CI cancelling the job
# with no output.
DEPLOY_TIMEOUT = 30 * 60
WAIT_TIMEOUT = 20 * 60
# For the database clusters to form and take their S3 credentials, and for a full backup.
LONG_WAIT_TIMEOUT = 30 * 60
K8S_WAIT_TIMEOUT = 20 * 60
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

# The model config zaza applies to every test model. zaza's "default-series" is left out
# because every app here sets its own base.
MODEL_CONFIG = {
    "image-stream": "daily",
    "test-mode": True,
    "transmit-vendor-metrics": False,
    "enable-os-upgrade": False,
    "automatically-retry-hooks": False,
    "use-default-secgroup": True,
}

# The old target_deploy_status from tests.yaml. As in zaza, "active" is always allowed, and a
# listed status or message prefix adds to the defaults below. A regex replaces the prefixes.
# Apps that aren't listed (mysql) use the defaults alone.
TARGET_DEPLOY_STATUS = {
    APP_NAME: {
        "workload-status": "blocked",
        "workload-status-message-prefix": "Waiting for controllers/accounts configuration",
    },
    "postgresql": {
        "workload-status": "active",
        "workload-status-message-regex": "^Primary.*|^Live master.*",
    },
    "etcd": {
        "workload-status": "active",
        "workload-status-message-prefix": "Healthy with 1 known peer",
    },
    "easyrsa": {
        "workload-status": "active",
        "workload-status-message-prefix": "Certificate Authority connected.",
    },
}
APPROVED_STATUSES = ["active"]
APPROVED_MESSAGE_PREFIXES = ["ready", "Ready", "Unit is ready"]

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
# The k8s charms that back up to S3. Every release in S3_BACKUP_TARGETS backs up the same ones.
K8S_S3_BACKUP_TARGETS = {
    "mysql-k8s": {"channel": "8.0/stable", "base": "ubuntu@22.04", "trust": True},
    "postgresql-k8s": {"channel": "16/stable", "base": "ubuntu@24.04", "trust": True},
    "mongodb-k8s": {"channel": "6/stable", "base": "ubuntu@22.04", "trust": True},
    "zookeeper-k8s": {"channel": "3/stable", "base": "ubuntu@22.04", "trust": False},
}
# The apps whose clusters must finish forming before they are related to S3. Relating them
# mid-bootstrap fails s3-credentials-relation-changed on non-leader units.
S3_CLUSTER_APPS = ("mysql", "mongodb", "zookeeper")
K8S_S3_CLUSTER_APPS = ("mysql-k8s", "mongodb-k8s", "zookeeper-k8s")
# PostgreSQL (pgBackRest) only supports S3 over HTTPS, but some other apps like mysql do not
# support self-signed TLS certificates, so PostgreSQL gets its own MinIO with TLS.
TLS_S3_APPS = ("postgresql", "postgresql-k8s")
# Two units per S3 backup target, so that each has a replica: juju-backup-all backs mysql up on
# a non-primary unit, and tries postgresql's replicas before its primary. Three units per target
# didn't fit on the CI runner next to the k8s cloud.
NUM_UNITS = 2

K8S_CLOUD = "juju-backup-all-charm-k8s"
S3_INTEGRATOR_CHANNEL = "2/stable"
S3_INTEGRATOR_BASE = "ubuntu@24.04"
MINIO_CHANNEL = "ckf-1.10/stable"
MINIO_CREDENTIALS = {"access-key": "ahs9ao#Fua", "secret-key": "ohCa!uB6oo"}

S3_TASK_TIMEOUT = 10 * 60  # 10 minutes


@pytest.fixture(scope="module", params=list(BUNDLES))
def series(request: pytest.FixtureRequest) -> str:
    """Ubuntu release under test. pytest runs every test for one release before the next."""
    return request.param


@pytest.fixture(scope="module")
def machine_cloud(request: pytest.FixtureRequest) -> str:
    """Cloud for the machine models: --juju-cloud if given, else the controller's own cloud.

    Once the k8s cloud is registered, the controller manages more than one cloud, and
    `juju add-model` fails unless it is told which one to use.
    """
    controllers = json.loads(
        jubilant.Juju().cli("controllers", "--format", "json", include_model=False)
    )
    cloud = controllers["controllers"][controller_name(request)]["cloud"]
    return request.config.getoption("--juju-cloud") or cloud


@pytest.fixture(scope="module")
def juju(
    juju_factory: JujuFactory, series: str, machine_cloud: str, request: pytest.FixtureRequest
):
    """Model for the current release, replacing pytest-jubilant's single-model ``juju``."""
    juju = juju_factory.get_juju(series, cloud=machine_cloud)
    yield juju
    destroy_model(juju_factory, juju, request)


@pytest.fixture(scope="module")
def juju_s3(
    juju_factory: JujuFactory, series: str, machine_cloud: str, request: pytest.FixtureRequest
):
    """Model for the current release's machine charms in S3_BACKUP_TARGETS.

    Unlike the bundle's model, it keeps Juju's default model config. The clusters' hooks can
    fail while the clusters form, and they rely on Juju retrying them.
    """
    if series not in S3_BACKUP_TARGETS:
        pytest.skip(f"{series} has no S3 backup targets")
    juju = juju_factory.get_juju(f"{series}-s3", cloud=machine_cloud)
    yield juju
    destroy_model(juju_factory, juju, request)


@pytest.fixture(scope="module")
def k8s_host_juju(
    juju_factory: JujuFactory, series: str, machine_cloud: str, request: pytest.FixtureRequest
):
    """Model for the k8s charm that provides the k8s cloud ``juju_k8s`` runs on."""
    juju = juju_factory.get_juju(f"{series}-k8s-host", cloud=machine_cloud)
    yield juju
    destroy_model(juju_factory, juju, request)


@pytest.fixture(scope="module")
def juju_k8s(
    juju_factory: JujuFactory,
    series: str,
    k8s_host_juju: jubilant.Juju,
    request: pytest.FixtureRequest,
):
    """Model on the k8s cloud, for MinIO and the charms in K8S_S3_BACKUP_TARGETS.

    Only request it once the k8s cloud is registered. On teardown, destroy the model and then
    remove the cloud, before ``k8s_host_juju`` destroys the k8s charm behind it.
    """
    juju = juju_factory.get_juju(f"{series}-k8s", cloud=K8S_CLOUD)
    yield juju

    if request.config.getoption("--no-juju-teardown"):
        return
    # The cloud can't be removed while a model is still on it.
    destroy_model(juju_factory, juju, request)
    jubilant.Juju().cli(
        "remove-k8s",
        K8S_CLOUD,
        "--controller",
        controller_name(request),
        include_model=False,
    )


def charm_path(series: str) -> Path:
    """Return the path of the charm built for ``series``, read from CHARM_PATH_<RELEASE>."""
    env_var = f"CHARM_PATH_{series.upper()}"
    path = os.environ.get(env_var)
    if not path:
        pytest.fail(f"{env_var} must be set to the path of the charm built for {series}")
    return Path(path)


def controller_name(request: pytest.FixtureRequest) -> str:
    """Return --juju-controller if given, else the current controller."""
    controllers = json.loads(
        jubilant.Juju().cli("controllers", "--format", "json", include_model=False)
    )
    return request.config.getoption("--juju-controller") or controllers["current-controller"]


def destroy_model(juju_factory: JujuFactory, juju: jubilant.Juju, request: pytest.FixtureRequest):
    """Destroy ``juju``'s model now, instead of when juju_factory tears down with the module.

    Drop it from juju_factory, so that it doesn't try to destroy it again. juju_factory only
    logs the debug-log of the models it still has, so log this one's here if a test failed.
    """
    if request.config.getoption("--no-juju-teardown"):
        return
    model = juju.model
    assert model is not None
    if request.session.testsfailed:
        try:
            logging.error(f"juju debug-log for model {model}:\n{juju.debug_log(limit=1000)}")
        except jubilant.CLIError as e:
            logging.error(f"Could not get the juju debug-log for model {model}: {e}")
    juju.destroy_model(model, destroy_storage=True, force=True, timeout=DESTROY_TIMEOUT)
    juju_factory._models.pop(model, None)  # pyright: ignore[reportPrivateUsage]


def short_model_name(juju: jubilant.Juju) -> str:
    """Return the name of ``juju``'s model without its controller."""
    assert juju.model is not None
    return juju.model.rpartition(":")[-1]


def any_unit_error(status: jubilant.Status) -> bool:
    """Report whether any unit's workload is in error."""
    return any(
        unit.workload_status.current == "error"
        for app in status.apps
        for unit in status.get_units(app).values()
    )


def any_provisioning_error(status: jubilant.Status) -> bool:
    """Report whether any machine failed to provision."""
    return any(
        machine.machine_status.current == "provisioning error"
        for machine in status.machines.values()
    )


def any_hard_error(status: jubilant.Status) -> bool:
    """Port of zaza's check_model_for_hard_errors."""
    return any_unit_error(status) or any_provisioning_error(status)


def base_started(status: jubilant.Status, base: str) -> bool:
    """Report whether a machine on ``base``, for example "ubuntu@24.04", has started."""
    return any(
        machine.juju_status.current == "started"
        and machine.base is not None
        and f"{machine.base.name}@{machine.base.channel}" == base
        for machine in status.machines.values()
    )


def deploy_machine_charm(
    juju: jubilant.Juju, charm, app: str, *, base: str, num_units: int = 1, **kwargs
):
    """Deploy a machine charm, starting a single machine first if the model has none on ``base``.

    LXD downloads a base's image the first time a machine needs it, and machines that download
    the same image at the same time can fail with "Alias already exists:
    juju/ubuntu@<version>/amd64". Once one machine has started, the image is cached and the
    others reuse it.
    """
    if base_started(juju.status(), base):
        juju.deploy(charm, app, base=base, num_units=num_units, **kwargs)
        return
    juju.deploy(charm, app, base=base, **kwargs)
    juju.wait(
        lambda status: base_started(status, base),
        error=any_provisioning_error,
        timeout=WAIT_TIMEOUT,
    )
    if num_units > 1:
        juju.add_unit(app, num_units=num_units - 1)


def app_reached_target(status: jubilant.Status, app: str) -> bool:
    """Report whether every unit of ``app`` is idle and matches its target deploy status."""
    target = TARGET_DEPLOY_STATUS.get(app, {})
    statuses = list(APPROVED_STATUSES)
    if "workload-status" in target:
        statuses.append(target["workload-status"])
    prefixes = list(APPROVED_MESSAGE_PREFIXES)
    if "workload-status-message-prefix" in target:
        prefixes.append(target["workload-status-message-prefix"])
    regex = target.get("workload-status-message-regex")

    units = status.apps[app].units
    if not units:
        return False
    for unit in units.values():
        message = unit.workload_status.message
        if regex is not None:
            message_ok = re.search(regex, message) is not None
        else:
            message_ok = message.startswith(tuple(prefixes))
        if not (
            unit.juju_status.current == "idle"
            and unit.workload_status.current in statuses
            and message_ok
        ):
            return False
    return True


def wait_for_target_deploy_status(juju: jubilant.Juju):
    """Port of zaza's wait_for_application_states.

    As in zaza, an app that reaches its target once is done, even if its status changes later.
    """
    pending = set(juju.status().apps)

    def ready(status: jubilant.Status) -> bool:
        pending.difference_update([app for app in pending if app_reached_target(status, app)])
        return not pending

    juju.wait(ready, error=any_hard_error, timeout=DEPLOY_TIMEOUT)


def wait_for_unit_workload_status(juju: jubilant.Juju, unit: str, workload_status: str):
    """Port of zaza's block_until_unit_wl_status."""
    app = unit.split("/")[0]
    juju.wait(
        lambda status: status.apps[app].units[unit].workload_status.current == workload_status,
        timeout=WAIT_TIMEOUT,
    )


def wait_for_clusters(juju: jubilant.Juju, apps: Tuple[str, ...]):
    """Wait for the database clusters in ``apps`` to finish forming.

    Their hooks can fail while the clusters form, and Juju retries them, so this doesn't fail
    fast. A persistent failure still keeps a unit out of active, so the wait times out.
    """
    juju.wait(
        lambda status: jubilant.all_active(status, *apps)
        and jubilant.all_agents_idle(status, *apps),
        timeout=LONG_WAIT_TIMEOUT,
    )


def run_on_leader(juju: jubilant.Juju, command: str) -> jubilant.Task:
    """Run ``command`` on the leader unit, and return the task even if the command fails.

    zaza returned failed commands instead of raising, so the tests check the return code.
    """
    try:
        return juju.exec(command, unit=LEADER)
    except jubilant.TaskError as e:
        return e.task


def run_action_on_leader(juju: jubilant.Juju, action: str, params=None) -> jubilant.Task:
    """Run ``action`` on the leader unit, and return the task even if the action fails.

    zaza waited as long as the action took. Juju's own default wait is too short for a full
    backup, which goes through every S3 backup target in turn, so wait up to LONG_WAIT_TIMEOUT.
    """
    try:
        return juju.run(LEADER, action, params, wait=LONG_WAIT_TIMEOUT)
    except jubilant.TaskError as e:
        return e.task


def assert_file_stat(juju: jubilant.Juju, filename, filetype: str):
    """Check that a file exists and has the expected type."""
    # `stat -c %F` prints the file type ("directory", "regular file", ...)
    # and exits non-zero if the path doesn't exist. It runs on the unit, not locally.
    task = run_on_leader(juju, "stat -c %F {}".format(filename))
    actual_filetype = task.stdout.strip()
    assert task.return_code == 0, "'{}' does not exist".format(filename)
    assert actual_filetype == filetype, "'{}' has the wrong type: '{}'".format(
        filename, actual_filetype
    )


def get_controller_name(controllers_file_contents: str) -> str:
    """Obtain the current controller name from controllers.yaml."""
    controller_name = re.findall(r"current-controller:\s(.*)$", controllers_file_contents)[0]
    logging.info(f"Current controller name: {controller_name}")
    return controller_name


def generate_self_signed_cert(ip: str) -> Tuple[bytes, bytes]:
    """Return a PEM-encoded self-signed certificate and private key valid for ``ip``.

    The certificate is its own CA so it can be passed as the client's ``tls-ca-chain``.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "minio.example.test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ip))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return cert.public_bytes(serialization.Encoding.PEM), key_pem


def kubectl(k8s_host_juju: jubilant.Juju, *args: str) -> str:
    """Run ``kubectl`` on the k8s charm's unit and return its output."""
    return k8s_host_juju.ssh("k8s/0", "sudo k8s kubectl", *args)


def expose_via_loadbalancer(
    k8s_host_juju: jubilant.Juju, juju_k8s: jubilant.Juju, app: str
) -> str:
    """Patch ``app``'s Kubernetes Service to type LoadBalancer and return its external IP."""
    namespace = short_model_name(juju_k8s)
    kubectl(
        k8s_host_juju,
        f"-n {namespace} patch svc {app}",
        '-p \'{"spec": {"type": "LoadBalancer"}}\'',
    )
    kubectl(
        k8s_host_juju,
        f"-n {namespace} wait svc {app}",
        "--for=jsonpath='{.status.loadBalancer.ingress[0].ip}'",
        "--timeout=5m",
    )
    return kubectl(
        k8s_host_juju,
        f"-n {namespace} get svc {app}",
        "-o",
        "jsonpath='{.status.loadBalancer.ingress[0].ip}'",
    ).strip()


def expose_via_nodeport(
    k8s_host_juju: jubilant.Juju, juju_k8s: jubilant.Juju, app: str, port: int
) -> int:
    """Patch ``app``'s Kubernetes Service to type NodePort and return the node port for ``port``.

    The node port is reachable on the k8s unit's address. Use this when the single
    LoadBalancer IP is already taken.
    """
    namespace = short_model_name(juju_k8s)
    kubectl(
        k8s_host_juju,
        f"-n {namespace} patch svc {app}",
        '-p \'{"spec": {"type": "NodePort"}}\'',
    )
    return int(
        kubectl(
            k8s_host_juju,
            f"-n {namespace} get svc {app}",
            "-o",
            f"jsonpath='{{.spec.ports[?(@.port=={port})].nodePort}}'",
        ).strip()
    )


def deploy_k8s_cloud(k8s_host_juju: jubilant.Juju, request: pytest.FixtureRequest):
    """Deploy and configure the k8s charm, and register it as the K8S_CLOUD Juju cloud."""
    k8s_host_juju.deploy(
        "k8s",
        channel="1.32/stable",
        base="ubuntu@24.04",
        constraints={
            "cores": "4",
            "mem": "16G",
            "root-disk": "100G",
            "virt-type": "virtual-machine",
        },
        config={
            "load-balancer-enabled": True,
            "local-storage-enabled": True,
        },
    )
    k8s_host_juju.wait(lambda status: jubilant.all_active(status, "k8s"), timeout=K8S_WAIT_TIMEOUT)

    k8s_unit_ip = k8s_host_juju.status().apps["k8s"].units["k8s/0"].public_address
    k8s_host_juju.config("k8s", {"load-balancer-cidrs": f"{k8s_unit_ip}/32"})

    kubeconfig = k8s_host_juju.ssh("k8s/0", "sudo k8s config")
    jubilant.Juju().cli(
        "add-k8s",
        K8S_CLOUD,
        "--controller",
        controller_name(request),
        include_model=False,
        stdin=kubeconfig,
    )


def deploy_minio(juju_k8s: jubilant.Juju, k8s_node_ip: str):
    """Deploy MinIO for the S3 backups, and a second MinIO with TLS for PostgreSQL.

    The single LoadBalancer IP is the k8s node's own address and is taken by "minio", so
    "minio-tls" is exposed via NodePort on that same address and its certificate is for it.
    """
    juju_k8s.deploy("minio", channel=MINIO_CHANNEL, trust=True, config=MINIO_CREDENTIALS)

    cert_pem, key_pem = generate_self_signed_cert(k8s_node_ip)
    juju_k8s.deploy(
        "minio",
        "minio-tls",
        channel=MINIO_CHANNEL,
        trust=True,
        config={
            **MINIO_CREDENTIALS,
            "ssl-cert": base64.b64encode(cert_pem).decode("ascii"),
            "ssl-key": base64.b64encode(key_pem).decode("ascii"),
        },
    )


def relate_to_s3(juju: jubilant.Juju, secret, app: str, s3_config: Dict[str, str], machine: bool):
    """Deploy an s3-integrator for ``app`` with ``s3_config`` and relate them.

    ``secret`` holds the MinIO credentials in ``juju``'s model.
    """
    integrator = f"s3-integrator-{app}"
    config = {"bucket": f"{app}-backups", "s3-uri-style": "path", **s3_config}
    if machine:
        deploy_machine_charm(
            juju,
            "s3-integrator",
            integrator,
            base=S3_INTEGRATOR_BASE,
            channel=S3_INTEGRATOR_CHANNEL,
            config=config,
        )
    else:
        juju.deploy("s3-integrator", integrator, channel=S3_INTEGRATOR_CHANNEL, config=config)
    juju.grant_secret("s3-credentials", integrator)
    juju.config(integrator, {"credentials": secret})
    if app in TLS_S3_APPS:
        juju.wait(
            lambda status: jubilant.all_active(status, integrator),
            error=lambda status: jubilant.any_error(status, integrator),
            timeout=WAIT_TIMEOUT,
        )
    juju.integrate(app, integrator)


def expected_app_backups(
    juju: jubilant.Juju, series: str, request: pytest.FixtureRequest
) -> Dict[str, Set[str]]:
    """Return the apps that do-backup must back up for this release, by model name."""
    # easyrsa is in the bundle too, but it is not a backup target.
    bundle_apps = {"mysql", "etcd"}
    if "postgresql-channel" in BUNDLES[series]:
        bundle_apps.add("postgresql")
    expected = {short_model_name(juju): bundle_apps}

    if series in S3_BACKUP_TARGETS:
        juju_s3 = request.getfixturevalue("juju_s3")
        juju_k8s = request.getfixturevalue("juju_k8s")
        expected[short_model_name(juju_s3)] = set(S3_BACKUP_TARGETS[series])
        expected[short_model_name(juju_k8s)] = set(K8S_S3_BACKUP_TARGETS)
    return expected


@pytest.mark.juju_setup
def test_deploy(juju: jubilant.Juju, series: str):
    """Deploy the bundle for this release and wait for the target deploy status."""
    bundle = BUNDLES[series]
    base = bundle["base"]

    juju.model_config(MODEL_CONFIG)

    # mysql and etcd are the backup targets, and so is postgresql on focal. easyrsa is only
    # there to issue etcd's TLS certificates.
    deploy_machine_charm(juju, charm_path(series), APP_NAME, base=base)
    if "postgresql-channel" in bundle:
        deploy_machine_charm(
            juju, "postgresql", "postgresql", base=base, channel=bundle["postgresql-channel"]
        )
    deploy_machine_charm(
        juju,
        "mysql-innodb-cluster",
        "mysql",
        base=base,
        channel=bundle["mysql-innodb-cluster-channel"],
        num_units=3,
    )
    deploy_machine_charm(juju, "etcd", "etcd", base=base)
    deploy_machine_charm(juju, "easyrsa", "easyrsa", base=base)
    juju.integrate("etcd:certificates", "easyrsa:client")

    # juju-backup-all is expected to be blocked here, because it has no credentials yet.
    wait_for_target_deploy_status(juju)


@pytest.mark.juju_setup
def test_deploy_s3_backup_targets(
    juju_s3: jubilant.Juju,
    k8s_host_juju: jubilant.Juju,
    series: str,
    request: pytest.FixtureRequest,
):
    """Deploy this release's S3 backup targets, and the k8s cloud and k8s targets.

    Anything already deployed, when rerunning against existing models, is left as it is.

    Ported from test_deploy_k8s_cloud and test_build_and_deploy in juju-backup-all's own
    functional suite. Every target gets an s3-integrator pointing at MinIO.
    """
    if "k8s" not in k8s_host_juju.status().apps:
        deploy_k8s_cloud(k8s_host_juju, request)
    juju_k8s = request.getfixturevalue("juju_k8s")
    deploy_k8s_targets = not juju_k8s.status().apps
    k8s_node_ip = k8s_host_juju.status().apps["k8s"].units["k8s/0"].public_address

    if deploy_k8s_targets:
        deploy_minio(juju_k8s, k8s_node_ip)
        for app, spec in K8S_S3_BACKUP_TARGETS.items():
            juju_k8s.deploy(
                app,
                app,
                base=spec["base"],
                channel=spec["channel"],
                trust=spec["trust"],
                num_units=NUM_UNITS,
            )

    for app, spec in S3_BACKUP_TARGETS[series].items():
        deploy_machine_charm(
            juju_s3,
            app,
            app,
            base=spec["base"],
            channel=spec["channel"],
            num_units=NUM_UNITS,
        )

    if deploy_k8s_targets:
        # MinIO's config hook resets its Service to ClusterIP, so let minio-tls settle before
        # exposing it.
        juju_k8s.wait(
            lambda status: jubilant.all_active(status, "minio", "minio-tls")
            and jubilant.all_agents_idle(status, "minio-tls"),
            timeout=WAIT_TIMEOUT,
        )
        wait_for_clusters(juju_k8s, K8S_S3_CLUSTER_APPS)
    wait_for_clusters(juju_s3, S3_CLUSTER_APPS)

    # Exposing a Service again is a no-op, so a rerun gets the same endpoints.
    minio_ip = expose_via_loadbalancer(k8s_host_juju, juju_k8s, "minio")
    minio_tls_port = expose_via_nodeport(k8s_host_juju, juju_k8s, "minio-tls", 9000)
    s3_config = {"endpoint": f"http://{minio_ip}:9000", "region": "us-east-1"}
    tls_s3_config = {
        "endpoint": f"https://{k8s_node_ip}:{minio_tls_port}",
        "region": "",
        "tls-ca-chain": str(juju_k8s.config("minio-tls")["ssl-cert"]),
    }

    targets = [(juju_s3, app, True) for app in S3_BACKUP_TARGETS[series]]
    if deploy_k8s_targets:
        targets += [(juju_k8s, app, False) for app in K8S_S3_BACKUP_TARGETS]
    secrets = {}
    for target_juju, app, machine in targets:
        if target_juju.model not in secrets:
            secrets[target_juju.model] = target_juju.add_secret(
                "s3-credentials", MINIO_CREDENTIALS
            )
        config = {**tls_s3_config, "path": app} if app in TLS_S3_APPS else s3_config
        relate_to_s3(target_juju, secrets[target_juju.model], app, config, machine)

    juju_s3.wait(jubilant.all_active, error=jubilant.any_error, timeout=LONG_WAIT_TIMEOUT)
    if deploy_k8s_targets:
        # mongodb-k8s's s3-credentials-relation-changed hook can fail even on a settled
        # cluster, and Juju retries it, so don't fail fast on it. A persistent failure still
        # keeps the unit out of active, so the wait times out instead of passing.
        fail_fast_apps = [app for app in juju_k8s.status().apps if app != "mongodb-k8s"]
        juju_k8s.wait(
            lambda status: jubilant.all_active(status) and jubilant.all_agents_idle(status),
            error=lambda status: jubilant.any_error(status, *fail_fast_apps),
            timeout=LONG_WAIT_TIMEOUT,
        )


@pytest.mark.juju_setup
def test_configure(juju: jubilant.Juju, series: str):
    """Configure juju-backup-all with the credentials of the controller the tests run on.

    juju-backup-all stays blocked until it has credentials for a controller. Pass it the test
    runner's own juju client files, the way libjuju's juju_config_dir() finds them.
    """
    juju_data = Path(os.environ.get("JUJU_DATA", "~/.local/share/juju")).expanduser()
    controllers_file_contents = (juju_data / "controllers.yaml").read_text()
    accounts_file_contents = (juju_data / "accounts.yaml").read_text()
    logging.info(f"Controllers file contents:\n{controllers_file_contents}")
    logging.info(f"Accounts file contents:\n{accounts_file_contents}")
    controller_name = get_controller_name(controllers_file_contents)

    # Limit backups to the current controller, in case the runner knows about other
    # controllers too.
    config = {
        "accounts": accounts_file_contents,
        "controllers": controllers_file_contents,
        "controller-names": controller_name,
    }
    if series in S3_BACKUP_TARGETS:
        config["timeout"] = S3_TASK_TIMEOUT
    juju.config(APP_NAME, config)

    # With valid credentials the charm should go from "blocked" to "active".
    wait_for_unit_workload_status(juju, UNIT_NAME, "active")
    juju.wait(jubilant.all_agents_idle, error=any_unit_error, timeout=WAIT_TIMEOUT)


def test_01_install(juju: jubilant.Juju):
    """Test: Installation completed successfully."""
    # The install hook creates a dedicated system user that owns the juju
    # client data and the backups. `id <user>` exits non-zero if it is missing.
    task = run_on_leader(juju, "id {}".format(BACKUP_USERNAME))
    assert task.return_code == 0, "user '{}' does not exist".format(BACKUP_USERNAME)

    # The install hook also lays out the juju client data dir
    # (/var/lib/jujubackupall) with ssh/ and cookies/ subdirectories...
    expected_dirs = [
        Paths.JUJUDATA_DIR,
        Paths.JUJUDATA_SSH_DIR,
        Paths.JUJUDATA_COOKIES_DIR,
    ]

    for path in expected_dirs:
        assert_file_stat(juju, path, "directory")

    # ...plus the SSH keypair the charm uses to reach units in other
    # models, and the auto_backup.py script that the cron job runs.
    expected_files = [
        Paths.SSH_PRIVATE_KEY,
        Paths.SSH_PUBLIC_KEY,
        Paths.AUTO_BACKUP_SCRIPT_PATH,
    ]

    for path in expected_files:
        assert_file_stat(juju, path, "regular file")


def test_02_do_backup_action(juju: jubilant.Juju, series: str, request: pytest.FixtureRequest):
    """Test: do-backup action performed successfully."""
    # The charm backs up every model on the controller, so omit the ones that belong to the
    # other releases (and the controller and k8s-host models, which have nothing to back up).
    expected = expected_app_backups(juju, series, request)
    models = json.loads(juju.cli("models", "--format", "json", include_model=False))["models"]
    omit_models = [m["short-name"] for m in models if m["short-name"] not in expected]

    # Run a full backup on demand, the same thing the cron job does on schedule.
    task = run_action_on_leader(juju, "do-backup", {"omit-models": ",".join(omit_models)})
    assert task.return_code == 0, "Backup action failed."

    # The action puts a JSON summary of everything it backed up into the
    # "result" key, e.g.:
    #   {"controller_backups": [{"download_path": ...}],
    #    "config_backups":     [{"download_path": ...}],
    #    "app_backups":        [{"model": ..., "app": "mysql", "download_path": ...}, ...]}
    # and an "errors" key if anything failed to back up.
    result_str = task.results.get("result")
    assert result_str is not None, "Action returned None!"
    logging.info(f"Result of action: {result_str}")
    result = json.loads(result_str)
    assert "errors" not in result, "Some backups failed: {}".format(result.get("errors"))

    # Gather the reported backup paths: the controller backup, the local
    # juju client config backup, and one backup per supported app.
    controller_bkp_path = result["controller_backups"][0].get("download_path")
    config_bkp_path = result["config_backups"][0].get("download_path")
    app_backups_list = result["app_backups"]
    app_names = {}
    db_bkp_paths = []

    # each app is a dictionary in the list
    for app_dict in app_backups_list:
        app_names.setdefault(app_dict["model"], set()).add(app_dict["app"])
        db_bkp_paths.append(app_dict["download_path"])

    # Every supported app in this release's models must have been backed up.
    assert app_names == expected

    # Backups are downloaded onto the juju-backup-all unit, so check that
    # every reported path is a real file there.
    expected_bkp_files = [controller_bkp_path, config_bkp_path]
    expected_bkp_files.extend(db_bkp_paths)
    for path in expected_bkp_files:
        assert_file_stat(juju, path, "regular file")

    # The S3 backups stay in S3, and the charm only keeps their metadata: the backup id in a
    # .txt file, or the create-backup action's results in a .json file for PostgreSQL.
    for path in db_bkp_paths:
        if path.endswith(".txt"):
            assert run_on_leader(juju, f"cat {path}").stdout.strip(), f"'{path}' is empty"
        elif path.endswith(".json"):
            metadata = json.loads(run_on_leader(juju, f"cat {path}").stdout)
            assert metadata.get("backup-status") == "backup created", metadata

    # The charm must go back to active once the backup is done.
    wait_for_unit_workload_status(juju, UNIT_NAME, "active")
