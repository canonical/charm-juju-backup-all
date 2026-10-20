# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""Pytest hooks and fixtures for the functional tests."""

import json
import logging
from typing import Optional, Set

import jubilant
import pytest
from helpers import DEPLOYMENTS, DESTROY_TIMEOUT, K8S_CLOUD, controller_name
from pytest_jubilant import JujuFactory

_config: Optional[pytest.Config] = None
# The deployments that already have a failed test.
_failed_deployments: Set[str] = set()


def pytest_configure(config: pytest.Config):
    """Keep the config, for pytest_runtest_logreport to find the terminal reporter."""
    global _config
    _config = config


def _deployment(item: pytest.Item) -> Optional[str]:
    """Return the deployment ``item`` runs for, or None if it isn't parametrized by one."""
    callspec = getattr(item, "callspec", None)
    return callspec.params.get("deployment") if callspec else None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    """Record the deployment of every failed test."""
    report = yield
    deployment = _deployment(item)
    if report.failed and deployment is not None:
        _failed_deployments.add(deployment)
    return report


def pytest_runtest_setup(item: pytest.Item):
    """Skip the rest of a deployment's tests once one of them has failed.

    Each test builds on the ones before it, so after a failure the rest would only fail too,
    each after waiting out its timeouts. Together those waits outlast the CI job, which is then
    cancelled before pytest prints its summary or the models' debug-logs.
    """
    deployment = _deployment(item)
    if deployment in _failed_deployments:
        pytest.skip(f"an earlier test for {deployment} failed")


def pytest_runtest_logreport(report: pytest.TestReport):
    """Print a failure's traceback as soon as it happens.

    pytest only prints tracebacks in its summary at the end of the session. When CI cancels a
    job that runs out of time, the session never ends, and the tracebacks are lost.
    """
    if not report.failed or _config is None:
        return
    reporter = _config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return
    reporter.write_line("")
    reporter.write_sep("!", f"{report.nodeid} ({report.when}) failed")
    report.toterminal(reporter._tw)  # pyright: ignore[reportPrivateUsage]
    reporter.write_line("")


@pytest.fixture(scope="module", params=DEPLOYMENTS)
def deployment(request: pytest.FixtureRequest) -> str:
    """Deployment under test. pytest runs every test for one deployment before the next."""
    return request.param


@pytest.fixture(scope="module")
def series(deployment: str) -> str:
    """Return the Ubuntu release that juju-backup-all runs on, for example "noble"."""
    return deployment.partition("-")[0]


@pytest.fixture(scope="module")
def suite(deployment: str) -> str:
    """Return what juju-backup-all backs up in this deployment: BUNDLE or K8S_BUNDLE."""
    return deployment.partition("-")[2]


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
    juju_factory: JujuFactory, deployment: str, machine_cloud: str, request: pytest.FixtureRequest
):
    """Model for juju-backup-all, and for a bundle deployment the bundle.

    It replaces pytest-jubilant's single-model ``juju``.
    """
    juju = juju_factory.get_juju(deployment, cloud=machine_cloud)
    yield juju
    destroy_model(juju_factory, juju, request)


@pytest.fixture(scope="module")
def juju_s3(
    juju_factory: JujuFactory, deployment: str, machine_cloud: str, request: pytest.FixtureRequest
):
    """Model for a "-bundle" deployment's machine charms in S3_BACKUP_TARGETS.

    Unlike juju-backup-all's model, it keeps Juju's default model config. The clusters' hooks
    can fail while the clusters form, and they rely on Juju retrying them.
    """
    juju = juju_factory.get_juju(f"{deployment}-s3", cloud=machine_cloud)
    yield juju
    destroy_model(juju_factory, juju, request)


@pytest.fixture(scope="module")
def k8s_host_juju(
    juju_factory: JujuFactory, deployment: str, machine_cloud: str, request: pytest.FixtureRequest
):
    """Model for the k8s charm that provides the k8s cloud ``juju_k8s`` runs on."""
    juju = juju_factory.get_juju(f"{deployment}-k8s-host", cloud=machine_cloud)
    yield juju
    destroy_model(juju_factory, juju, request)


@pytest.fixture(scope="module")
def juju_k8s(
    juju_factory: JujuFactory,
    deployment: str,
    k8s_host_juju: jubilant.Juju,
    request: pytest.FixtureRequest,
):
    """Model on the k8s cloud, for MinIO and the charms in K8S_S3_BACKUP_TARGETS.

    Only request it once the k8s cloud is registered. On teardown, destroy the model and then
    remove the cloud, before ``k8s_host_juju`` destroys the k8s charm behind it.
    """
    juju = juju_factory.get_juju(f"{deployment}-k8s", cloud=K8S_CLOUD)
    yield juju

    if request.config.getoption("--no-juju-teardown"):
        return
    # The cloud can't be removed while a model is still on it.
    destroy_model(juju_factory, juju, request)
    try:
        jubilant.Juju().cli(
            "remove-k8s",
            K8S_CLOUD,
            "--controller",
            controller_name(request),
            include_model=False,
        )
    except jubilant.CLIError as e:
        # The model can still be on its way out if destroying it timed out. CI throws the
        # controller away anyway, so don't turn the cleanup into a test error.
        logging.warning(f"Could not remove the {K8S_CLOUD} cloud: {e}")


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
