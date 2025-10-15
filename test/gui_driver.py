import sys
import logging

logging.basicConfig(
    filename="log.jsonl",
    filemode="a",
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s %(module)s:%(lineno)d %(process)d %(threadName)s %(message)s",
)
from qtpy.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton

from qt_remote_commands_over_ssh_for_napari_plugins import add_widgets


app = QApplication(sys.argv)

window = QWidget()
layout = QVBoxLayout()
window.setLayout(layout)

# call the function to add the widgets
cm = add_widgets(layout, exe_name="bin/align-server", error_callback=print)
cm.host_name.setText("localhost")


def button_callback():
    print(cm.get_args())


button = QPushButton()
button.setText("Run")
button.clicked.connect(button_callback)
layout.addWidget(button)

window.show()
sys.exit(app.exec_())
