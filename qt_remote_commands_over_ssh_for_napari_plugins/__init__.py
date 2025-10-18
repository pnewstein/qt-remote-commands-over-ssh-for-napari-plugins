"""
this code contains classes for messages between server and client and code to start the loop
"""

import sys
from dataclasses import asdict
import json
from typing import Callable
from pathlib import Path
import tempfile
import secrets
import logging
from .common import Response, send_with_logging, from_string

__all__ = ["send_with_logging", "from_string", "to_string", "main_loop"]


logger = logging.getLogger(__name__)


def to_string(dclass_instance) -> str:
    """
    Convert a dataclass instance to a JSON string using `asdict`
    """
    return json.dumps(asdict(dclass_instance))


def main_loop(callback: Callable[[str, Path], Response]):
    """
    Main server loop: creates a temporary session directory, sends initial session info,
    then reads requests from stdin, invoking `callback(request, session_path)` and returning
    serialized `Response` objects to stdout.
    """
    # first initialize connection by creating a path
    while True:
        session_path = Path(tempfile.gettempdir()) / secrets.token_urlsafe(5)
        try:
            session_path.mkdir(exist_ok=False)
            break
        except FileExistsError:
            logger.warning("collided with session path %s", session_path)
            continue
    logger.info("got session %s", session_path)
    first_response = Response(str(session_path), "")
    message = "\n" + to_string(first_response) + "\n"
    send_with_logging(message)
    # then iterate through every message recieved and respond
    for line in sys.stdin:
        line = line.strip()
        if not line:
            logger.warning("no line")
            continue
        logger.info("recieved: %s", line)
        try:
            response = callback(line, session_path)
        except Exception as e:
            # If parsing or processing fails, still emit a response with error
            logger.exception("Error in callback while handling line: %s", line)
            response = Response(out="", error=str(e))
        message = "\n" + to_string(response) + "\n"
        send_with_logging(message)
