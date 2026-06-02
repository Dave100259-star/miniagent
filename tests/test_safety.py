import pytest

from miniagent.safety import Workspace


def test_resolve_stays_inside(tmp_path):
    ws = Workspace(tmp_path)
    p = ws.resolve("sub/dir/file.txt")
    assert str(p).startswith(str(tmp_path.resolve()))


def test_escape_is_blocked(tmp_path):
    ws = Workspace(tmp_path)
    with pytest.raises(ValueError):
        ws.resolve("../../../etc/passwd")


def test_absolute_escape_blocked(tmp_path):
    ws = Workspace(tmp_path)
    with pytest.raises(ValueError):
        ws.resolve("/etc/passwd" if not str(tmp_path).startswith("C:") else "C:\\Windows\\system.ini")
