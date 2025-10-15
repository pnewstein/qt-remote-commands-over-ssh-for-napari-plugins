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

from qtpy.QtWidgets import QLineEdit, QLabel, QApplication

logging.basicConfig(
    filename="client.log",
    filemode="a",
    format="%(asctime)s,%(msecs)03d %(name)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.DEBUG,
)

from qt_remote_commands_over_ssh_for_napari_plugins import (
    Response,
    Client,
    ConnectionManager,
    to_string,
    from_string,
    send_with_logging,
    stdout_stderr_reader,
    add_widgets
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
    _ =  QApplication.instance() or QApplication(sys.argv)
    error_strings: list[str] = []
    def callback(value: str):
        error_strings.append(value)
    
    host_name = QLineEdit("localhost")
    exe = QLineEdit(f"{sys.executable} {str(HERE / 'server.py')}")
    label = QLabel()
    
    manager = ConnectionManager.create(host_name, exe, label, callback)
    
    # Test that manager can be entered and returns a client
    with manager as client:
        assert client is not None
        assert client.working_path is not None
        assert client.working_path.exists()
        wp = client.working_path
        assert "Connected:" in label.text()
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
            time.sleep(.05)
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


def test_add_widgets():
    from qtpy.QtWidgets import QVBoxLayout, QWidget, QApplication
    import sys
    
    # Qt requires an application instance
    _ = QApplication.instance() or QApplication(sys.argv)
    
    error_strings: list[str] = []
    def callback(value: str):
        error_strings.append(value)
    
    # Create a layout to add widgets to
    widget = QWidget()
    layout = QVBoxLayout()
    widget.setLayout(layout)
    
    # Test with exe_name provided
    manager = add_widgets(
        layout, 
        callback, 
        exe_name=f"{sys.executable} {str(HERE / 'server.py')}"
    )
    
    assert isinstance(manager, ConnectionManager)
    assert manager.host_name.text() == ""  # Default empty
    assert manager.exe.text() == f"{sys.executable} {str(HERE / 'server.py')}"
    
    # Set hostname and test connection
    manager.host_name.setText("localhost")
    
    with manager as client:
        assert client is not None
        assert client.working_path is not None
        assert "Connected:" in manager.label.text()
    
    # Test without exe_name (should show exe input field)
    layout2 = QVBoxLayout()
    widget2 = QWidget()
    widget2.setLayout(layout2)
    
    manager2 = add_widgets(layout2, callback, exe_name="")
    assert manager2.exe.text() == ""
    
    # Cleanup
    if manager._client:
        manager._client.close()
