# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""Functional tests for the juju-backup-all charm.

The tests run once for each deployment in DEPLOYMENTS, such as "noble-bundle" or
"noble-k8s-bundle". Each one has juju-backup-all, and the charms it backs up:

- "<release>-bundle", for each release in BUNDLES, has the machine charms: the bundle ported
  from the old zaza suite (tests.yaml, bundles/ and configure.py), and for a release in
  S3_BACKUP_TARGETS, the machine charms that juju-backup-all backs up to S3.
- "<release>-k8s-bundle", for each release in S3_BACKUP_TARGETS, has the k8s charms in
  K8S_S3_BACKUP_TARGETS, which juju-backup-all backs up to S3.

The S3 backup targets are ported from juju-backup-all's own functional suite. They back up to
MinIO, in a model on a k8s cloud deployed for the deployment. The machine S3 targets go in a
model of their own, and the k8s ones in MinIO's model.

For each deployment:

1. test_deploy: deploy juju-backup-all, and for a "-bundle" deployment the bundle, and wait for
   zaza's target_deploy_status.
2. test_deploy_s3_backup_targets: deploy the deployment's S3 backup targets, if it has any.
3. test_configure: give the charm the credentials of the controller the tests run on.
4. test_01_install and test_02_do_backup_action: the checks themselves.

CI runs each deployment in a job of its own, on a runner and controller of its own, with
`tox -e func -- -k <deployment>`. A deployment's charms didn't all fit on one runner next to
another's. When one run covers several deployments, they share one controller, so each
deployment's models are destroyed before the next one starts.

The charm under test comes from CHARM_PATH_<RELEASE>, for example CHARM_PATH_NOBLE. CI already
sets these variables for the zaza overlays.

The fixtures for the deployment and its models are in conftest.py, and the deployments in
helpers.py.
"""

import base64
import datetime
import ipaddress
import json
import logging
import os
import re
from pathlib import Path
from typing import Callable, Dict, Optional, Set, Tuple

import jubilant
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from helpers import BUNDLE, BUNDLES, K8S_BUNDLE, K8S_CLOUD, S3_BACKUP_TARGETS, controller_name

from config import BACKUP_USERNAME, Paths

APP_NAME = "juju-backup-all"
UNIT_NAME = "juju-backup-all/0"
LEADER = f"{APP_NAME}/leader"

# Each release's CI job has a 2 hour limit, so no single wait may take most of it.
# A wait that times out fails the test with the status it saw, instead of CI cancelling the job
# with no output.
DEPLOY_TIMEOUT = 45 * 60
WAIT_TIMEOUT = 30 * 60
# For the database clusters to form and take their S3 credentials, and for a full backup.
LONG_WAIT_TIMEOUT = 30 * 60
K8S_WAIT_TIMEOUT = 20 * 60

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
# A mysql-innodb-cluster unit can stay blocked on "Cluster is inaccessible from this instance"
# while the cluster's other members report all three ONLINE. juju-backup-all only needs one
# unit to run mysqldump on, so a cluster that a unit reports as tolerating a failure is enough.
INNODB_CLUSTER_ONLINE = re.compile(r"Cluster is ONLINE and can tolerate up to (ONE|\d+) failures?")

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
# Two units per S3 backup target, so that each has a replica: juju-backup-all tries postgresql's
# replicas before its primary. Three units per target didn't fit on the CI runner next to the k8s
# cloud.
NUM_UNITS = 2
# Except mysql: its create-backup refuses unless the cluster can tolerate losing a member
# ("OK" or "OK_PARTIAL"), and a cluster of fewer than three is "OK_NO_TOLERANCE".
# juju-backup-all backs it up on a non-primary unit.
APP_NUM_UNITS = {"mysql": 3, "mysql-k8s": 3}
# mysql and postgresql default to the "production" profile, which sizes their memory from what
# the unit can see: the whole CI runner for an LXD container, or the whole k8s node for a pod.
# Several units at once ran the runner and the k8s node out of memory, so use the "testing"
# profile, which keeps them small.
APP_CONFIG = {
    app: {"profile": "testing"} for app in ("mysql", "mysql-k8s", "postgresql", "postgresql-k8s")
}

S3_INTEGRATOR_CHANNEL = "2/stable"
S3_INTEGRATOR_BASE = "ubuntu@24.04"
MINIO_CHANNEL = "ckf-1.10/stable"
MINIO_CREDENTIALS = {"access-key": "ahs9ao#Fua", "secret-key": "ohCa!uB6oo"}

S3_TASK_TIMEOUT = 10 * 60  # 10 minutes

# The k8s VM only runs MinIO for a "-bundle" deployment, and the k8s S3 backup targets too for a
# "-k8s-bundle" one.
K8S_CLOUD_CONSTRAINTS = {
    BUNDLE: {"cores": "4", "mem": "4G", "root-disk": "30G", "virt-type": "virtual-machine"},
    K8S_BUNDLE: {"cores": "4", "mem": "16G", "root-disk": "100G", "virt-type": "virtual-machine"},
}


def charm_path(series: str) -> Path:
    """Return the path of the charm built for ``series``, read from CHARM_PATH_<RELEASE>."""
    env_var = f"CHARM_PATH_{series.upper()}"
    path = os.environ.get(env_var)
    if not path:
        pytest.fail(f"{env_var} must be set to the path of the charm built for {series}")
    return Path(path)


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


def unready_units(status: jubilant.Status) -> str:
    """Return a line for each unit that isn't active and idle, with its status and message."""
    lines = [
        f"{name}: {unit.juju_status.current}, {unit.workload_status.current}: "
        f"{unit.workload_status.message!r}"
        for app in sorted(status.apps)
        for name, unit in status.get_units(app).items()
        if not (unit.workload_status.current == "active" and unit.juju_status.current == "idle")
    ]
    return "\n".join(lines) or "(every unit is active and idle)"


