FROM node:22-bullseye-slim AS node_runtime
FROM ghcr.io/cyberbotics/webots-docker/webots@sha256:f31b128a3e4c06e54b26ce3d963a0e6b1c9634907978ae4397db9b4cde2d9f0c

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-dri python3-venv tini xauth xvfb \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node_runtime /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
COPY engine/requirements.txt engine/requirements.txt
COPY agent/requirements.txt agent/requirements.txt
RUN python3 -m venv /opt/rover && /opt/rover/bin/pip install --no-cache-dir \
    -r engine/requirements.txt -r agent/requirements.txt
ENV PATH="/opt/rover/bin:$PATH" LIBGL_ALWAYS_SOFTWARE=1 PYTHONUNBUFFERED=1

COPY agent agent
COPY engine engine
COPY integration integration
COPY simulation simulation
COPY ui ui
COPY rover_lab rover_lab
EXPOSE 10000
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/bin/bash", "/app/integration/docker/start.sh"]
