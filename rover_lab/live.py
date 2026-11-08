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
from agent.local_commands import parse_command, LocalPolicy
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
        self.phase = "idle"
        self.command = None
        self.max_decisions = None
        self.execution_mode = "live"
        self.local_plan = None

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
                    "provider": "local" if self.execution_mode == "local" else "claude",
                    "model": None if self.execution_mode == "local" else HAIKU_MODEL,
                    "execution_mode": self.execution_mode, "local_available": True,
                    "interpreted_command": self.local_plan.canonical if self.local_plan else self.command,
                    "running": self.running, "result": self.result, "phase": self.phase,
                    "command": self.command, "max_decisions": self.max_decisions,
                    "decisions": len(rows), "actions": [{k: row[k] for k in ("step", "tool", "args", "result")}
                                                        for row in rows[-5:]]}

    def operator(self, path, data, *, timeout=35):
        request = Request(self.engine_url + path, data=json.dumps(data).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)

    def start(self, command, cap, execution_mode="live"):
        if not isinstance(command, str) or not command.strip() or len(command) > 500:
            raise ValueError("Enter a command of at most 500 characters.")
        if type(cap) is not int or not 1 <= cap <= 60:
            raise ValueError("Decision cap must be a whole number from 1 to 60.")
        if execution_mode not in ('live', 'local'):
            raise ValueError('Choose local or live execution mode.')
        plan = parse_command(command) if execution_mode == 'local' else None
        with self.lock:
            if self.running:
                raise RuntimeError("Run already active.")
            if execution_mode == "live" and (os.environ.get("ROVER_LIVE_ENABLED") != "1" or not os.environ.get("ANTHROPIC_API_KEY")):
                raise ValueError("Live mode is unavailable. Restart Rover Lab with -EnableLiveAgent and a local Claude key.")
            # Fail before Start if the engine is already owned by another run.
            if EngineClient(self.engine_url).state()["state"] != "Idle":
                raise RuntimeError("Reset the rover before starting a live run.")
            self.output_root.mkdir(parents=True, exist_ok=True)
            self.attempt = Path(mkdtemp(prefix="attempt-", dir=self.output_root))
            self.running = True
            self.phase = "starting"
            self.command = command.strip()
            self.max_decisions = cap
            self.execution_mode = execution_mode
            self.local_plan = plan
            self.result = None
            self.thread = threading.Thread(target=self.run, args=(command.strip(), cap), daemon=True)
            try:
                self.thread.start()
            except Exception:
                self.running = False
                self.phase = "failed"
                raise

    def reset(self):
        with self.lock:
            if self.running:
                raise RuntimeError("Wait for the current run to finish before resetting.")
            result = self.operator("/run/reset", {})
            self.result = None
            self.attempt = None
            self.phase = "idle"
            self.command = None
            self.max_decisions = None
            self.local_plan = None
            return result

    def run(self, command, cap):
        started = False
        local = self.execution_mode == 'local'
        interpreted = self.local_plan.canonical if self.local_plan else command
        record = {"execution_mode": self.execution_mode, "provider": "local" if local else "claude",
                  "model": None if local else HAIKU_MODEL, "interpreted_command": interpreted,
                  "max_decisions": cap, "status": "operator_error",
                  "attempt_id": self.attempt.name, "created_at": datetime.now(timezone.utc).isoformat(),
                  "command_sha256": hashlib.sha256(command.encode()).hexdigest(), "official_slot": None}
        try:
            # The expected fixture is operator-only, and only known for this exact command.
            data = {"command": command, "record_pov": True}
            if interpreted.casefold().rstrip(".!?") == "fetch the red sphere and drop it in the green zone":
                data["expected_fixture_key"] = "item_1"
            self.operator("/run/start", data)
            started = True
            with self.lock:
                self.phase = "deciding"
            if local:
                policy = LocalPolicy(self.local_plan)
                result = run_command(interpreted, engine=EngineClient(self.engine_url), claude=policy,
                                     provider='local', camera_control=self.local_plan.kind == 'fetch',
                                     output_root=self.attempt, max_decisions=cap)
            else:
                # Per-client override preserves the user's environment and other providers.
                from agent.claude import ClaudeClient
                def audit(payload):
                    with (self.attempt / "claude_requests.jsonl").open("a", encoding="utf-8") as file:
                        file.write(json.dumps(payload) + "\n")
                client = ClaudeClient(model=HAIKU_MODEL, audit_request=audit)
                result = run_command(command, engine=EngineClient(self.engine_url), claude=client,
                                     output_root=self.attempt, max_decisions=cap)
            record.update(status=result.status, decisions=result.decisions, usage=result.usage,
                          agent_run_id=result.run_dir.name, provider_calls=0 if local else result.decisions)
        except Exception:
            # Provider and simulator details remain local to their own implementations.
            record["status"] = "operator_error"
        finally:
            if started:
                with self.lock:
                    self.phase = "finishing"
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
                    self.result = {k: record[k] for k in ("status", "decisions", "usage", "provider_calls") if k in record}
                    self.result["outcome"] = (record.get("verdict", {}).get("outcome") or
                                              ("unscored" if "verdict" in record else "unconfirmed"))
                    if local and self.local_plan.kind == 'sequence':
                        # Manual action completion is distinct from fetch evaluation.
                        self.result['outcome'] = 'completed' if record['status'] == 'local_complete' else 'stopped'
                    self.result["finish_confirmed"] = "verdict" in record
                    self.result["capture_status"] = record.get("verdict", {}).get("pov_status", "unconfirmed")
                    self.running = False
                    self.phase = "completed" if "verdict" in record else "failed"
