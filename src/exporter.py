from functools import wraps
from logging import getLogger

from charms.operator_libs_linux.v1 import snap
from yaml import safe_dump

from config import Paths

logger = getLogger(__name__)


class Exporter(object):
    """A class representing the exporter snap."""

    def __init__(self, stored):
        """Initialize the class."""
        self._stored = stored
        self._exporter = None

    @staticmethod
    def _ensure_installed(func):
        """Ensure snap is installed before running a snap operation."""

        @wraps(func)
        def wrapper(self, *args, **kwargs):
            fn = func.__name__
            self._exporter = self._exporter or snap.SnapCache()[Paths.EXPORTER_NAME]
            logger.info("%s exporter snap.", fn.capitalize())
            if not (self._exporter and self._exporter.present):
                msg = "Cannot %s the exporter because it is not installed."
                logger.error(msg, fn)
                logger.error("%s exporter snap - Failed", fn.capitalize())
                return
            func(self, *args, **kwargs)
            logger.info("%s exporter snap - Done", fn.capitalize())

        return wrapper

    def install_or_refresh(self, channel=None):
        """Install or refresh the exporter snap."""
        logger.info("Installing exporter snap.")
        channel = channel or self._stored.config["exporter-channel"]
        if self._stored.config["exporter-snap"]:
            snap.install_local(self._stored.config["exporter-snap"], dangerous=True)
        else:
            snap.add([Paths.EXPORTER_NAME], channel=channel)
        logger.info("Exporter snap installed.")

    @_ensure_installed
    def remove(self):
        """Remove the exporter snap."""
        snap.remove([Paths.EXPORTER_NAME])

    @_ensure_installed
    def start(self):
        """Start the exporter daemon."""
        self._exporter.start()

    @_ensure_installed
    def restart(self):
        """Restart the exporter daemon."""
        self._exporter.restart()

    @_ensure_installed
    def stop(self):
        """Stop the exporter daemon."""
        self._exporter.stop()

    @_ensure_installed
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

    def config_changed(self, change_set, metrics_endpoint):
        if len(change_set) > 0:
            logger.info("Exported config changed")
        if "exporter-snap" in change_set or "exporter-channel" in change_set:
            self.install_or_refresh()
        if "exporter-port" in change_set:
            # (@raychan96) Though dynamically changing static configure is not
            # suggested, we still offer the possibility to change one of them.
            metrics_endpoint.update_scrape_job_spec(
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
