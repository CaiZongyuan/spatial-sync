"""A node-test reader must never discover a partial descendant identity."""

import ast
import json
import os
import signal
import time
from pathlib import Path


def test_descendant_marker_is_complete_when_its_final_name_becomes_visible(tmp_path, monkeypatch):
    fixture = Path(__file__).parents[1] / "fixtures/fake_trainer.py"
    snippets = [
        node.value
        for node in ast.walk(ast.parse(fixture.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "LW_DESCENDANT_MARKER" in node.value
        and "createdAt" in node.value
    ]
    assert len(snippets) == 1
    marker = tmp_path / "descendant.json"
    monkeypatch.setenv("LW_DESCENDANT_MARKER", str(marker))
    monkeypatch.setattr(signal, "signal", lambda *_: None)
    monkeypatch.setattr(time, "sleep", lambda *_: None)

    def interleave_reader(path, text, *args, **kwargs):
        with path.open("w", encoding="utf-8") as stream:
            # The real consumer waits only for this name, then immediately parses it.
            if marker.exists():
                json.loads(marker.read_text(encoding="utf-8"))
            return stream.write(text)

    monkeypatch.setattr(Path, "write_text", interleave_reader)
    exec(snippets[0], {})
    identity = json.loads(marker.read_text(encoding="utf-8"))
    assert identity["pid"] == os.getpid() and identity["createdAt"] > 0
