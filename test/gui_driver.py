import sys
from qtpy.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton

from qt_remote_commands_over_ssh_for_napari_plugins import add_widgets 

app = QApplication(sys.argv)

window = QWidget()
layout = QVBoxLayout()
window.setLayout(layout)

# call the function to add the widgets
cm = add_widgets(layout, exe_name="my_package", error_callback=print)
def button_callback():
    print(cm.get_args())

button = QPushButton()
button.setText("Run")
button.clicked.connect(button_callback)
layout.addWidget(button)

window.show()
sys.exit(app.exec_())
