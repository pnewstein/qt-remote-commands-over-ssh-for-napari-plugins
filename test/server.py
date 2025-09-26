from qt_remote_commands_over_ssh_for_napari_plugins import main_loop, Response, logger


def callback(request: str) -> Response:
    return Response("test3", "")


main_loop(callback)
