"""Operator-only run provenance and persistent, exclusive hidden-slot claims."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path


def prepare_attempt(args, evidence_dir):
    model_variable = "CLAUDE_MODEL" if args.provider == "claude" else "OPENAI_MODEL"
    record = {
        "attempt_id": evidence_dir.name,
        "execution_mode": getattr(args, "execution_mode", "live"),
        "provider": args.provider,
        "model": os.environ.get(model_variable),
        "command_sha256": hashlib.sha256(args.command.encode("utf-8")).hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "max_decisions": args.max_decisions,
        "official_slot": getattr(args, "official_slot", None),
    }
    slot = record["official_slot"]
    if slot is not None:
        if slot not in (1, 2, 3):
            raise ValueError("Official slot must be 1, 2 or 3")
        ledger = Path(args.official_ledger).resolve()
        ledger.mkdir(parents=True, exist_ok=True)
        # Never clear a claim automatically, including startup failures. One shared
        # ledger on the designated submission laptop is required across checkouts.
        with (ledger / f"slot-{slot}.json").open("x", encoding="utf-8") as stream:
            json.dump({**record, "attempt_directory": str(evidence_dir.resolve())}, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
    (evidence_dir / "provenance.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record
