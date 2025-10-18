from pathlib import Path
import logging

import numpy as np

from request import Request

logging.basicConfig(
    filename="server.log",
    filemode="a",
    format="%(asctime)s,%(msecs)03d %(name)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.DEBUG,
)

from qt_remote_commands_over_ssh_for_napari_plugins import (
    main_loop,
    Response,
    from_string,
)


def callback(request_str: str, path: Path) -> Response:
    request = from_string(Request, request_str)
    data: np.ndarray = np.load(path / request.path)
    out = (data**request.gamma).astype(data.dtype)
    out_path = (path / request.path).with_suffix(".out.npy")
    np.save(out_path, out)
    return Response(out_path.name, "")


main_loop(callback)