def wait(
    juju: jubilant.Juju,
    ready: Callable[[jubilant.Status], bool],
    *,
    error: Optional[Callable[[jubilant.Status], bool]] = None,
    timeout: float,
):
    """Run ``juju.wait``, and if it fails, fail the test with the units that aren't ready.

    jubilant's errors hold the whole status of the model, which CI truncates.
    """
    try:
        juju.wait(ready, error=error, timeout=timeout)
    except (TimeoutError, jubilant.WaitError) as e:
        reason = f"timed out after {timeout}s" if isinstance(e, TimeoutError) else "hit an error"
        pytest.fail(
            f"Waiting on model {juju.model} {reason}. "
            f"Units that aren't active and idle:\n{unready_units(juju.status())}"
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
    wait(
        juju,
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
    if status.apps[app].charm_name == "mysql-innodb-cluster":
        return all(unit.juju_status.current == "idle" for unit in units.values()) and any(
            unit.workload_status.current == "active"
            and INNODB_CLUSTER_ONLINE.search(unit.workload_status.message)
            for unit in units.values()
        )
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

    try:
        juju.wait(ready, error=any_hard_error, timeout=DEPLOY_TIMEOUT)
    except jubilant.WaitError:
        # A unit or machine is in error. jubilant's error holds the whole status, which CI
        # truncates.
        pytest.fail(
            f"A unit or machine in model {juju.model} is in error. "
            f"Units that aren't active and idle:\n{unready_units(juju.status())}"
        )
    except TimeoutError:
        # Name the apps that never reached their target instead.
        status = juju.status()
        units = [
            f"{name}: {unit.juju_status.current}, {unit.workload_status.current}: "
            f"{unit.workload_status.message!r}"
            for app in sorted(pending)
            for name, unit in status.get_units(app).items()
        ]
        pytest.fail(
            f"These apps didn't reach their target deploy status: {sorted(pending)}\n"
            + "\n".join(units)
        )


def wait_for_unit_workload_status(juju: jubilant.Juju, unit: str, workload_status: str):
    """Port of zaza's block_until_unit_wl_status."""
    app = unit.split("/")[0]
    wait(
        juju,
        lambda status: status.apps[app].units[unit].workload_status.current == workload_status,
        timeout=WAIT_TIMEOUT,
    )


def wait_for_clusters(juju: jubilant.Juju, apps: Tuple[str, ...]):
    """Wait for the database clusters in ``apps`` to finish forming.

    Their hooks can fail while the clusters form, and Juju retries them, so this doesn't fail
    fast. A persistent failure still keeps a unit out of active, so the wait times out.
    """
    wait(
        juju,
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


def deploy_k8s_cloud(k8s_host_juju: jubilant.Juju, suite: str, request: pytest.FixtureRequest):
    """Deploy and configure the k8s charm, and register it as the K8S_CLOUD Juju cloud."""
    k8s_host_juju.deploy(
        "k8s",
        channel="1.32/stable",
        base="ubuntu@24.04",
        constraints=K8S_CLOUD_CONSTRAINTS[suite],
        config={
            "load-balancer-enabled": True,
            "local-storage-enabled": True,
        },
    )
    wait(
        k8s_host_juju, lambda status: jubilant.all_active(status, "k8s"), timeout=K8S_WAIT_TIMEOUT
    )

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
        wait(
            juju,
            lambda status: jubilant.all_active(status, integrator),
            error=lambda status: jubilant.any_error(status, integrator),
            timeout=WAIT_TIMEOUT,
        )
    juju.integrate(app, integrator)


def expected_app_backups(
    juju: jubilant.Juju, series: str, suite: str, request: pytest.FixtureRequest
) -> Dict[str, Set[str]]:
    """Return the apps that do-backup must back up for this deployment, by model name.

    MinIO is in the k8s model too, but it is not a backup target.
    """
    if suite == K8S_BUNDLE:
        juju_k8s = request.getfixturevalue("juju_k8s")
        return {short_model_name(juju_k8s): set(K8S_S3_BACKUP_TARGETS)}

    # easyrsa is in the bundle too, but it is not a backup target.
    bundle_apps = {"mysql", "etcd"}
    if "postgresql-channel" in BUNDLES[series]:
        bundle_apps.add("postgresql")
    expected = {short_model_name(juju): bundle_apps}
    if series in S3_BACKUP_TARGETS:
        juju_s3 = request.getfixturevalue("juju_s3")
        expected[short_model_name(juju_s3)] = set(S3_BACKUP_TARGETS[series])
    return expected


@pytest.mark.juju_setup
def test_deploy(juju: jubilant.Juju, series: str, suite: str):
    """Deploy juju-backup-all, and the bundle for a "-bundle" deployment, and wait for them."""
    bundle = BUNDLES[series]
    base = bundle["base"]

    juju.model_config(MODEL_CONFIG)
    deploy_machine_charm(juju, charm_path(series), APP_NAME, base=base)

    if suite == BUNDLE:
        deploy_bundle(juju, series)

    # juju-backup-all is expected to be blocked here, because it has no credentials yet.
    wait_for_target_deploy_status(juju)


def deploy_bundle(juju: jubilant.Juju, series: str):
    """Deploy the bundle for ``series`` next to juju-backup-all."""
    bundle = BUNDLES[series]
    base = bundle["base"]

    # mysql and etcd are the backup targets, and so is postgresql on focal. easyrsa is only
    # there to issue etcd's TLS certificates.
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


@pytest.mark.juju_setup
def test_deploy_s3_backup_targets(series: str, suite: str, request: pytest.FixtureRequest):
    """Deploy this deployment's S3 backup targets, and the k8s cloud and MinIO they back up to.

    A "-bundle" deployment has the machine charms in S3_BACKUP_TARGETS, and a "-k8s-bundle" one
    the k8s charms in K8S_S3_BACKUP_TARGETS. Anything already deployed in the k8s model, when
    rerunning against existing models, is left as it is.

    Ported from test_deploy_k8s_cloud and test_build_and_deploy in juju-backup-all's own
    functional suite. Every target gets an s3-integrator pointing at MinIO.
    """
    machine_targets = S3_BACKUP_TARGETS.get(series, {}) if suite == BUNDLE else {}
    k8s_targets = K8S_S3_BACKUP_TARGETS if suite == K8S_BUNDLE else {}
    if not (machine_targets or k8s_targets):
        pytest.skip(f"{series}-{suite} has no S3 backup targets")

    # Only request the models this deployment needs, so that the others aren't created.
    k8s_host_juju = request.getfixturevalue("k8s_host_juju")
    if "k8s" not in k8s_host_juju.status().apps:
        deploy_k8s_cloud(k8s_host_juju, suite, request)
    juju_k8s = request.getfixturevalue("juju_k8s")
    juju_s3 = request.getfixturevalue("juju_s3") if machine_targets else None
    new_k8s_model = not juju_k8s.status().apps
    k8s_node_ip = k8s_host_juju.status().apps["k8s"].units["k8s/0"].public_address

    if new_k8s_model:
        deploy_minio(juju_k8s, k8s_node_ip)

    # The machine targets run in LXD, not on the k8s VM, so they deploy while MinIO installs.
    for app, spec in machine_targets.items():
        deploy_machine_charm(
            juju_s3,
            app,
            app,
            base=spec["base"],
            channel=spec["channel"],
            num_units=APP_NUM_UNITS.get(app, NUM_UNITS),
            config=APP_CONFIG.get(app),
        )

    if new_k8s_model:
        # MinIO's config hook resets its Service to ClusterIP, so let minio-tls settle before
        # exposing it. Deploy the k8s targets only then: with all of them starting at once on
        # the k8s VM, MinIO's install hook didn't finish within WAIT_TIMEOUT.
        wait(
            juju_k8s,
            lambda status: jubilant.all_active(status, "minio", "minio-tls")
            and jubilant.all_agents_idle(status, "minio-tls"),
            timeout=WAIT_TIMEOUT,
        )
        for app, spec in k8s_targets.items():
            juju_k8s.deploy(
                app,
                app,
                base=spec["base"],
                channel=spec["channel"],
                trust=spec["trust"],
                num_units=APP_NUM_UNITS.get(app, NUM_UNITS),
                config=APP_CONFIG.get(app),
            )
        if k8s_targets:
            wait_for_clusters(juju_k8s, K8S_S3_CLUSTER_APPS)
    if juju_s3 is not None:
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

    targets = [(juju_s3, app, True) for app in machine_targets]
    if new_k8s_model:
        targets += [(juju_k8s, app, False) for app in k8s_targets]
    secrets = {}
    for target_juju, app, machine in targets:
        if target_juju.model not in secrets:
            secrets[target_juju.model] = target_juju.add_secret(
                "s3-credentials", MINIO_CREDENTIALS
            )
        config = {**tls_s3_config, "path": app} if app in TLS_S3_APPS else s3_config
        relate_to_s3(target_juju, secrets[target_juju.model], app, config, machine)

    if juju_s3 is not None:
        wait(juju_s3, jubilant.all_active, error=jubilant.any_error, timeout=LONG_WAIT_TIMEOUT)
    if k8s_targets:
        # mongodb-k8s's s3-credentials-relation-changed hook can fail even on a settled
        # cluster, and Juju retries it, so don't fail fast on it. A persistent failure still
        # keeps the unit out of active, so the wait times out instead of passing.
        fail_fast_apps = [app for app in juju_k8s.status().apps if app != "mongodb-k8s"]
        wait(
            juju_k8s,
            lambda status: jubilant.all_active(status) and jubilant.all_agents_idle(status),
            error=lambda status: jubilant.any_error(status, *fail_fast_apps),
            timeout=LONG_WAIT_TIMEOUT,
        )


@pytest.mark.juju_setup
def test_configure(juju: jubilant.Juju, series: str, suite: str):
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
    if suite == K8S_BUNDLE or series in S3_BACKUP_TARGETS:
        config["timeout"] = S3_TASK_TIMEOUT
    juju.config(APP_NAME, config)

    # With valid credentials the charm should go from "blocked" to "active".
    wait_for_unit_workload_status(juju, UNIT_NAME, "active")
    wait(juju, jubilant.all_agents_idle, error=any_unit_error, timeout=WAIT_TIMEOUT)


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


def test_02_do_backup_action(
    juju: jubilant.Juju, series: str, suite: str, request: pytest.FixtureRequest
):
    """Test: do-backup action performed successfully."""
    # The charm backs up every model on the controller, so omit the ones that belong to other
    # deployments (and the controller and k8s-host models, which have nothing to back up).
    expected = expected_app_backups(juju, series, suite, request)
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
