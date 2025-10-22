"""
Code for running a client
"""

from dataclasses import dataclass
import json
from typing import Sequence, Callable, Any, TypeVar, Generic, cast, Generator
from types import FunctionType
from subprocess import Popen, PIPE, run
from pathlib import Path
import logging
import threading
import queue
import shlex


from .common import Response, send_with_logging, from_string

try:
    from napari.layers import (
        Image,
        Points,
        Labels,
        Shapes,
        Tracks,
        Surface,
        Vectors,
        Layer,
    )
    from qtpy.QtCore import Qt
    from qtpy.QtWidgets import (
        QLabel,
        QHBoxLayout,
        QLineEdit,
        QPushButton,
        QComboBox,
        QVBoxLayout,
    )
    from qtpy.QtGui import QDoubleValidator, QIntValidator
    import napari
    from napari.qt.threading import (
        GeneratorWorker,
        create_worker,
    )

except ImportError as e:
    raise ImportError("install optional client dependencies") from e

T = TypeVar("T")
Value = TypeVar(
    "Value", float, int, str, Image, Points, Labels, Shapes, Tracks, Surface, Vectors
)
logger = logging.getLogger(__name__)


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

    Args:
        proc: The subprocess to monitor
        output_queue: Queue for parsed JSON responses
        error_callback: Function to call on error messages
        stderr: Whether to read from stderr instead of stdout
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

    Attributes:
        command: Command used to start the remote process
        host_name: Remote host name
        proc: Subprocess handle
        working_path: Remote working directory
        output_queue: Queue for process responses
        error_callback: Function to handle error messages
        timeout: Default timeout for operations
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
        timeout is the timeout for the initial connection

        Start the subprocess, spawn reader threads, and wait for the initial Response being the
        remote working path
        """
        cmd_list = list(command)
        command_with_ssh = ["ssh"] + list(ssh_args) + [host] + cmd_list
        output_queue: queue.Queue[Response] = queue.Queue()
        logger.info("running %s", command_with_ssh)
        proc = Popen(
            command_with_ssh,
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
        if first_response is None or first_response.error:
            raise RuntimeError("Connection Failed")
        working_path = Path(first_response.out)
        return cls(
            command=cmd_list,
            host_name=host,
            proc=proc,
            working_path=working_path,
            output_queue=output_queue,
            error_callback=error_callback,
            timeout=timeout,
        )

    def is_alive(self):
        """Check if the remote process is still running"""
        return self.proc.poll() is None

    def request(self, req: str, timeout=None) -> Response:
        """
        Send a request string to the subprocess and block until a Response is received or timeout

        Args:
            req: Request string to send
            timeout: Optional timeout override

        Returns:
            Response from server

        Raises:
            RuntimeError: If process is dead
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

        Args:
            local_path: Local file path to upload

        Raises:
            RuntimeError: If process is dead
            ValueError: If working directory is uninitialized
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

        Args:
            remote_path: Remote file path to download relative to working_path
            local_path: Local destination path

        Raises:
            RuntimeError: If process is dead
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
        copies src_path to dst_path on the remote session directory using scp

        Args:
            src_path: Source path on remote relative to working path
            dst_path: Destination path on remote, relative to working_path
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


@dataclass(frozen=True, slots=True)
class Argument(Generic[Value]):
    """
    Describes a parameter for background functions with metadata and validation

    Attributes:
        short_description: Brief parameter description, displayed as label
        long_description: Detailed parameter documentation, displayed on hover
        value_type: Expected data type
        default: Optional default value
    """

    short_description: str
    long_description: str
    value_type: type[Value]
    default: Value | None


def get_value_from_widget(
    widget: QComboBox | QLineEdit,
    argument: Argument[Value],
    viewer: napari.Viewer | None,
) -> Value:
    """
    get value from widget
    getting appropriate layer from viewer if Value is a subclass of Layer and
    widget is a QComboBox

    Args:
        widget: Qt widget to extract value from
        argument: Argument specification for type conversion
        viewer: Optional napari viewer for layer access

    Returns:
        Converted value of specified type
    """
    if isinstance(widget, QComboBox):
        assert issubclass(argument.value_type, Layer)
        text = widget.currentText()
        if viewer is None:
            raise ValueError(
                "Its not possible to get value from combo boxes without a viewer"
            )
        # relies on update_combo_box to ensure type
        return cast(Value, viewer.layers[text])
    text = widget.text()
    if argument.value_type is float:
        return cast(Value, float(text))
    if argument.value_type is int:
        return cast(Value, int(text))
    if argument.value_type is str:
        return cast(Value, text)
    raise ValueError(f"Unsupported type: {argument.value_type}")


