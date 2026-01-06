import logging
from enum import StrEnum, unique

logger = logging.getLogger(__name__)


__all__ = ("LogActions",)


@unique
class LogActions(StrEnum):
    CONFIG = "CONFIG"
    INIT = "INIT"
    DRY_RUN = "DRY-RUN"

    STARTED = "STARTED"
    FINISHED = "FINISHED"

    CREATED = "CREATED"
    MOVED = "MOVED"
    IDENTIFIED = "IDENTIFIED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"

    RETRYING = "RETRYING"
    WAITING = "WAITING"
