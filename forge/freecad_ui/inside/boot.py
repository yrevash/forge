"""Imported by the stub macro the launcher writes (which puts this folder on the path)."""

from __future__ import annotations

import os
import sys

import FreeCAD
import ops
import server

# Keep a reference on the FreeCAD module, or Python would delete the server at once.
FreeCAD._forge_ui_server = server.Server(ops.handle)
ops.prepare()
sys.stderr.write(f"forge ui server: listening, pid {os.getpid()}\n")
