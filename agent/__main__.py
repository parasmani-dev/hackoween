"""Command-line entry point for an operator-started rover run."""

import argparse
import json
from dataclasses import asdict

from .claude import ClaudeError
from .engine import EngineClient
from .runner import run_command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", help="Operator's fetch command")
    parser.add_argument("--engine-url", default="http://localhost:8080")
    parser.add_argument("--output-root", default="runs")
    args = parser.parse_args()
    try:
        result = run_command(
            args.command, engine=EngineClient(args.engine_url), output_root=args.output_root
        )
    except (ClaudeError, ValueError) as error:
        parser.error(str(error))
    except OSError:
        parser.error("Could not create rover run artifacts.")
    print(json.dumps(asdict(result), default=str))
    return 0 if result.status == "engine_done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
