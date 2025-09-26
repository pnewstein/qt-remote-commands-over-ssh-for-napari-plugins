"""
this code contains classes for messages between server and client and code to start the loop
"""

import sys
from dataclasses import dataclass, asdict
import json
from typing import Sequence, Literal, Callable, IO, TYPE_CHECKING
from subprocess import Popen, PIPE
from pathlib import Path
import tempfile
import secrets
import logging
import threading
import queue
import time

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
logger.debug("loading module")


if TYPE_CHECKING:
    import qtpy.QtWidgets



def to_string(dclass_instance) -> str:
    """Serialize to a JSON string"""
    return json.dumps(asdict(dclass_instance))


def from_string(DClass, string: str):
    """Deserialize from a JSON string"""
    data = json.loads(string)
    return DClass(**data)


def send_with_logging(message: str, location: IO | None = None):
    """
    sends to stdout and logs as info
    """
    if location is None:
        location = sys.stdout
    assert location is not None
    logger.info("sending message: %s", message)
    location.write(message)
    location.flush()


@dataclass(frozen=True, slots=True)
class Response:
    out: str
    error: str


def main_loop(callback: Callable[[str], Response]):
    """
    this is the main loop that takes in requests and emits responses
    callback must be a dataclass
    """
    # first initialize connection by creating a path
    while True:
        session_path = Path(tempfile.gettempdir()) / secrets.token_urlsafe(5)
        try:
            session_path.mkdir(exist_ok=False)
            break
        except FileExistsError:
            logger.warning("collided with session path %s", session_path)
            continue
    logger.warning("got session %s", session_path)
    first_response = Response(str(session_path), "")
    message = "\n" + to_string(first_response) + "\n"
    send_with_logging(message)
    # then iterate through every message recieved and respond
    for line in sys.stdin:
        line = line.strip()
        if not line:
            logger.warning("no line")
            continue
        logger.info("recieved: %s", line)
        try:
            response = callback(line)
        except Exception as e:
            # If parsing or processing fails, still emit a response with error
            response = Response(out="", error=str(e))
        message = "\n" + to_string(response) + "\n"
        send_with_logging(message)


def stdout_stderr_reader(
    proc: Popen,
    output_queue: queue.Queue[Response],
    error_queue: queue.Queue[str],
    stderr=False,
) -> None:
    """
    Continuously read lines from process in a separate thread.
    """
    logger.debug("started thread %s", stderr)
    reader = proc.stderr if stderr else proc.stdout
    assert reader is not None
    reader_name = "stderr" if stderr else "stdout"
    try:
        while True:
            try:
                line = reader.readline()
                if not line:
                    logger.info(f"EOF reached on {reader_name}")
                    break
                processed_line = line.rstrip("\n\r")
            except OSError as e:
                logger.error(f"OS error reading {reader_name}: {e}")
                break
            if stderr:
                logger.debug("adding stderr to error queue")
                error_queue.put(processed_line)
            else:
                try:
                    logger.debug("adding to output queue")
                    output_queue.put(from_string(Response, processed_line))
                except json.JSONDecodeError:
                    logger.debug("adding stdout to error queue")
                    error_queue.put(processed_line)
    except Exception as e:
        logger.error(f"Unexpected error in {reader_name} reader: {e}")


