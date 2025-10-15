# Qt Remote Commands over SSH for napari Plugins 

A Python package for executing remote processing tasks over SSH from napari
plugins with Qt-based UIs.

## Overview

This package enables napari plugins to offload computationally intensive image
processing to remote servers via SSH. It provides:

- Thread-safe SSH client for sending requests and transferring files
- Qt widget integration for connection management UI
- JSON-based request/response protocol for structured communication
- Context manager API for safe resource handling

## Installation 


``` pip install qt-remote-commands-over-ssh-for-napari-plugins[client] ```

## Use Case


When building napari plugins that need to process large images or run heavy
computations, you can:

1. Run a lightweight server script on a remote machine
1. Send image data and processing parameters from the napari UI
1. Receive processed results and display them as new layers

The package handles SSH connection pooling, file transfers (via scp), and
thread synchronization so multiple UI operations can safely share a single
connection.

## Example 

See the included example which demonstrates:

- A napari plugin that applies a gamma factor to images
- Serializing numpy arrays and sending them to a remote server
- Processing the data remotely and returning results
- Adding processed images back to the napari viewer

The client sends image data over SSH, the server processes it, and results are
transferred back and displayed—all while keeping the napari UI responsive
through background threading.
