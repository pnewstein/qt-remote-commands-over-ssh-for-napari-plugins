"""
contains code common to client and server
"""

import sys
import logging
import json
from dataclasses import dataclass
from typing import IO

logger = logging.getLogger(__name__)


def send_with_logging(message: str, location: IO | None = None):
    """
    Write a message to the given stream (default stdout) and log it at INFO level
    """
    if location is None:
        location = sys.stdout
    assert location is not None
    logger.info("sending message: %s", message)
    location.write(message)
    location.flush()


@dataclass(frozen=True, slots=True)
class Response:
    """
    Represents a structured response with standard output and error fields
    """

    out: str
    error: str


def from_string(DClass, string: str):
    """
    Reconstruct a dataclass instance of type `DClass` from a JSON string
    """
    data = json.loads(string)
    return DClass(**data)
