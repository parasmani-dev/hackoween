"""Serve the real Webots adapter for a persistent local operator dashboard."""
import argparse
import os
import time
from engine.server import create_server
from simulation.adapter import WebotsAdapter

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge-port", type=int, required=True)
    parser.add_argument("--engine-port", type=int, required=True)
    args = parser.parse_args()
    adapter = WebotsAdapter(port=args.bridge_port,
                            timeout=float(os.environ.get("ROVER_WEBOTS_TIMEOUT", "30")))
    for attempt in range(60):
        try:
            adapter.state()
            break
        except OSError:
            time.sleep(0.5)
    else:
        raise RuntimeError("Webots bridge did not start")
    with create_server(adapter, port=args.engine_port) as server:
        server.serve_forever()
