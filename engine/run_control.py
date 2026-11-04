import logging
from threading import Lock

FIXTURE_KEYS = frozenset(f"item_{number}" for number in range(1, 7))


class RunConflict(Exception):
    pass


class RunControl:
    def __init__(self, adapter):
        self.adapter = adapter
        self.adapter_lock = Lock()
        self.mutation_lock = Lock()
        self.clear()

    def clear(self):
        self.phase = "Idle"
        self.expected_fixture_key = None
        self.holding = False
        self.retry_count = 0
        self.outcome = None
        self.verdict_reason = None
        self.needs_reset = False
        self.pov = {}

    def state(self):
        with self.adapter_lock:
            return {
                **self.adapter.state(),
                "state": self.phase,
                "holding": self.holding,
                "retry_count": self.retry_count,
            }

    def look(self):
        with self.adapter_lock:
            return self.adapter.look()

    def execute(self, method, args, response_fields):
        if not self.mutation_lock.acquire(blocking=False):
            raise RunConflict("mutation_in_progress")
        try:
            with self.adapter_lock:
                return self._execute(method, args, response_fields)
        finally:
            self.mutation_lock.release()

    def _execute(self, method, args, response_fields):
        if method == "finish":
            return self.finish()
        if method in ("reset", "start"):
            return self.lifecycle(method, *args)
        if self.needs_reset:
            raise RunConflict("reset_required")
        if self.phase in ("Idle", "Done"):
            raise RunConflict("no_active_run")

        previous_phase = self.phase
        if previous_phase != "Blocked":
            if method == "pickup":
                self.phase = "Pick up"
            elif method == "drop":
                self.phase = "Drop"
        try:
            result = getattr(self.adapter, method)(*args)
            payload = {field: result[field] for field in response_fields}
            if any(type(value) is not bool for value in payload.values()):
                raise ValueError("Action results must contain boolean flags")
            # Action phases are internal while the adapter lock is held.
            self.phase = previous_phase
            self.advance(method, payload)
        except Exception:
            self.phase = previous_phase
            self.needs_reset = True
            raise
        if self.phase == "Done":
            self.finalize_capture()
        return payload

    def lifecycle(self, method, expected_key=None, command=None, record_pov=False):
        if method == "start":
            if self.needs_reset:
                raise RunConflict("reset_required")
            if self.phase not in ("Idle", "Done"):
                raise RunConflict("run_already_active")
            if expected_key is not None:
                if not isinstance(expected_key, str) or expected_key not in FIXTURE_KEYS:
                    raise RunConflict("invalid_expected_fixture_key")
                if not callable(getattr(self.adapter, "delivered_fixture_key", None)):
                    raise RunConflict("evaluator_unavailable")
        try:
            self.adapter.reset()
        except Exception:
            self.needs_reset = True
            raise
        self.clear()
        if method == "start":
            self.expected_fixture_key = expected_key
            if record_pov:
                self.start_capture(command)
            self.phase = "Locate"
        return {"state": self.phase}

    def finish(self):
        if self.phase == "Idle":
            raise RunConflict("no_active_run")
        if self.phase != "Done":
            self.phase = "Done"
            self.outcome = "fail_not_reached"
            self.finalize_capture()
        result = {"state": self.phase, "outcome": self.outcome, **self.pov}
        if self.verdict_reason is not None:
            result["reason"] = self.verdict_reason
        return result

    def start_capture(self, command):
        self.pov["pov_status"] = "incomplete"
        try:
            if not callable(getattr(self.adapter, "finish_capture", None)):
                raise RuntimeError("Capture finalization unavailable")
            capture_id = self.adapter.start_capture(command)
            if not isinstance(capture_id, str) or not capture_id.strip():
                raise ValueError("Invalid capture ID")
            self.pov["pov_capture_id"] = capture_id
        except Exception:
            logging.warning("Private POV capture could not start")

    def finalize_capture(self):
        # Called only at the transition to Done, under both run locks.
        if self.needs_reset or "pov_capture_id" not in self.pov:
            return
        try:
            manifest = self.adapter.finish_capture()
            if not isinstance(manifest, str) or not manifest.strip():
                raise ValueError("Invalid capture manifest")
            self.pov.update(pov_status="complete", pov_manifest=manifest)
        except Exception:
            logging.warning("Private POV capture could not finalize")

    def record_delivery(self):
        delivered_key = None
        if self.expected_fixture_key is not None:
            read_key = getattr(self.adapter, "delivered_fixture_key", None)
            if read_key is not None:
                try:
                    delivered_key = read_key()
                except Exception:
                    logging.warning("Private delivery evaluation unavailable")
        if not isinstance(delivered_key, str) or delivered_key not in FIXTURE_KEYS:
            self.verdict_reason = "evaluator_unavailable"
        elif delivered_key == self.expected_fixture_key:
            self.outcome = "pass"
        else:
            self.outcome = "fail_wrong_object"

    def advance(self, method, result):
        success = result["success"]
        if method in ("move", "turn"):
            contact = result["collided"] if method == "move" else not success
            if self.phase == "Blocked" and (contact or not success):
                self.retry_count += 1
                if self.retry_count == 3:
                    self.phase = "Done"
                    self.outcome = "fail_collision"
            elif contact:
                self.phase = "Blocked"
            elif method == "move" and success:
                self.retry_count = 0
                self.phase = "Carry" if self.holding else "Go to object"
        elif method == "pickup" and success:
            self.holding = True
            if self.phase != "Blocked":
                self.phase = "Carry"
        elif method == "drop" and success:
            self.holding = False
            if result["in_zone"]:
                self.phase = "Done"
                self.record_delivery()
            elif self.phase != "Blocked":
                self.phase = "Drop"
