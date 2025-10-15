"""
this code contains classes for messages between server and client and code to start the loop
"""

import sys
from dataclasses import dataclass, asdict
import json
from typing import Sequence, Callable, IO, TYPE_CHECKING
from subprocess import Popen, PIPE, run
from pathlib import Path
import tempfile
import secrets
import logging
import threading
import queue
import shlex

logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    import qtpy.QtWidgets


def raise_exception(e: Exception):
    """
    raises the exception. A good errored callback for napari threads
    """
    raise e


def to_string(dclass_instance) -> str:
    """
    Convert a dataclass instance to a JSON string using `asdict`
    """
    return json.dumps(asdict(dclass_instance))


def from_string(DClass, string: str):
    """
    Reconstruct a dataclass instance of type `DClass` from a JSON string
    """
    data = json.loads(string)
    return DClass(**data)


def send_with_logging(message: str, location: IO | None = None):
    """
    Write a message to the given stream (default stdout) and log it at INFO level
    """
    if location is None:
        location = sys.stdout
    assert location is not None
    logger.info("sending message: %s", message)
    location.write(message)
    location.flush()


@dataclass(frozen=True, slots=True)
class Response:
    """
    Represents a structured response with standard output and error fields
    """

    out: str
    error: str


def main_loop(callback: Callable[[str, Path], Response]):
    """
    Main server loop: creates a temporary session directory, sends initial session info,
    then reads requests from stdin, invoking `callback(request, session_path)` and returning
    serialized `Response` objects to stdout.
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
    logger.info("got session %s", session_path)
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
            response = callback(line, session_path)
        except Exception as e:
            # If parsing or processing fails, still emit a response with error
            logger.exception("Error in callback while handling line: %s", line)
            response = Response(out="", error=str(e))
        message = "\n" + to_string(response) + "\n"
        send_with_logging(message)


def stdout_stderr_reader(
    proc: Popen,
    output_queue: queue.Queue[Response],
    error_callback: Callable[[str], None],
    stderr=False,
) -> None:
    """
    Continuously read lines from a subprocess stdout/stderr and push parsed JSON responses
    to output_queue, and calls error_callback on invalid json.
    if stderr is True, read from the process' stderr and push all responses to callback

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
                logger.debug("adding stderr to error queue: %s", processed_line)
                if processed_line.strip():
                    error_callback(processed_line)
            else:
                try:
                    logger.debug("adding to output queue: %s", processed_line)
                    output_queue.put(from_string(Response, processed_line))
                except json.JSONDecodeError:
                    logger.debug("adding stdout to error queue: %s", processed_line)
                    if processed_line.strip():
                        error_callback(processed_line)
    except Exception as e:
        logger.exception(e)


