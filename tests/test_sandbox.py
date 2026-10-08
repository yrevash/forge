"""The sandbox must survive everything a bad program can do."""

import pytest

from forge.sandbox import Sandbox

BOX = "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 20, 30)\n"


@pytest.fixture(scope="module")
def sandbox():
    with Sandbox(timeout=5) as sb:
        yield sb


def test_valid_program_is_measured(sandbox):
    reply = sandbox.run(BOX)
    assert reply["status"] == "ok"
    m = reply["measure"]
    assert m["one_valid_solid"]
    assert m["volume"] == pytest.approx(6000.0)
    assert m["bbox"] == pytest.approx([10, 20, 30])
    assert m["n_faces"] == 6


def test_syntax_error(sandbox):
    reply = sandbox.run("result = (")
    assert reply["status"] == "error"
    assert reply["error_type"] == "SyntaxError"


def test_exception_reports_line(sandbox):
    reply = sandbox.run("x = 1\nraise ValueError('boom')\n")
    assert reply["status"] == "error"
    assert reply["error_type"] == "ValueError"
    assert reply["line"] == 2


def test_missing_result(sandbox):
    assert sandbox.run("x = 1")["status"] == "no_result"


def test_non_solid_result(sandbox):
    code = "import cadquery as cq\nresult = cq.Workplane('XY').rect(1, 1)\n"
    assert sandbox.run(code)["status"] == "no_result"


def test_infinite_loop_times_out_and_sandbox_survives(sandbox):
    reply = sandbox.run("while True:\n    pass\n", timeout=1)
    assert reply["status"] == "timeout"
    assert sandbox.run(BOX)["status"] == "ok"


def test_hard_crash_and_sandbox_survives(sandbox):
    reply = sandbox.run("import os, signal\nos.kill(os.getpid(), signal.SIGSEGV)\n")
    assert reply["status"] == "crash"
    assert sandbox.run(BOX)["status"] == "ok"


def test_sys_exit_is_not_a_result(sandbox):
    reply = sandbox.run("import sys\nsys.exit(0)\n")
    assert reply["status"] == "error"
    assert reply["error_type"] == "SystemExit"


def test_network_is_blocked(sandbox):
    code = (
        "import socket\n"
        "socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
        "import cadquery as cq\nresult = cq.Workplane('XY').box(1, 1, 1)\n"
    )
    reply = sandbox.run(code)
    assert reply["status"] == "error"


def test_cannot_write_outside_temp_dir(sandbox, tmp_path):
    target = tmp_path / "escaped.txt"
    reply = sandbox.run(f"open({str(target)!r}, 'w').write('x')\n")
    assert reply["status"] == "error"
    assert not target.exists()


def test_cannot_read_home_directory(sandbox):
    from pathlib import Path

    reply = sandbox.run(f"import os\nos.listdir({str(Path.home())!r})\n")
    assert reply["status"] == "error"
    assert reply["error_type"] == "PermissionError"


def test_step_export_check(sandbox):
    reply = sandbox.run(BOX, check_export=True)
    assert reply["status"] == "ok"
    assert reply["step_export_ok"] is True


def test_programs_do_not_share_state(sandbox):
    sandbox.run("import builtins\nbuiltins.leaked = 1\n")
    reply = sandbox.run("import builtins\nassert not hasattr(builtins, 'leaked')\n")
    assert reply["status"] == "no_result"
