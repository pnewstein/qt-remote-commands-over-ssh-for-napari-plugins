import sys
from pathlib import Path
import pytest
from qt_remote_commands_over_ssh_for_napari_plugins import Response, Client, to_string, from_string
HERE = Path("test")

HERE = Path(__file__).parent


def test_responce():
    m = Response("test", "")
    assert m == m
    n = Response("", "test")
    assert m != n
    assert to_string(m) != to_string(n)
    assert from_string(Response, to_string(m)) == m


def test_client():
    with Client([sys.executable, HERE / "server.py"], print) as client:
        assert client.working_path.exists()
        print(client.request("test1"))
        print(client.request("test"))
        print(client.request("test"))

    with pytest.raises(RuntimeError):
        with Client(["ssh", "-o", "BatchMode=yes", "-o", 'StrictHostKeyChecking=no', "localhost"], print, .1) as client:
            pass
