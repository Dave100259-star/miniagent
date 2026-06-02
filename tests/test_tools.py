from miniagent.safety import Workspace
from miniagent.tools import default_registry


def test_write_then_read(tmp_path):
    ws, reg = Workspace(tmp_path), default_registry()
    out = reg.call("write_file", {"path": "x.txt", "content": "hi"}, ws)
    assert "已写入" in out
    assert reg.call("read_file", {"path": "x.txt"}, ws) == "hi"


def test_list_dir(tmp_path):
    ws, reg = Workspace(tmp_path), default_registry()
    reg.call("write_file", {"path": "a.txt", "content": "1"}, ws)
    assert "a.txt" in reg.call("list_dir", {"path": "."}, ws)


def test_read_missing_returns_error(tmp_path):
    ws, reg = Workspace(tmp_path), default_registry()
    assert reg.call("read_file", {"path": "nope.txt"}, ws).startswith("ERROR")


def test_run_command(tmp_path):
    ws, reg = Workspace(tmp_path), default_registry()
    out = reg.call("run_command", {"command": "python -c \"print(123)\""}, ws)
    assert "123" in out and "exit=0" in out


def test_unknown_tool(tmp_path):
    ws, reg = Workspace(tmp_path), default_registry()
    assert reg.call("nope", {}, ws).startswith("ERROR")


def test_schemas_shape():
    reg = default_registry()
    schemas = reg.schemas()
    assert {s["function"]["name"] for s in schemas} == {
        "read_file", "write_file", "edit_file", "list_dir", "run_command"}
    assert all(s["type"] == "function" for s in schemas)


def test_edit_file(tmp_path):
    ws, reg = Workspace(tmp_path), default_registry()
    reg.call("write_file", {"path": "a.py", "content": "x = 1\n"}, ws)
    out = reg.call("edit_file", {"path": "a.py", "old": "x = 1", "new": "x = 2"}, ws)
    assert "已替换" in out
    assert reg.call("read_file", {"path": "a.py"}, ws) == "x = 2\n"


def test_edit_file_text_missing(tmp_path):
    ws, reg = Workspace(tmp_path), default_registry()
    reg.call("write_file", {"path": "a.py", "content": "hello"}, ws)
    assert reg.call("edit_file", {"path": "a.py", "old": "nope", "new": "x"}, ws).startswith("ERROR")


def test_edit_file_ambiguous(tmp_path):
    ws, reg = Workspace(tmp_path), default_registry()
    reg.call("write_file", {"path": "a.py", "content": "a\na\n"}, ws)
    assert "不唯一" in reg.call("edit_file", {"path": "a.py", "old": "a", "new": "b"}, ws)
