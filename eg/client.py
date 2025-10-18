import logging
import sys
from pathlib import Path
from typing import Generator, TypedDict

from qtpy.QtWidgets import (
    QVBoxLayout,
    QWidget,
)

# Configure logging to file for debugging client-side operations
from qt_remote_commands_over_ssh_for_napari_plugins import (
    to_string,
)
from qt_remote_commands_over_ssh_for_napari_plugins.client import (
    ConnectionManager,
    GuiBackgroundFunction,
    Argument,
)

import napari
import napari.viewer
from napari.layers import Image
import numpy as np
from request import Request

logging.basicConfig(
    filename="client.log",
    filemode="a",
    format="%(asctime)s,%(msecs)03d %(name)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.DEBUG,
)

logger = logging.getLogger(__name__)


def error_callback(error: str):
    print(f"{error = }")


class AddImageKwargs(TypedDict):
    data: np.ndarray
    scale: np.ndarray
    translate: np.ndarray
    name: str


class RemoteGamma(QWidget):
    def __init__(self, viewer: napari.viewer.Viewer):
        super().__init__()
        self.viewer = viewer
        layout = QVBoxLayout()
        self.setLayout(layout)
        # load connection widgets
        self.cm = ConnectionManager.create(
            error_callback,
            "localhost",
            f"\"{sys.executable}\" \"{Path(__file__).parent/'server.py'}\"",
        )
        self.cm.get_gui_background_function().add_widgets(layout)
        # Create get_gui_background_function for remote_gamma
        self.gbf = GuiBackgroundFunction[AddImageKwargs].create(
            "Remote Gamma",
            self.remote_gamma_generator,
            self.post_remote_gamma,
            arguments=(
                Argument("Image", "Input image for gamma correction", Image, None),
                Argument(
                    "Gamma",
                    "Gamma value: each pixel is raised to this power",
                    float,
                    1.0,
                ),
            ),
            viewer=self.viewer,
        )
        self.gbf.add_widgets(layout)

    def remote_gamma_generator(
        self, input_layer: Image, gamma: float
    ) -> Generator[str, None, AddImageKwargs]:
        """
        Submits a request to the server
        returns data to be passed as kwargs to napari.Viewer.add_image
        """
        # Serialize the current layer's data to disk for transfer
        data = np.array(input_layer.data)
        local_path = Path("data.npy")
        yield "saving localy"
        np.save(local_path, data)
        try:
            # Acquire connection and send data to remote server
            with self.cm as client:
                yield "sending file"
                client.send_file(local_path)
                # Send processing request with gamma parameter
                request = Request(str(local_path), float(gamma))
                yield "processing data"
                response = client.request(to_string(request))
                if response.error:
                    error_callback(response.error)
                    raise RuntimeError(response.error)
                # Download processed result from remote server
                file = Path(response.out)
                yield "receiving file"
                client.receive_file(file, file)
            data = np.load(file)
            file.unlink()
        finally:
            local_path.unlink()
        # Return layer kwargs to add result to viewer on main thread
        yield "Done"
        return {
            "data": data,
            "scale": input_layer.scale,
            "translate": input_layer.translate,
            "name": "with gamma",
        }

    def post_remote_gamma(self, kwargs: AddImageKwargs):
        """
        runs in main thread after remote_gamma_generator
        """
        self.viewer.add_image(**kwargs)


viewer = napari.Viewer()
# Create viewer and add sample data for demonstration
viewer.add_image(np.random.default_rng().random((10, 10, 10)))
viewer.window.add_dock_widget(RemoteGamma(viewer))
napari.run()
