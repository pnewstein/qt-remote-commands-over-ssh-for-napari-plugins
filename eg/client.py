import logging
import sys
from pathlib import Path
from typing import Generator

from qtpy.QtWidgets import (
    QVBoxLayout,
    QPushButton,
    QLineEdit,
    QWidget,
    QComboBox,
    QHBoxLayout,
    QLabel,
)
from qtpy.QtGui import QDoubleValidator

# Configure logging to file for debugging client-side operations
from qt_remote_commands_over_ssh_for_napari_plugins import (
    add_widgets,
    raise_exception,
    to_string,
)

import napari
import napari.viewer
from napari.layers import Image
from napari.qt.threading import thread_worker, GeneratorWorker
import numpy as np
from request import Request

logging.basicConfig(
    filename="client.log",
    filemode="a",
    format="%(asctime)s,%(msecs)03d %(name)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.DEBUG,
)


def error_callback(error: str):
    print(f"{error = }")


class RemoteGamma(QWidget):
    def __init__(self, viewer: napari.viewer.Viewer):
        super().__init__()
        # Listen for layer changes to keep dropdown in sync with available images
        viewer.layers.events.inserted.connect(self.reset_image_box)
        viewer.layers.events.removed.connect(self.reset_image_box)
        self.viewer = viewer
        layout = QVBoxLayout()
        # load connection widgets
        self.cm = add_widgets(layout, error_callback)
        # Configure connection to local server running the processing script
        self.cm.host_name.setText("localhost")
        self.cm.exe.setText(
            f"{sys.executable} \"{Path(__file__).parent/'server.py'}\""
        )
        # add a dropdown to pick an image to process
        self.setLayout(layout)
        image_box_row = QHBoxLayout()
        image_box_row.addWidget(QLabel("Channel to process"))
        self.image_box = QComboBox()
        image_box_row.addWidget(self.image_box)
        layout.addLayout(image_box_row)
        self.reset_image_box()
        # add a gamma box
        gamma_row = QHBoxLayout()
        gamma_row.addWidget(QLabel("Gamma"))
        self.gamma = QLineEdit()
        self.gamma.setValidator(QDoubleValidator())
        self.gamma.setText("2")
        gamma_row.addWidget(self.gamma)
        layout.addLayout(gamma_row)
        # add a submit button
        self.submit_button = QPushButton("Submit Coords")
        self.submit_button.clicked.connect(self.submit)
        layout.addWidget(self.submit_button)
        self.status = QLabel("")
        layout.addWidget(self.status)

    def reset_image_box(self):
        """
        resets a combo box to a new set of values
        """
        # Preserve user's selection if the layer still exists
        old_value = self.image_box.currentText()
        self.image_box.clear()
        # Filter to only Image layers (excludes Points, Shapes, etc. use set to avoid repeat
        values = set(
            l.name for l in self.viewer.layers if isinstance(l, Image)
        )
        self.image_box.addItems(list(values))
        if old_value in values:
            self.image_box.setCurrentText(old_value)

    def update_status(self, text: str):
        """
        updates the status bar. takes yielded values
        """
        self.status.setText(text)

    @thread_worker
    def submit_thread(self) -> Generator[str, None, dict]:
        """
        Submits a request to the server
        returns data to be passed as kwargs to napari.Viewer.add_image
        """
        # Serialize the current layer's data to disk for transfer
        input_layer = self.viewer.layers[self.image_box.currentText()]
        data = input_layer.data

        local_path = Path("data.npy")
        yield "saving localy"
        np.save(local_path, data)
        try:
            # Acquire connection and send data to remote server
            with self.cm as client:
                yield "sending file"
                client.send_file(local_path)
                # Send processing request with gamma parameter
                request = Request(str(local_path), float(self.gamma.text()))
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
        return {
            "data": data,
            "scale": input_layer.scale,
            "translate": input_layer.translate,
            "name": "with gamma",
        }

    def post_submit(self, add_image_kwargs: dict):
        """
        turn back on the submit button and add image
        """
        self.status.setText("Done")
        viewer.add_image(**add_image_kwargs)

    def reset_button(self):
        """
        Cleans up the button
        """
        self.submit_button.setChecked(False)
        self.submit_button.setEnabled(True)

    def submit(self, *args):
        _ = args
        # turn off the button
        self.submit_button.setChecked(True)
        self.submit_button.setEnabled(False)
        # Start processing in background thread to keep UI responsive
        worker: GeneratorWorker = self.submit_thread()  # type: ignore
        # add the progress indicators
        worker.yielded.connect(self.update_status)
        # Add result as new layer when processing completes
        worker.returned.connect(self.post_submit)
        # Add result as new layer when processing completes
        worker.finished.connect(self.reset_button) # type: ignore
        # Re-raise exceptions on main thread for visibility
        worker.errored.connect(raise_exception)
        worker.start()

    def closeEvent(self, a0):
        """Clean up client connection when widget is closed"""
        if self.cm._client:
            self.cm._client.close()
        super().closeEvent(a0)


viewer = napari.Viewer()
# Create viewer and add sample data for demonstration
viewer.add_image(np.random.default_rng().random((10, 10, 10)))
viewer.window.add_dock_widget(RemoteGamma(viewer))
napari.run()
