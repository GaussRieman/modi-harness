"""S7 — cross-process resume.

Process A: run a task to interrupt, persist state to sqlite, exit.
Process B (subprocess): open the same sqlite checkpointer, resume with the
approval payload, observe the run finish.

The driver child process is implemented inline in this file and invoked via
``python -m`` style. We use ``subprocess.run`` so both phases run in fresh
interpreters and the only shared state is the sqlite file.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# This driver script is written to disk and run as a child process for both
# the prepare and the resume phases. Keeping the driver inline (rather than in
# a separate module) makes the test self-contained.
DRIVER = """
import json
import sqlite3
import sys
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import Field

from modi_harness import ModiAgent
from modi_harness._test_fixtures import make_session


class _Script(BaseChatModel):
    script: list = Field(default_factory=list)
    cursor: dict = Field(default_factory=lambda: {"i": 0})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        i = self.cursor["i"]
        self.cursor["i"] = i + 1
        return ChatResult(generations=[ChatGeneration(message=self.script[i])])

    @property
    def _llm_type(self):
        return "xp_script"


_SEND_SPEC = {
    "name": "send",
    "description": "",
    "input_schema": {
        "type": "object",
        "properties": {"to": {"type": "string"}},
        "required": ["to"],
    },
    "risk_level": "L3",
    "side_effect": True,
}


def _session(workdir: Path, db: Path, script: _Script):
    conn = sqlite3.connect(str(db), check_same_thread=False)
    cp = SqliteSaver(conn)
    cp.setup()
    agent = ModiAgent.from_markdown(
        workdir / "agents" / "demo.md",
        tools=[(_SEND_SPEC, lambda **kw: {"sent": kw["to"]})],
    )
    return make_session(
        workdir,
        chat_model=script,
        agents=[agent],
        checkpointer=cp,
    )


def main():
    phase = sys.argv[1]
    workdir = Path(sys.argv[2])
    db = Path(sys.argv[3])

    if phase == "prepare":
        script = _Script(
            script=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "send", "args": {"to": "x"}, "id": "tc"}],
                ),
                AIMessage(content="done"),
            ]
        )
        h = _session(workdir, db, script)
        first = h.run_task(agent="demo", input={"goal": "x"}, thread_id="t-xp")
        print(json.dumps({
            "status": first["status"],
            "thread_id": first["thread_id"],
            "approval_id": first["pending_approval"]["approval_id"],
        }))
        return 0

    if phase == "resume":
        approval_id = sys.argv[4]
        # No script needed during resume except the second AI message.
        script = _Script(script=[AIMessage(content="done")])
        h = _session(workdir, db, script)
        second = h.approve_action(
            thread_id="t-xp",
            approval_id=approval_id,
            decision="approved",
        )
        print(json.dumps({"status": second["status"]}))
        return 0


if __name__ == "__main__":
    sys.exit(main())
"""


@pytest.mark.smoke
def test_s7_cross_process_resume(tmp_path: Path) -> None:
    # Write agent and driver.
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "demo.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: demo
            description: xp
            tools:
              - send
            permission_profile:
              mode: ask
            ---
            Use send when asked.
            """
        )
    )
    driver = tmp_path / "_driver.py"
    driver.write_text(DRIVER)

    db = tmp_path / "checkpoint.sqlite"

    # Phase A — prepare in a fresh interpreter
    proc_a = subprocess.run(
        [sys.executable, str(driver), "prepare", str(tmp_path), str(db)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert proc_a.returncode == 0, proc_a.stderr
    info = json.loads(proc_a.stdout.strip().splitlines()[-1])
    assert info["status"] == "interrupted"
    approval_id = info["approval_id"]

    assert db.exists(), "sqlite checkpoint file should be created by phase A"

    # Phase B — resume in another fresh interpreter
    proc_b = subprocess.run(
        [sys.executable, str(driver), "resume", str(tmp_path), str(db), approval_id],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert proc_b.returncode == 0, proc_b.stderr
    result = json.loads(proc_b.stdout.strip().splitlines()[-1])
    assert result["status"] == "completed"