def make_reset_box_callback(
    layer_type: type[Layer], widget: QComboBox, viewer: napari.Viewer
) -> Callable[[], None]:
    """
    Create callback to refresh layer dropdown when layers change

    Args:
        layer_type: Type of napari layers to include
        widget: ComboBox to update
        viewer: napari viewer instance

    Returns:
        Callback function that updates dropdown items
    """

    def inner():
        # Preserve user's selection if the layer still exists
        old_value = widget.currentText()
        widget.clear()
        # Filter to only layer_type
        values = set(l.name for l in viewer.layers if isinstance(l, layer_type))
        widget.addItems(sorted(values))
        if old_value in values:
            widget.setCurrentText(old_value)
        elif values:
            widget.setCurrentIndex(0)

    return inner


@dataclass(frozen=True)
class GuiBackgroundFunction(Generic[T]):
    """
    Manages background execution with GUI integration for long-running operations

    Attributes:
        name: Function display name
        background_callback: Generator function for background execution
        returned_callback: Function to handle completion
        arguments: Parameter definitions
        viewer: Optional napari viewer
        submit_button: Qt button to trigger execution
        status: Qt label for status updates
    """

    name: str
    background_callback: Callable[..., Generator[str, None, T]]
    returned_callback: Callable[[T], None]
    arguments: tuple[Argument[Any], ...]
    viewer: napari.Viewer | None
    submit_button: QPushButton
    status: QLabel
    _arg_widgets: dict[Argument[Any], QComboBox | QLineEdit]

    @classmethod
    def create(
        cls,
        name: str,
        background_callback: Callable[..., Generator[str, None, T]],
        returned_callback: Callable[[T], None],
        arguments: tuple[Argument[Any], ...],
        viewer: napari.Viewer | None,
    ):
        """
        creates widgets from all of the args and adds callbacks to the button

        Args:
            name: Function display name
            background_callback: Generator function for background execution
            returned_callback: Function to handle completion
            arguments: Parameter definitions
            viewer: Optional napari viewer

        Returns:
            Configured GuiBackgroundFunction instance
        """
        arg_widgets = {}
        for arg in arguments:
            if issubclass(arg.value_type, Layer):
                widget: QComboBox | QLineEdit = QComboBox()
                layers_changed_callback = make_reset_box_callback(
                    arg.value_type, widget, viewer
                )
                if viewer is None:
                    raise ValueError("you cannot have Layer args without a viewer")
                viewer.layers.events.inserted.connect(layers_changed_callback)
                viewer.layers.events.removed.connect(layers_changed_callback)
                layers_changed_callback()
            else:
                widget = QLineEdit()
                if arg.default is not None:
                    widget.setText(str(arg.default))
                if arg.value_type is int:
                    widget.setValidator(QIntValidator())
                if arg.value_type is float:
                    widget.setValidator(QDoubleValidator())
            arg_widgets[arg] = widget
        submit = QPushButton("Submit")
        status = QLabel("")
        status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        status.setStyleSheet("QLabel { qproperty-alignment: 'AlignCenter'; }")
        out = cls(
            name,
            background_callback,
            returned_callback,
            arguments,
            viewer,
            submit,
            status,
            arg_widgets,
        )
        out.submit_button.clicked.connect(out.submit)
        return out

    def add_widgets(
        self,
        layout: QVBoxLayout,
    ):
        """
        Add all GUI elements to the provided layout

        Args:
            layout: Qt layout to add widgets to
        """
        label = QLabel(self.name)
        label.setStyleSheet(
            "QLabel { qproperty-alignment: 'AlignCenter'; font-weight: bold; }"
        )
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(label)
        for arg, widget in self._arg_widgets.items():
            row = QHBoxLayout()
            label = QLabel(arg.short_description)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setToolTip(arg.long_description)
            row.addWidget(label)
            row.addWidget(widget)
            layout.addLayout(row)
        layout.addWidget(self.submit_button)
        layout.addWidget(self.status)

    def get_values(self) -> tuple:
        """
        gets the values for each of the args
        returns the args to background_callback
        the return type is tuple[arg.value_type for arg in self.arguments]

        Args:
            arg: Argument to retrieve value for

        Returns:
            Converted value of specified type
        """
        return tuple(
            get_value_from_widget(self._arg_widgets[arg], arg, self.viewer)
            for arg in self.arguments
        )

    def _reset_button(self, *args):
        """
        Cleans up the button
        """
        _ = args
        self.submit_button.setChecked(False)
        self.submit_button.setEnabled(True)

    def submit(self, *args):
        """
        callback for clicking the submit button
        Starts background execution with current parameter values
        """
        _ = args
        # turn off the button
        self.submit_button.setChecked(True)
        self.submit_button.setEnabled(False)
        worker = create_worker(
            cast(FunctionType, self.background_callback), *self.get_values()
        )
        assert isinstance(worker, GeneratorWorker)
        worker.yielded.connect(self.status.setText)
        worker.returned.connect(self.returned_callback)
        worker.finished.connect(self._reset_button)
        worker.start()

    def run_blocking(self):
        """
        Execute the function synchronously in the current thread
        """
        iterator = self.background_callback(*self.get_values())
        try:
            while True:
                self.status.setText(next(iterator))
        except StopIteration as e:
            self.returned_callback(e.value)


