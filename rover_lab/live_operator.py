"""Explicit operator-started live runs; credentials and evaluation stay server-side."""

import json
import re
import threading
from pathlib import Path
from tempfile import mkdtemp
from types import SimpleNamespace
from urllib.request import Request, urlopen

from agent.claude import ClaudeClient
from agent.engine import EngineClient
from agent.openai_client import OpenAIClient
from agent.runner import run_command
from simulation.check_http_fetch import finish_run
from simulation.provenance import prepare_attempt


class LiveRequestError(ValueError):
    pass


class LiveConflict(RuntimeError):
    pass


class LiveOperator:
    def __init__(self, engine_url, output_root):
        self.engine_url = engine_url
        self.output_root = Path(output_root)
        self.lock = threading.RLock()
        self.running = False
        self.active = None
        self.result = None
        self._thread = None

    def call(self, path, payload, *, timeout=35):
        req = Request(self.engine_url + path, data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=timeout) as response:
            return json.load(response)

    @staticmethod
    def validate(data):
        if not isinstance(data, dict) or data.keys() - {"command", "provider", "max_decisions", "expected_fixture_key", "confirm_paid"}:
            raise LiveRequestError("Unexpected live-run fields.")
        command = data.get("command")
        if not isinstance(command, str) or not command.strip() or len(command) > 500:
            raise LiveRequestError("Enter a command of 1 to 500 characters.")
        if data.get("provider") not in ("claude", "openai"):
            raise LiveRequestError("Choose Claude or OpenAI.")
        cap = data.get("max_decisions")
        if type(cap) is not int or not 1 <= cap <= 60:
            raise LiveRequestError("Decision cap must be a whole number from 1 to 60.")
        key = data.get("expected_fixture_key")
        if key is not None and (not isinstance(key, str) or key not in {f"item_{i}" for i in range(1, 7)}):
            raise LiveRequestError("Choose a valid scoring target or leave the run unscored.")
        if data.get("confirm_paid") is not True:
            raise LiveRequestError("Live Start requires acknowledgement that it uses API credits.")
        return {"command": command.strip(), "provider": data["provider"],
                "max_decisions": cap, "expected_fixture_key": key}

    def start(self, data):
        args = self.validate(data)
        with self.lock:
            if self.running:
                raise LiveConflict("A live run is already active.")
            if EngineClient(self.engine_url, timeout=3).state()["state"] not in ("Idle", "Done"):
                raise LiveConflict("Finish or reset the current run first.")
            try:
                client = {"claude": ClaudeClient, "openai": OpenAIClient}[args["provider"]]()
            except Exception:
                raise LiveRequestError("Provider setup is incomplete. Configure its API key and model in the local .env file.") from None
            model = client.model
            self.output_root.mkdir(parents=True, exist_ok=True)
            self.active = Path(mkdtemp(prefix="attempt-", dir=self.output_root))
            self.result = None
            self.running = True
            self._thread = threading.Thread(target=self._run, args=(args, model), daemon=True)
            self._thread.start()
        return {"ok": True, "running": True}

    def _run(self, args, model):
        attempt = self.active
        record = {"provider": args["provider"], "model": model, "command": args["command"],
                  "max_decisions": args["max_decisions"], "scripted": False,
                  "execution_profile": "vision_with_local_approach_control",
                  "local_pickup_checks_enabled": True,
                  "scored": args["expected_fixture_key"] is not None,
                  "agent_status": "startup_error", "usage": {}, "decisions": 0,
                  "model_requests": 0, "local_pickup_checks": 0,
                  "local_approach_probes": 0, "local_control_steps": 0}
        if args["expected_fixture_key"] is not None:
            record["expected_fixture_key"] = args["expected_fixture_key"]
        started = False
        try:
            provenance_args = SimpleNamespace(**args, execution_mode="live", official_slot=None)
            provenance = prepare_attempt(provenance_args, attempt)
            provenance.update(execution_profile="vision_with_local_approach_control",
                              local_pickup_checks_enabled=True)
            (attempt / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
            record.update(provenance)
            payload = {"command": args["command"], "record_pov": True}
            if args["expected_fixture_key"] is not None:
                payload["expected_fixture_key"] = args["expected_fixture_key"]
            self.call("/run/start", payload)
            started = True
            # The private scoring selector is deliberately absent from agent arguments.
            result = run_command(args["command"], provider=args["provider"],
                                 engine=EngineClient(self.engine_url), output_root=attempt,
                                 max_decisions=args["max_decisions"], audit_requests=True,
                                 local_pickup_checks=True)
            model_requests = getattr(result, "model_requests", result.decisions)
            source_counts = self.source_counts(result.run_dir)
            local_checks = source_counts["local_pickup_check"]
            local_probes = source_counts["local_approach_probe"]
            record.update(agent_status=result.status, decisions=result.decisions,
                          usage=result.usage, agent_run_id=result.run_dir.name,
                          model_requests=model_requests,
                          local_pickup_checks=local_checks,
                          local_approach_probes=local_probes,
                          local_control_steps=local_checks + local_probes)
        except Exception:
            record["error"] = "The live run stopped. Review its local logs before retrying."
        finally:
            if started:
                try:
                    reason = "decision_limit" if record["agent_status"] == "decision_limit" else "abandoned"
                    verdict = finish_run(self.call, reason)
                    record["verdict"] = verdict.get("outcome") if record["scored"] else "unscored"
                    for name in ("pov_status", "pov_capture_id", "pov_manifest"):
                        if name in verdict:
                            record[name] = verdict[name]
                except Exception:
                    record["verdict"] = "unknown"
                    record["error"] = "Could not confirm run finalization. Reset before another attempt."
            else:
                record["verdict"] = "not_started"
            try:
                (attempt / "operator-summary.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
            except OSError:
                record["error"] = "Run completed, but its summary could not be saved."
            with self.lock:
                self.result = record
                self.running = False

    def reset(self):
        with self.lock:
            if self.running:
                raise LiveConflict("Wait for the live run to finish before resetting.")
            if EngineClient(self.engine_url, timeout=3).state()["state"] not in ("Idle", "Done"):
                raise LiveConflict("Another run is active. Wait for it to finish.")
            value = self.call("/run/reset", {})
            self.result = None
            self.active = None
            return value

    @staticmethod
    def source_counts(folder):
        counts = {"model": 0, "local_pickup_check": 0, "local_approach_probe": 0}
        if folder and (folder / "decision_sources.jsonl").exists():
            for line in (folder / "decision_sources.jsonl").read_text(encoding="utf-8").splitlines(keepends=True):
                if line.endswith("\n"):
                    try:
                        source = json.loads(line).get("source")
                    except (ValueError, AttributeError):
                        continue
                    if isinstance(source, str) and source in counts:
                        counts[source] += 1
        return counts

    def snapshot(self):
        with self.lock:
            attempt, result, running = self.active, self.result, self.running
        folder = next(attempt.glob("run-*"), None) if attempt else None
        rows = []
        if folder and (folder / "steps.jsonl").exists():
            for line in (folder / "steps.jsonl").read_text(encoding="utf-8").splitlines(keepends=True):
                if line.endswith("\n"):
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        counts = self.source_counts(folder)
        have_sources = sum(counts.values()) > 0
        model_requests = counts["model"] if have_sources else (result or {}).get("model_requests", 0)
        local_checks = counts["local_pickup_check"] if have_sources else (result or {}).get("local_pickup_checks", 0)
        local_probes = counts["local_approach_probe"] if have_sources else (result or {}).get("local_approach_probes", 0)
        public = {name: result.get(name) for name in ("provider", "model", "command", "agent_status", "verdict", "scored", "usage", "decisions", "error", "pov_status", "execution_profile", "model_requests", "local_pickup_checks", "local_approach_probes", "local_control_steps")} if result else None
        if public:
            public.update(model_requests=model_requests, local_pickup_checks=local_checks,
                          local_approach_probes=local_probes, local_control_steps=local_checks + local_probes)
        if public and (not isinstance(public["model"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", public["model"]) or public["model"].startswith("sk-")):
            public["model"] = "Configured model"
        decisions = max(len(rows), sum(counts.values()), (result or {}).get("decisions", 0))
        return {"running": running, "result": public, "run_id": folder.name if folder else None,
                "decisions": decisions, "collisions": sum(r["result"].get("collided") is True for r in rows),
                "execution_profile": "vision_with_local_approach_control",
                "model_requests": model_requests, "local_pickup_checks": local_checks,
                "local_approach_probes": local_probes, "local_control_steps": local_checks + local_probes,
                "rows": rows,
                "last_action": {k: rows[-1][k] for k in ("step", "tool", "args", "result")} if rows else None}
