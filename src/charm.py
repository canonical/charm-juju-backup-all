#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import os
import logging

from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, ModelError

from utils import JujuBackupAllHelper

logger = logging.getLogger(__name__)


class JujuBackupAllCharm(CharmBase):
    """Charm the service."""

    _stored = StoredState()

    def __init__(self, *args):
        """Initialize charm and configure states and events to observe."""
        super().__init__(*args)
        self.framework.observe(self.on.install, self._on_install_or_upgrade)
        self.framework.observe(self.on.upgrade_charm, self._on_install_or_upgrade)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.do_backup_action, self._on_do_backup_action)
        self.framework.observe(
            self.on.push_ssh_keys_action, self._on_push_ssh_keys_action
        )
        self.framework.observe(
            self.on.nrpe_external_master_relation_changed,
            self._on_nem_changed,
        )

        # initialise helpers, etc.
        self.helper = JujuBackupAllHelper(self.model, self._stored)
        self._snap_path = None
        self._snap_path_set = False
        self._configure_logging()

        # initialize relation hooks
        self.framework.observe(
            self.on["metrics-endpoint"].relation_joined,
            self._on_exporter_relation_joined,
        )
        self.framework.observe(
            self.on["metrics-endpoint"].relation_departed,
            self._on_exporter_relation_departed,
        )
        self.metrics_endpoint = MetricsEndpointProvider(
            self,
            "metrics-endpoint",
            jobs=[
                {
                    "static_configs": [
                        {
                            "targets": [f"*:{self.model.config['exporter-port']}"],
                        }
                    ],
                }
            ],
        )

        self._stored.set_default(installed=False, config={})

    @property
    def snap_path(self):
        """Get local path to exporter snap.

        Returns:
            snap_path: the path to the snap file if exporter snap is attached or None.
        """
        if not self._snap_path_set:
            try:
                self._snap_path = str(self.model.resources.fetch("exporter-snap").absolute())
                # Don't return path to empty resource file
                if not os.path.getsize(self._snap_path) > 0:
                    self._snap_path = None
            except ModelError:
                self._snap_path = None
            finally:
                self._snap_path_set = True

        return self._snap_path

    def _on_do_backup_action(self, event):
        """Handle the dobackup action."""
        omit_models_param = event.params.get("omit-models", "")
        if omit_models_param:
            omit_models = omit_models_param.split(",")
        else:
            omit_models = []
        backup_results = self.helper.perform_backup(omit_models=omit_models)
        event.set_results({"result": backup_results})

    def _on_push_ssh_keys_action(self, event):
        """Handle the push-ssh-keys action."""
        result = self.helper.push_ssh_keys()
        event.set_results({"result": result})

    def _on_install_or_upgrade(self, event):
        """Install charm and perform initial setup."""
        self.helper.create_backup_user()
        self.helper.init_jujudata_dir()
        self.helper.deploy_scripts()
        self._stored.installed = True
        self.model.unit.status = ActiveStatus("Install complete")
        logging.info("Charm install complete")

    def _on_config_changed(self, event):
        """Reconfigure charm."""
        # (@raychan96) Keep track of what config options are changed. This can
        # be helpful when we want to respond to the change of a specific config
        # option.
        change_set = set()
        model_config = {k: v for k, v in self.model.config.items()}
        model_config.update({"exporter-snap": self.snap_path})
        for key, value in model_config.items():
            if key not in self._stored.config or self._stored.config[key] != value:
                logger.info("Setting {} to: {}".format(key, value))
                self._stored.config[key] = value
                change_set.add(key)

        if not self._stored.installed:
            logging.info(
                "Config changed called before install complete, deferring event: "
                "{}".format(event.handle)
            )
            event.defer()
            return

        if not self.config["controllers"] or not self.config["accounts"]:
            logging.warning("missing controller connection config, blocking")
            self.model.unit.status = BlockedStatus(
                "Waiting for controllers/accounts configuration"
            )
            return

        if not self.helper.validate_config():
            logging.warning("invalid controller connection config, blocking")
            self.model.unit.status = BlockedStatus(
                "Invalid controllers/accounts configuration"
            )
            return

        logger.debug("charm is installed, and the juju config is in place")
        self.helper.create_backup_dir()
        self.helper.update_jujudata_config()
        self.helper.update_crontab()
        self.helper.exporter.config_changed(change_set, self.metrics_endpoint)
        self.model.unit.status = ActiveStatus("Unit is ready")

    def _on_nem_changed(self, event):
        """Handle nrpe-external-master relation change."""
        logging.info("Got nrpe-external-master changed {}".format(event))
        self.helper.configure_nrpe()

    def _on_exporter_relation_joined(self, event):
        """Handle metrics-endpoint relation joined."""
        logging.info("Got metrics-endpoint joined {}".format(event))
        self.helper.exporter._on_relation_joined(event)

    def _on_exporter_relation_departed(self, event):
        """Handle metrics-endpoint relation departed."""
        logging.info("Got metrics-endpoint departed {}".format(event))
        self.helper.exporter._on_relation_departed(event)

    def _configure_logging(self):
        logging.getLogger("websockets").setLevel(logging.ERROR)
        logging.getLogger("juju").setLevel(logging.ERROR)
        logging.getLogger("connector").setLevel(logging.CRITICAL)
        logging.getLogger("asyncio").setLevel(logging.CRITICAL)


if __name__ == "__main__":
    main(JujuBackupAllCharm)  # pragma: no cover
