import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from loop_research.engine import CodexEngine, EngineFailure, diagnostic
from loop_research.runtime import process_alive
from loop_research.store import ResearchStore
from loop_research.workflow import ResearchWorkflow
from loop_research import roles


SHIM = """#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
if args == ['login', 'status']:
    print('Logged in using API key' if os.environ.get('SHIM_AUTH') == 'api' else 'Logged in using ChatGPT')
elif args == ['--version']:
    print('codex-cli synthetic-runtime-fixture')
elif args == ['exec', '--help']:
    print('--ignore-user-config --ephemeral')
else:
    assert 'OPENAI_API_KEY' not in os.environ
    assert 'CODEX_API_KEY' not in os.environ
    assert '--ignore-user-config' in args and '--sandbox' in args
    assert args[args.index('--sandbox') + 1] == 'read-only'
    marker = os.environ.get('SHIM_MARKER')
    if marker:
        Path(marker).write_text(str(os.getpid()))
    sys.stdin.read()
    if os.environ.get('SHIM_SLEEP') == '1':
        time.sleep(30)
    result = dict(interpretation='Synthetic question',domains=['Synthetic domain'],
                  search_queries=[dict(query='synthetic query',provider='arxiv',purpose='Synthetic search')],
                  key_terms=['synthetic'],recommendation='proceed',reason='Synthetic rationale',
                  uncertainties=['Synthetic runtime fixture; not research evidence.'])
    Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(result))
    print(json.dumps(dict(type='thread.started',thread_id='synthetic-thread')))
    print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=1,output_tokens=1))))
"""


@unittest.skipUnless(sys.platform.startswith("linux"), "Owned process checks target Ubuntu/Linux")
class RuntimeAcceptance(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="loop-research-runtime-")
        self.root = Path(self.temporary.name)
        self.binary = self.root / "codex"
        self.binary.write_text(SHIM)
        self.binary.chmod(0o700)
        self.marker = self.root / "child.pid"
        self.env = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ["PATH"],
                        SHIM_MARKER=str(self.marker), OPENAI_API_KEY="synthetic-key-not-real",
                        CODEX_API_KEY="synthetic-key-not-real")
        self.db = self.root / "study.sqlite3"
        store = ResearchStore(self.db, initialize=True)
        store.init("Synthetic runtime question")
        store.configure_study("Explicit test models", settings={"model": "fixture-author", "review_model": "fixture-reviewer"})
        store.close()

    def tearDown(self):
        self.temporary.cleanup()

    def test_official_cli_contract_and_subscription_environment(self):
        with patch.dict(os.environ, self.env):
            result, execution = CodexEngine(timeout=3).execute("orientation", {}, "Synthetic test", roles.ORIENTATION)
        self.assertEqual(result["recommendation"], "proceed")
        self.assertEqual(execution["thread_id"], "synthetic-thread")

    def test_api_key_authentication_never_runs_inference(self):
        with patch.dict(os.environ, dict(self.env, SHIM_AUTH="api")):
            with self.assertRaises(EngineFailure):
                CodexEngine(timeout=3).execute("orientation", {}, "Synthetic test", roles.ORIENTATION)
        self.assertFalse(self.marker.exists())

    def launch(self):
        env = dict(self.env, SHIM_SLEEP="1")
        client = subprocess.Popen(
            [sys.executable, "-m", "loop_research", "--db", str(self.db), "study", "orient", "--timeout", "20"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for _ in range(250):
            if client.poll() is not None:
                self.fail(str(client.communicate()))
            if self.marker.exists():
                store = ResearchStore(self.db)
                row = store.task_row("T001")
                store.close()
                if row["process_id"]:
                    return client, row
            time.sleep(0.02)
        client.kill()
        client.wait()
        self.fail("Fake child did not start")

    def test_human_pause_kills_only_the_owned_child_and_records_interruption(self):
        client, row = self.launch()
        try:
            self.assertTrue(process_alive(row["process_id"], row["process_identity"]))
            store = ResearchStore(self.db)
            ResearchWorkflow(store).pause("Human requests a pause")
            self.assertEqual(store.study_state()["status"], "held")
            self.assertEqual(store.task_row("T001")["status"], "interrupted")
            store.close()
            client.communicate(timeout=5)
            self.assertFalse(process_alive(row["process_id"], row["process_identity"]))
        finally:
            if client.poll() is None:
                client.kill()
                client.wait()

    def test_hard_killed_controller_does_not_leave_inference_running(self):
        client, row = self.launch()
        client.send_signal(signal.SIGKILL)
        client.communicate(timeout=5)
        for _ in range(100):
            if not process_alive(row["process_id"], row["process_identity"]):
                break
            time.sleep(0.02)
        self.assertFalse(process_alive(row["process_id"], row["process_identity"]))
        store = ResearchStore(self.db)
        ResearchWorkflow(store).recover()
        self.assertEqual(store.task_row("T001")["status"], "interrupted")
        store.close()

    def test_diagnostics_redact_authentication_material(self):
        with patch.dict(os.environ, {"CODEX_API_KEY": "sensitive-test-value"}):
            text = diagnostic("ERROR Bearer abcdef\nfailed with sensitive-test-value\nERROR sk-secret-key")
        self.assertNotIn("abcdef", text)
        self.assertNotIn("sensitive-test-value", text)
        self.assertNotIn("sk-secret-key", text)


if __name__ == "__main__":
    unittest.main()
