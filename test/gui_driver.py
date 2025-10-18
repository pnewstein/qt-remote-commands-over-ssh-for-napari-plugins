from pathlib import Path
import sys
import logging

logging.basicConfig(
    filename="log.jsonl",
    filemode="a",
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s %(module)s:%(lineno)d %(process)d %(threadName)s %(message)s",
)
from qtpy.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton

from qt_remote_commands_over_ssh_for_napari_plugins.client import ConnectionManager


app = QApplication(sys.argv)

window = QWidget()
layout = QVBoxLayout()
window.setLayout(layout)

# call the function to add the widgets
cm = ConnectionManager.create(
    print,
    "localhost",
    " ".join((sys.executable, str(Path(__file__).parent / "server.py"))),
)
cm.get_gui_background_function().add_widgets(layout)

# cm = add_widgets(layout, exe_name="bin/align-server", error_callback=print)
# cm.host_name.setText("localhost")


# def button_callback():
# print(cm.get_args())


# button = QPushButton()
# button.setText("Run")
# button.clicked.connect(button_callback)
# layout.addWidget(button)

window.show()
sys.exit(app.exec_())
