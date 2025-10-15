from pathlib import Path
import logging

logging.basicConfig(
    filename="server.log",
    filemode="a",
    format="%(asctime)s,%(msecs)03d %(name)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.DEBUG,
)
import sys

from qt_remote_commands_over_ssh_for_napari_plugins import main_loop, Response

print("loading server")


def callback(request: str, path: Path) -> Response:
    print("callback")
    if request == "error":
        print("hit error", file=sys.stderr)
        return Response("", "ERROR")
    return Response(str(path / request), "")


main_loop(callback)
