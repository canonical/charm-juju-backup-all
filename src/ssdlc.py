# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""SSDLC (Secure Software Development Lifecycle) Logging.

These events provide critical visibility into the asset's lifecycle and health, and can help
detect potential tampering or malicious activities aimed at altering system behavior.

Logging these events allows for the identification of unauthorized changes to system states,
such as unapproved restarts or unexpected shutdowns, which may indicate security incidents
or availability attacks, or changes to security settings.
"""
from datetime import datetime, timezone
from enum import Enum
from logging import getLogger

logger = getLogger(__name__)


class SSDLCSysEvent(str, Enum):  # noqa: N801
    """Constant event defined in SSDLC."""

    STARTUP = "sys_startup"
    SHUTDOWN = "sys_shutdown"
    RESTART = "sys_restart"
    CRASH = "sys_crash"


_EVENT_MESSAGE_MAPS = {
    SSDLCSysEvent.STARTUP: "juju-backup-all start service %s",
    SSDLCSysEvent.SHUTDOWN: "juju-backup-all shutdown service %s",
    SSDLCSysEvent.RESTART: "juju-backup-all restart service %s",
    SSDLCSysEvent.CRASH: "juju-backup-all service %s crash",
}


class Service(str, Enum):
    """Service names for juju-backup-all charm."""

    JUJU_BACKUP_ALL_EXPORTER = "juju-backup-all-exporter"


def log_ssdlc_system_event(
    event: SSDLCSysEvent,
    service: Service = Service.JUJU_BACKUP_ALL_EXPORTER,
    msg: str = "",
):
    """Log system startup event in SSDLC required format.

    Args:
        event: The SSDLC system event type
        service: Service enum (defaults to JUJU_BACKUP_ALL_EXPORTER)
        msg: Optional additional message
    """
    event_msg = _EVENT_MESSAGE_MAPS[event].format(service)

    now = datetime.now(timezone.utc).astimezone()
    logger.warning(
        {
            "datetime": now.isoformat(),
            "appid": f"service.{service.value}",
            "event": f"{event.value}:{service.value}",
            "level": "WARN",
            "description": f"{event_msg} {msg}".strip(),
        },
    )
