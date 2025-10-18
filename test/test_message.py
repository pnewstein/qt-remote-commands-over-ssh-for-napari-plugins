import sys
from pathlib import Path
import pytest
import io
import queue
from types import SimpleNamespace
import logging
import threading
import time
import sys

from qtpy.QtWidgets import QApplication, QWidget, QVBoxLayout, QComboBox, QLineEdit
from napari.layers import Image
import numpy as np

logging.basicConfig(
    filename="client.log",
    filemode="a",
    format="%(asctime)s,%(msecs)03d %(name)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.DEBUG,
)

from qt_remote_commands_over_ssh_for_napari_plugins import (
    Response,
    to_string,
    from_string,
    send_with_logging,
)
from qt_remote_commands_over_ssh_for_napari_plugins.client import (
    Client,
    ConnectionManager,
    stdout_stderr_reader,
    Argument,
    get_value_from_widget,
    GuiBackgroundFunction,
)

HERE = Path(__file__).parent


def test_responce():
    m = Response("test", "")
    assert m == m
    n = Response("", "test")
    assert m != n
    assert to_string(m) != to_string(n)
    assert from_string(Response, to_string(m)) == m


def test_send_with_logging_writes():
    buf = io.StringIO()
    send_with_logging("hi", buf)
    buf.seek(0)
    assert "hi" in buf.read()


def test_reader():
    qout = queue.Queue()
    error_strings: list[str] = []

    def callback(value: str):
        error_strings.append(value)

    fake_proc = SimpleNamespace(stdout=io.StringIO("not json\n"), stderr=None)
    stdout_stderr_reader(fake_proc, qout, callback)
    assert "not json" in error_strings
    fake_proc2 = SimpleNamespace(stderr=io.StringIO("not even json\n"), stdout=None)
    stdout_stderr_reader(fake_proc2, qout, callback, True)
    assert "not even json" in error_strings


def test_client():
    error_strings: list[str] = []

    def callback(value: str):
        error_strings.append(value)

    client = Client.from_cmd_args(
        [], "localhost", [sys.executable, str(HERE / "server.py")], callback
    )
    try:
        assert len(error_strings) == 1
        assert error_strings.pop(0) == "loading server"
        assert client.working_path.exists()
        assert not client.request("test1").error
        assert len(error_strings) == 1
        assert error_strings.pop(0) == "callback"
        assert not client.request("error").out
        time.sleep(0.05)
        assert len(error_strings) == 2
        assert set(error_strings) == set(["callback", "hit error"])
        local_path = Path("test.txt")
        local_path.write_text("test")
        client.send_file(local_path)
        dst_path = Path("test1.txt")
        client.remote_cp(local_path, dst_path)
        client.receive_file(dst_path, dst_path)
        assert dst_path.read_text() == "test"
        local_path.unlink()
        dst_path.unlink()
    finally:
        client.close()

    with pytest.raises(queue.Empty):
        Client.from_cmd_args([], "localhost", [], print, timeout=0.1)


def test_client_manager():
    _ = QApplication.instance() or QApplication(sys.argv)
    error_strings: list[str] = []

    def callback(value: str):
        error_strings.append(value)

    manager = ConnectionManager.create(
        callback, "localhost", (f"{sys.executable} {str(HERE / 'server.py')}")
    )

    # Test that manager can be entered and returns a client
    with manager as client:
        assert client is not None
        assert client.working_path is not None
        assert client.working_path.exists()
        wp = client.working_path
        response = client.request("test1")
        assert not response.error

    # Test that client persists and can be reused
    with manager as client2:
        assert client2 is client  # Same client instance reused
        assert client2.is_alive()
        assert wp == client.working_path
        response = client2.request("test1")
        assert not response.error

    # Test thread safety - only one thread at a time
    results = []

    def worker():
        with manager as client:
            time.sleep(0.05)
            results.append(client.request("test1"))

    thread = threading.Thread(target=worker)
    thread.start()
    with pytest.raises(RuntimeError):
        with manager as client:
            pass
    thread.join()
    assert len(results) == 1
    assert all(not r.error for r in results)

    # Cleanup
    if manager._client:
        manager._client.close()


def test_get_value_from_widget_dropdown():
    _ = QApplication.instance() or QApplication(sys.argv)
    image = Image(data=np.arange(100).reshape((10, 10)))
    layer_name = "test"
    viewer = SimpleNamespace(layers={layer_name: image})
    widget = QComboBox()
    widget.addItems([layer_name])
    widget.setCurrentText(layer_name)
    arg = Argument("", "", Image, None)
    value = get_value_from_widget(widget, arg, viewer)
    assert value is image


def test_get_value_from_widget_line():
    _ = QApplication.instance() or QApplication(sys.argv)
    widget = QLineEdit("100.0")
    arg = Argument("", "", float, None)
    value = get_value_from_widget(widget, arg, None)
    assert value == 100.0


def test_get_args():
    _ = QApplication.instance() or QApplication(sys.argv)

    def test_gen(*args):
        yield "test"
        return "test2"

    gbf = GuiBackgroundFunction[str].create(
        "",
        test_gen,
        print,
        (
            Argument("", "", float, 0.1),
            Argument("", "", int, 2),
            Argument("", "", str, "three"),
        ),
        None,
    )
    assert (0.1, 2, "three") == gbf.get_values()


def test_function_works():
    _ = QApplication.instance() or QApplication(sys.argv)
    zero_collection = list[int]()

    def test_gen(*args):
        yield "test"
        return 0

    def return_callback(zero: int):
        zero_collection.append(zero)

    gbf = GuiBackgroundFunction[int].create(
        "",
        test_gen,
        return_callback,
        (
            Argument("", "", float, 0.1),
            Argument("", "", int, 2),
            Argument("", "", str, "three"),
        ),
        None,
    )
    assert not tuple(zero_collection)
    gbf.run_blocking()
    assert tuple(zero_collection) == (0,)


def test_add_widgets():
    _ = QApplication.instance() or QApplication(sys.argv)
    error_strings: list[str] = []

    def callback(value: str):
        error_strings.append(value)

    # Create a layout to add widgets to
    widget = QWidget()
    layout = QVBoxLayout()
    widget.setLayout(layout)

    manager = ConnectionManager.create(
        callback, "localhost", (f"{sys.executable} {str(HERE / 'server.py')}")
    )

    # Test with exe_name provided
    manager.get_gui_background_function().add_widgets(layout)

    assert isinstance(manager, ConnectionManager)

    with manager as client:
        assert client is not None
        assert client.host_name == "localhost"
        assert tuple(client.command) == (sys.executable, str(HERE / "server.py"))
        assert client.working_path is not None

    # Cleanup
    if manager._client:
        manager._client.close()