@dataclass(frozen=True, slots=True)
class ConnectOut:
    """
    Result container for connection attempts

    Attributes:
        host_name: Connected host name
        exe: Remote command executed
        session_id: Remote session identifier
        client: Connected client instance
    """

    host_name: str
    exe: str
    session_id: str
    client: Client


@dataclass
class ConnectionManager:
    """
    Holds Qt widgets and logic for connecting to a remote session via the Client

    Attributes:
        error_callback: Function to handle connection errors
        _lock: Thread lock for client access
        _client: Current client instance
        _gui_background_function: GUI for connection setup
    """

    error_callback: Callable[[str], None]
    _lock: threading.Lock
    _client: Client | None
    _gui_background_function: GuiBackgroundFunction[ConnectOut] | None

    @classmethod
    def create(
        cls,
        error_callback: Callable[[str], None],
        default_hostname: str,
        default_exe: str,
    ):
        """
        Create connection manager with default values

        Args:
            error_callback: Function to handle connection errors
            default_hostname: Default SSH hostname
            default_exe: Default remote command

        Returns:
            Configured ConnectionManager instance
        """
        instance = cls(error_callback, threading.Lock(), None, None)
        instance._gui_background_function = GuiBackgroundFunction[ConnectOut].create(
            "Connect to a server",
            background_callback=instance.get_connect_out,
            returned_callback=instance.returned_callback,
            arguments=(
                Argument("Host name", "eg `ssh <host_name>`", str, default_hostname),
                Argument("Exe", "Command run on remote computer", str, default_exe),
            ),
            viewer=None,
        )
        return instance

    def returned_callback(self, connect_out: ConnectOut):
        """
        Handle successful connection result

        Args:
            connect_out: Connection result data
        """
        self._client = connect_out.client

    def get_gui_background_function(self) -> GuiBackgroundFunction[ConnectOut]:
        """
        Get the connection GUI function

        Returns:
            GUI function for connection setup
        """
        assert self._gui_background_function is not None
        return self._gui_background_function

    def get_connect_out(
        self, host_name: str, exe: str
    ) -> Generator[str, None, ConnectOut]:
        """
        Establish connection to remote server with progress updates

        Args:
            host_name: SSH hostname to connect to
            exe: Command to execute remotely

        Yields:
            Status messages during connection

        Returns:
            Connection result data
        """
        yield "Connecting"
        ssh_args = [
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
        ]
        client = Client.from_cmd_args(
            ssh_args,
            host_name,
            shlex.split(exe),
            self.error_callback,
        )
        session_id = client.working_path.name
        yield f"Connected {session_id}"
        return ConnectOut(host_name, exe, session_id, client)

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
        gbf = self.get_gui_background_function()
        gbf.run_blocking()
        assert self._client is not None
        return self._client

    def __exit__(self, *_args):
        """
        Release client lock after operation completion
        """
        _ = _args
        self._lock.release()
