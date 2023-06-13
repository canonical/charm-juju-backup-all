import logging
import re

import zaza.model as model
from juju.utils import juju_config_dir


def get_controller_name(controllers_file_contents):
    """Obtain the current controller name from controllers.yaml."""
    controller_name = re.findall(
        r"current-controller:\s(.*)$", controllers_file_contents
    )[0]
    logging.info(f"Current controller name: {controller_name}")
    return controller_name


def set_configuration():
    """Configure juju-backup-all and etcd charms."""
    app_name = "juju-backup-all"
    unit_name = "juju-backup-all/0"

    controllers_file = juju_config_dir() + "/controllers.yaml"
    accounts_file = juju_config_dir() + "/accounts.yaml"

    with open(controllers_file) as controllers:
        controllers_file_contents = controllers.read()
    with open(accounts_file) as accounts:
        accounts_file_contents = accounts.read()
    logging.info(f"Controllers file contents:\n{controllers_file_contents}")
    logging.info(f"Accounts file contents:\n{accounts_file_contents}")
    controller_name = get_controller_name(controllers_file_contents)

    model.set_application_config(
        app_name,
        {
            "accounts": accounts_file_contents,
            "controllers": controllers_file_contents,
            "controller-names": controller_name,
        },
    )

    model.block_until_unit_wl_status(unit_name, "active")
    model.block_until_all_units_idle()