class Client:
    def __init__(
        self,
        command: Sequence[str | Path],
        error_callback: Callable[[str], None],
        timeout: float = 10,
    ):
        self.command = command
        self.proc: Popen | None = None
        self.working_path: Path | None = None
        self.output_queue: queue.Queue[Response] = queue.Queue()
        self.error_queue: queue.Queue[str] = queue.Queue()
        self.error_callback = error_callback
        self.timeout = timeout
        logger.info("running %s", command)

    def __enter__(self):
        "start the server subprocess"
        self.proc = Popen(
            self.command,
            stdin=PIPE,
            stdout=PIPE,
            stderr=PIPE,
            text=True,
            bufsize=1,
        )
        stdout_reader = threading.Thread(
            target=stdout_stderr_reader,
            args=(self.proc, self.output_queue, self.error_queue, False),
            name="stdout_reader",
            daemon=True,
        )
        stdout_reader.start()
        stderr_reader = threading.Thread(
            target=stdout_stderr_reader,
            args=(self.proc, self.output_queue, self.error_queue, True),
            name="stderr_reader",
            daemon=True,
        )
        stderr_reader.start()
        first_response = self.read_stdin(self.timeout)
        if first_response is None:
            raise RuntimeError("Connection Failed")
        self.working_path = Path(first_response.out)
        return self

    def read_stdin(self, timeout: float) -> Response | None:
        for _ in range(int(timeout * 100)):
            response: Response | None = None
            error: str | None = None
            try:
                response = self.output_queue.get(block=False)
            except queue.Empty:
                pass
            try:
                error = self.error_queue.get(block=False)
            except queue.Empty:
                pass
            if error is not None:
                self.error_callback(error)
            if response is not None:
                return response
            time.sleep(0.01)

    def request(self, req: str, timeout=None) -> Response:
        """
        does a request blocked until responce or timeout
        """
        if timeout is None:
            timeout = self.timeout
        if self.proc is None:
            raise ValueError("Uninitialized process")
        assert self.proc.stdin is not None
        assert self.proc.stdout is not None
        send_with_logging(req + "\n", self.proc.stdin)
        responce = self.read_stdin(timeout)
        if responce is None:
            raise RuntimeError("Request timed out")
        return responce

    def __exit__(self, exc_type, exc, tb):
        if self.proc is None:
            raise ValueError("Uninitialized process")
        assert self.proc.stdin is not None
        assert self.proc.stdout is not None
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            if self.proc.stdout:
                self.proc.stdout.close()
        finally:
            self.proc.terminate()
            self.proc.wait()


@dataclass
class ConnectionManager:
    """
    contains qt widgets
    """

    host_name: "qtpy.QtWidgets.QLineEdit"
    conda: "qtpy.QtWidgets.QComboBox"
    env: "qtpy.QtWidgets.QLineEdit"
    exe: "qtpy.QtWidgets.QLineEdit | str"
    label: "qtpy.QtWidgets.QLabel"
    session_id: str | None = None

    def get_args(self) -> list[str]:
        if isinstance(self.exe, str):
            exe_name = self.exe
        else:
            exe_name = self.exe.text()
        return [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            self.host_name.text(),
            exe_name,
        ]


def add_widgets(layout: "qtpy.QtWidgets.QVBoxLayout", error_callback: Callable[[str], None], exe_name="") -> ConnectionManager:
    from qtpy.QtWidgets import QLabel, QHBoxLayout, QLineEdit, QComboBox, QPushButton
    from napari.qt.threading import thread_worker

    label = QLabel("Connect to a server")
    label.setStyleSheet(
        "QLabel { qproperty-alignment: 'AlignCenter';" "font-weight: bold; }"
    )
    layout.addWidget(label)
    host_name_row = QHBoxLayout()
    host_name_row.addWidget(QLabel("Host"))
    host_name = QLineEdit()
    host_name_row.addWidget(host_name)
    layout.addLayout(host_name_row)
    conda_row = QHBoxLayout()
    conda_row.addWidget(QLabel("Conda executable"))
    conda = QComboBox()
    conda.addItems(["conda", "mamba", "micromamba"])
    conda_row.addWidget(conda)
    layout.addLayout(conda_row)
    env_row = QHBoxLayout()
    env_row.addWidget(QLabel("Environment name"))
    env = QLineEdit()
    env_row.addWidget(env)
    layout.addLayout(env_row)
    if not exe_name:
        exe_row = QHBoxLayout()
        exe_row.addWidget(QLabel("Package name"))
        exe = QLineEdit()
        exe_row.addWidget(exe)
        layout.addLayout(exe_row)
    else:
        exe = exe_name
    status = QLabel("")
    layout.addWidget(status)
    out = ConnectionManager(host_name, conda, env, exe, status)
    connect_button = QPushButton("Check connection")

    @thread_worker
    def quick_connect():
        logger.debug("started quick_connect worker")
        args = out.get_args()
        try:
            with Client(args, print) as client:
                assert client.working_path is not None
                session_id = client.working_path.name
        except Exception as e:
            logger.error("failed to start")
            out.label.setText(f"Fail: {e}")
            return
        return session_id

    def post_connect(session_id: str | None):
        logger.debug("got session id %s", session_id)
        if session_id is None:
            return
        out.session_id = session_id
        out.label.setText(f"Connected: {session_id}")

    worker = quick_connect()
    worker.returned.connect(post_connect)
    layout.addWidget(connect_button)
    def button_callback():
        logger.debug("button callback")
        out.label.setText("Connecting ...")
        worker.start()
    connect_button.clicked.connect(button_callback)
    return out
