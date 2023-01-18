from functools import wraps
from logging import getLogger
from time import sleep

from charms.operator_libs_linux.v1 import snap
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from yaml import safe_dump

from config import EXPORTER_NAME, EXPORTER_RELATION_NAME, Paths

logger = getLogger(__name__)


def check_snap_installed(func):
    """Ensure snap is installed before running a snap operation."""

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            fn = func.__name__
            self._exporter = self._exporter or snap.SnapCache()[EXPORTER_NAME]
            logger.info("%s exporter snap.", fn.capitalize())
            if not (self._exporter and self._exporter.present):
                msg = f"Cannot {fn} the exporter because it is not installed."
                raise snap.SnapNotFoundError(msg)
            func(self, *args, **kwargs)
            logger.info("%s exporter snap - Done", fn.capitalize())
        except snap.SnapNotFoundError as e:
            logger.error(str(e))
            logger.error("%s exporter snap - Failed", fn.capitalize())

    return wrapper


class Exporter(MetricsEndpointProvider):
    """A class representing the exporter and the metric endpoints."""

    def __init__(self, *args, **kwargs):
        """Initialize the class."""
        super().__init__(*args, **kwargs)
        self._exporter = None
        self._stored = self._charm._stored
        events = self._charm.on[self._relation_name]
        self.framework.observe(events.relation_joined, self._on_relation_joined)
        self.framework.observe(events.relation_departed, self._on_relation_departed)

    def install_or_refresh(self, channel=None):
        """Install or refresh the exporter snap."""
        logger.info("Installing exporter snap.")
        channel = channel or self._stored.config["exporter-channel"]
        try:
            if self._stored.config["exporter-snap"]:
                snap.install_local(self._stored.config["exporter-snap"], dangerous=True)
            else:
                snap.add([EXPORTER_NAME], channel=channel)
            logger.info("Installed exportr snap.")
        except snap.SnapError as e:
            logger.error(str(e))
        else:
            logger.info("Exporter snap installed.")

    @check_snap_installed
    def remove(self):
        """Remove the exporter snap."""
        snap.remove([EXPORTER_NAME])

    @check_snap_installed
    def start(self):
        """Start the exporter daemon."""
        self._exporter.start()

    @check_snap_installed
    def restart(self):
        """Restart the exporter daemon."""
        self._exporter.restart()

    @check_snap_installed
    def stop(self):
        """Stop the exporter daemon."""
        self._exporter.stop()

    @check_snap_installed
    def configure(self):
        """Configure exporter daemon."""
        if not (self._exporter and self._exporter.present):
            return

        with open(Paths.EXPORTER_CONFIG, "w", encoding="utf-8") as f:
            safe_dump(
                {
                    "port": self._stored.config["exporter-port"],
                    "level": "INFO",
                    "backup_path": str(Paths.EXPORTER_BACKUP_RESULTS_PATH),
                },
                f,
            )
        self._exporter.restart()

    @check_snap_installed
    def check_health(self):
        """Check the health of the exporter snap and try to recover it if needed.

        This function perform health check on exporter snap if the exporter is
        already installed and the metrics-endpoint relation is joined. If it is
        somehow stopped, we should try to restart it, if not possible we will
        set the charm to BlockedStatus to alert the users.
        """
        relation = self.model.get_relation(EXPORTER_RELATION_NAME)
        if not relation:
            return

        try:
            if self._exporter.services[EXPORTER_NAME]["active"]:
                logger.info("Exporter health check - healthy.")
            else:
                logger.warning("Exporter health check - unhealthy.")
                logger.warning(
                    "'%s' joined but the exporter is not up. Restarting...",
                    EXPORTER_RELATION_NAME,
                )
                num_retries = 3
                for i in range(1, num_retries + 1):
                    logger.warning("Restarting exporter - %d retry", i)
                    self.restart()
                    sleep(3)
                    if self._exporter.services[EXPORTER_NAME]["active"]:
                        logger.info("Exporter restarted.")
                        return
                logger.error("Failed to restart the exporter.")
        except Exception as e:
            logger.error(
                "Unknown error when trying to check exporter health: %s", str(e)
            )

    def on_config_changed(self, change_set):
        observe = set(["exporter-snap", "exporter-channel", "exporter-port"])
        if len(observe.intersection(change_set)) > 0:
            logger.info("Exported config changed")
        if "exporter-snap" in change_set or "exporter-channel" in change_set:
            self.install_or_refresh()
        if "exporter-port" in change_set:
            # Though dynamically changing static configure is not suggested, we
            # still offer the possibility to change one of them.
            self.update_scrape_job_spec(
                [
                    {
                        "static_configs": [
                            {
                                "targets": [
                                    f"*:{self._stored.config['exporter-port']}"
                                ],
                            }
                        ],
                    }
                ]
            )
            logger.info("Updated static_configs.targets")
            self.configure()

    def _on_relation_joined(self, event):
        """Start the exporter snap when relation joined."""
        self.start()

    def _on_relation_departed(self, event):
        """Remove the exporter snap when relation departed."""
        self.stop()
