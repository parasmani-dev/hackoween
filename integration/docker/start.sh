set -euo pipefail

export ROVER_LAB_RUN_DIR=/app/runs/rover-lab-8113
export ROVER_LAB_SCENARIO_ONE_PORT=8112
export ROVER_PREVIEW_FILE="$ROVER_LAB_RUN_DIR/scenario-2/front.jpg"
export ROVER_SPECTATOR_FILE="$ROVER_LAB_RUN_DIR/scenario-2/spectator.jpg"
export ROVER_DEMO_LAYOUT="$ROVER_LAB_RUN_DIR/selected-layout.txt"
mkdir -p "$ROVER_LAB_RUN_DIR"
printf A > "$ROVER_DEMO_LAYOUT"

python rover_lab/dashboard.py --port 8113 --engine-port 18846 &
python integration/docker/gateway.py &

wait -n
exit 1
