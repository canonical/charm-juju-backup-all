#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus

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
        self.helper = JujuBackupAllHelper(self.model)
        self._configure_logging()

        self._stored.set_default(installed=False)

    def _on_do_backup_action(self, event):
        """Handle the dobackup action."""
        backup_results = self.helper.perform_backup()
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
        self.model.unit.status = ActiveStatus("Unit is ready")

    def _on_nem_changed(self, event):
        """Handle nrpe-external-master relation change."""
        logging.info("Got nrpe-external-master changed {}".format(event))
        self.helper.configure_nrpe()

    def _configure_logging(self):
        logging.getLogger("websockets").setLevel(logging.ERROR)
        logging.getLogger("juju").setLevel(logging.ERROR)
        logging.getLogger("connector").setLevel(logging.CRITICAL)
        logging.getLogger("asyncio").setLevel(logging.CRITICAL)


if __name__ == "__main__":
    main(JujuBackupAllCharm)  # pragma: no cover
