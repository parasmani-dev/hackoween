"""Operator-only live runs for Rover Lab. No credentials enter browser responses."""
import json
import hashlib
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp
from urllib.request import Request, urlopen

from agent.engine import EngineClient
from agent.runner import run_command
from simulation.check_http_fetch import finish_run

HAIKU_MODEL = "claude-haiku-4-5-20251001"


class LiveRun:
    def __init__(self, engine_url, output_root):
        self.engine_url = engine_url
        self.output_root = Path(output_root)
        self.lock = threading.Lock()
        self.running = False
        self.result = None
        self.attempt = None
        self.thread = None

    def status(self):
        with self.lock:
            rows = []
            if self.attempt:
                journal = next(self.attempt.glob("run-*/steps.jsonl"), None)
                if journal:
                    try:
                        rows = [json.loads(line) for line in journal.read_text().splitlines(keepends=True)
                                if line.endswith("\n")]
                    except (OSError, ValueError):
                        rows = []
            return {"enabled": os.environ.get("ROVER_LIVE_ENABLED") == "1",
                    "ready": bool(os.environ.get("ANTHROPIC_API_KEY")),
                    "provider": "claude", "model": HAIKU_MODEL,
                    "running": self.running, "result": self.result,
                    "decisions": len(rows), "actions": [{k: row[k] for k in ("step", "tool", "args", "result")}
                                                        for row in rows[-5:]]}

    def operator(self, path, data, *, timeout=35):
        request = Request(self.engine_url + path, data=json.dumps(data).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)

    def start(self, command, cap):
        if not isinstance(command, str) or not command.strip() or len(command) > 500:
            raise ValueError("Enter a fetch command of at most 500 characters.")
        if type(cap) is not int or not 1 <= cap <= 60:
            raise ValueError("Decision cap must be a whole number from 1 to 60.")
        with self.lock:
            if self.running:
                raise RuntimeError("Run already active.")
            if os.environ.get("ROVER_LIVE_ENABLED") != "1" or not os.environ.get("ANTHROPIC_API_KEY"):
                raise ValueError("Live mode is unavailable. Restart Rover Lab with -EnableLiveAgent and a local Claude key.")
            # Fail before Start if the engine is already owned by another run.
            if EngineClient(self.engine_url).state()["state"] != "Idle":
                raise RuntimeError("Reset the rover before starting a live run.")
            self.output_root.mkdir(parents=True, exist_ok=True)
            self.attempt = Path(mkdtemp(prefix="attempt-", dir=self.output_root))
            self.running = True
            self.result = None
            self.thread = threading.Thread(target=self.run, args=(command.strip(), cap), daemon=True)
            try:
                self.thread.start()
            except Exception:
                self.running = False
                raise

    def reset(self):
        with self.lock:
            if self.running:
                raise RuntimeError("Wait for the current run to finish before resetting.")
            result = self.operator("/run/reset", {})
            self.result = None
            return result

    def run(self, command, cap):
        started = False
        record = {"execution_mode": "live", "provider": "claude", "model": HAIKU_MODEL,
                  "max_decisions": cap, "status": "operator_error",
                  "attempt_id": self.attempt.name, "created_at": datetime.now(timezone.utc).isoformat(),
                  "command_sha256": hashlib.sha256(command.encode()).hexdigest(), "official_slot": None}
        try:
            # The expected fixture is operator-only, and only known for this exact command.
            data = {"command": command, "record_pov": True}
            if command.casefold().rstrip(".!?") == "fetch the red sphere and drop it in the green zone":
                data["expected_fixture_key"] = "item_1"
            self.operator("/run/start", data)
            started = True
            # A per-client model override keeps the user's environment and other providers intact.
            from agent.claude import ClaudeClient
            def audit(payload):
                with (self.attempt / "claude_requests.jsonl").open("a", encoding="utf-8") as file:
                    file.write(json.dumps(payload) + "\n")
            client = ClaudeClient(model=HAIKU_MODEL, audit_request=audit)
            result = run_command(command, engine=EngineClient(self.engine_url), claude=client,
                                 output_root=self.attempt, max_decisions=cap)
            record.update(status=result.status, decisions=result.decisions, usage=result.usage,
                          agent_run_id=result.run_dir.name)
        except Exception:
            # Provider and simulator details remain local to their own implementations.
            record["status"] = "operator_error"
        finally:
            if started:
                try:
                    reason = "decision_limit" if record["status"] == "decision_limit" else "abandoned"
                    record["verdict"] = finish_run(self.operator, reason)
                except Exception:
                    record["finish_error"] = "Finish could not be confirmed; check the engine before reset."
            try:
                (self.attempt / "operator-result.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
            except OSError:
                record["status"] = "artifact_error"
            finally:
                with self.lock:
                    # Only public summary fields reach the UI. Capture paths stay on disk.
                    self.result = {k: record[k] for k in ("status", "decisions", "usage") if k in record}
                    self.result["outcome"] = (record.get("verdict", {}).get("outcome") or
                                              ("unscored" if "verdict" in record else "unconfirmed"))
                    self.result["finish_confirmed"] = "verdict" in record
                    self.running = False