@dataclass(frozen=True, slots=True)
class Client:
    """
    Bidirectional client interface for communicating with a remote server subprocess
    """

    command: list[str | Path]
    host_name: str
    proc: Popen
    working_path: Path
    output_queue: queue.Queue[Response]
    error_callback: Callable[[str], None]
    timeout: float

    @classmethod
    def from_cmd_args(
        cls,
        ssh_args: Sequence[str],
        host: str,
        command: Sequence[str | Path],
        error_callback: Callable[[str], None],
        timeout: float = 10,
    ):
        """
        ssh_args are the args that go after 'ssh'
        host is the hostname (like localhost)
        command is the command to run on the remote computer
        timeout is the timout for the inital connection

        Start the subprocess, spawn reader threads, and wait for the initial Response being the
        remote working path
        """
        command = ["ssh"] + list(ssh_args) + [host] + list(command)
        output_queue: queue.Queue[Response] = queue.Queue()
        timeout = timeout
        logger.info("running %s", command)
        proc = Popen(
            command,
            stdin=PIPE,
            stdout=PIPE,
            stderr=PIPE,
            text=True,
            bufsize=1,
        )
        stdout_reader = threading.Thread(
            target=stdout_stderr_reader,
            args=(proc, output_queue, error_callback, False),
            name="stdout_reader",
            daemon=True,
        )
        stdout_reader.start()
        stderr_reader = threading.Thread(
            target=stdout_stderr_reader,
            args=(proc, output_queue, error_callback, True),
            name="stderr_reader",
            daemon=True,
        )
        stderr_reader.start()
        first_response = output_queue.get(timeout=timeout)
        if first_response is None:
            raise RuntimeError("Connection Failed")
        working_path = Path(first_response.out)
        return cls(
            command=command,
            host_name=host,
            proc=proc,
            working_path=working_path,
            output_queue=output_queue,
            error_callback=error_callback,
            timeout=timeout,
        )

    def is_alive(self):
        return self.proc.poll() is None

    def request(self, req: str, timeout=None) -> Response:
        """
        Send a request string to the subprocess and block until a Response is received or timeout
        """
        if timeout is None:
            timeout = self.timeout
        if not self.is_alive():
            error = RuntimeError("Process is dead")
            logging.exception(error)
            raise error
        assert self.proc.stdin is not None
        assert self.proc.stdout is not None
        send_with_logging(req + "\n", self.proc.stdin)
        response = self.output_queue.get(timeout=timeout)
        return response

    def send_file(self, local_path: Path):
        """
        Upload a file to the remote session directory using scp.
        blocking until sent
        """
        if not self.is_alive():
            error = RuntimeError("Process is dead")
            logging.exception(error)
            raise error
        remote_dir = self.working_path
        if remote_dir is None:
            raise ValueError("Uninitialized process")
        assert remote_dir is not None
        args = ["scp", local_path, f"{self.host_name}:{remote_dir}"]
        logger.info(args)
        output = run(args, check=True, text=True, capture_output=True)
        if output.stdout:
            self.error_callback(output.stdout)
        if output.stderr:
            self.error_callback(output.stderr)

    def receive_file(self, remote_path: Path, local_path: Path):
        """
        Upload a file from the remote session directory using scp.
        blocking until sent
        """
        if not self.is_alive():
            error = RuntimeError("Process is dead")
            logging.exception(error)
            raise error
        remote_dir = self.working_path
        assert remote_dir is not None
        args = ["scp", f"{self.host_name}:{remote_dir/remote_path}", local_path]
        logger.info(args)
        output = run(args, check=True, text=True, capture_output=True)
        if output.stdout:
            self.error_callback(output.stdout)
        if output.stderr:
            self.error_callback(output.stderr)

    def remote_cp(self, src_path: Path, dst_path: Path):
        """
        copys src_path to dst_path on the remote session directory using scp
        """
        if not self.is_alive():
            error = RuntimeError("Process is dead")
            logging.exception(error)
            raise error
        remote_dir = self.working_path
        assert remote_dir is not None
        args = [
            "scp",
            f"{self.host_name}:{remote_dir/src_path}",
            f"{self.host_name}:{remote_dir/dst_path}",
        ]
        logger.info(args)
        output = run(args, check=True, text=True, capture_output=True)
        if output.stdout:
            self.error_callback(output.stdout)
        if output.stderr:
            self.error_callback(output.stderr)

    def close(self):
        """
        Clean up and terminate the subprocess, closing its streams
        """
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
    Holds Qt widgets and logic for connecting to a remote session via the Client
    """

    host_name: "qtpy.QtWidgets.QLineEdit"
    exe: "qtpy.QtWidgets.QLineEdit"
    label: "qtpy.QtWidgets.QLabel"
    error_callback: Callable[[str], None]
    _lock: threading.Lock
    _session_id: str | None = None
    _client: Client | None = None

    @classmethod
    def create(
        cls,
        host_name: "qtpy.QtWidgets.QLineEdit",
        exe: "qtpy.QtWidgets.QLineEdit",
        label: "qtpy.QtWidgets.QLabel",
        error_callback: Callable[[str], None],
    ):
        return cls(host_name, exe, label, error_callback, threading.Lock())

    def set_session_id(self, session_id: str):
        """
        sets session id reflecting connection in label
        """
        self.label.setText(f"Connected: {session_id}")
        self._session_id = session_id

    def get_session_id(self):
        """
        gets the session id
        """
        return self._session_id

    def __enter__(self) -> Client:
        """
        enters the client returning the client.
        __exit__ must be called on the returned client later
        """
        if not self._lock.acquire(blocking=False):
            error = RuntimeError("Failed to acquire lock for client")
            logging.exception(error)
            raise error
        if self._client is not None and self._client.is_alive():
            return self._client
        self.label.setText("Connecting ...")
        ssh_args = [
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
        ]
        out: Client = Client.from_cmd_args(
            ssh_args,
            self.host_name.text(),
            shlex.split(self.exe.text()),
            self.error_callback,
        )
        session_id = out.working_path.name
        self.set_session_id(session_id)
        self._client = out
        return out

    def __exit__(self, *_args):
        _ = _args
        self._lock.release()


def add_widgets(
    layout: "qtpy.QtWidgets.QVBoxLayout",
    error_callback: Callable[[str], None],
    exe_name="",
) -> ConnectionManager:
    """
    Create and add connection UI elements (labels, host/exe fields, connect button)
    to a provided QVBoxLayout, returning a ConnectionManager instance
    """
    from qtpy.QtWidgets import QLabel, QHBoxLayout, QLineEdit, QPushButton
    from napari.qt.threading import thread_worker, FunctionWorker

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
    exe = QLineEdit(exe_name)
    if not exe_name:
        exe_row = QHBoxLayout()
        exe_row.addWidget(QLabel("Package name"))
        exe_row.addWidget(exe)
        layout.addLayout(exe_row)
    status = QLabel("")
    layout.addWidget(status)
    connection_manager = ConnectionManager.create(host_name, exe, status, error_callback)
    connect_button = QPushButton("Check connection")

    @thread_worker
    def quick_connect():
        with connection_manager as client:
            return client.working_path.name

    layout.addWidget(connect_button)

    def button_callback():
        worker: FunctionWorker = quick_connect()  # type: ignore
        worker.returned.connect(connection_manager.set_session_id)
        worker.start()

    connect_button.clicked.connect(button_callback)
    return connection_manager
