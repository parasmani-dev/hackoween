from threading import Lock


class RunConflict(Exception):
    pass


class RunControl:
    def __init__(self, adapter, evaluate_delivery=None):
        self.adapter = adapter
        self.evaluate_delivery = evaluate_delivery
        self.lock = Lock()
        self.clear()

    def clear(self):
        self.phase = "Idle"
        self.command = None
        self.holding = False
        self.retry_count = 0
        self.outcome = None
        self.needs_reset = False

    def state(self):
        with self.lock:
            return {
                **self.adapter.state(),
                "state": self.phase,
                "holding": self.holding,
                "retry_count": self.retry_count,
            }

    def look(self):
        with self.lock:
            return self.adapter.look()

    def execute(self, method, args, response_fields):
        if not self.lock.acquire(blocking=False):
            raise RunConflict("mutation_in_progress")
        try:
            if method == "reset" or method == "start":
                return self.lifecycle(method, args)
            if self.needs_reset:
                raise RunConflict("reset_required")
            if self.phase in ("Idle", "Done"):
                raise RunConflict("no_active_run")

            try:
                result = getattr(self.adapter, method)(*args)
                payload = {field: result[field] for field in response_fields}
                if any(type(value) is not bool for value in payload.values()):
                    raise ValueError("Action results must contain boolean flags")
                self.advance(method, payload)
            except Exception:
                self.needs_reset = True
                raise
            return payload
        finally:
            self.lock.release()

    def lifecycle(self, method, args):
        if method == "start":
            if self.needs_reset:
                raise RunConflict("reset_required")
            if self.phase not in ("Idle", "Done"):
                raise RunConflict("run_already_active")
        try:
            self.adapter.reset()
        except Exception:
            self.needs_reset = True
            raise
        self.clear()
        if method == "start":
            self.command, = args
            self.phase = "Locate"
        return {"state": self.phase}

    def advance(self, method, result):
        success = result["success"]
        if method == "move":
            if self.phase == "Blocked" and (result["collided"] or not success):
                self.retry_count += 1
                if self.retry_count == 3:
                    self.phase = "Done"
                    self.outcome = "fail_collision"
            elif result["collided"]:
                self.phase = "Blocked"
            elif success:
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
                if self.evaluate_delivery is not None:
                    correct = self.evaluate_delivery(self.command)
                    self.outcome = "pass" if correct else "fail_wrong_object"
            elif self.phase != "Blocked":
                self.phase = "Drop"
