# Rover Lab in Docker

Run these commands from the repository root with Docker running in Linux
container mode.

```sh
docker build -f integration/docker/Dockerfile -t rover-lab .
docker run --rm --name rover-lab -p 127.0.0.1:10000:10000 rover-lab
```

Open http://localhost:10000 or http://127.0.0.1:10000 and select a scenario.
Opening Scenario 1 or Jury Demo starts its Webots world. Switching scenarios
while a run is active returns HTTP 409. Wait for the run to finish, then
open the other scenario again. Stop the container with Ctrl+C.
These scenarios use scripted actions and need no model credentials.

The repository keeps `integration/docker/start.sh` in LF format, and the build
also normalizes the copied launcher for existing Windows checkouts using CRLF.
Software rendering can make a long move exceed the desktop's 30-second bridge
timeout. The image gives the Webots bridge 60 seconds and scripted action
clients 65 seconds. An action that exceeds those limits still ends uncertain
without being repeated. `ROVER_WEBOTS_TIMEOUT` and `ROVER_ACTION_TIMEOUT`
configure these bounds in seconds; keep the action timeout above the bridge
timeout when overriding them. Desktop defaults remain 30 and 35 seconds.

## Publish on another port

Set `ROVER_PUBLIC_URL` to the exact browser origin, including the published
host port. For a browser using localhost on port 8113, run the following.

```sh
docker run --rm --name rover-lab -p 127.0.0.1:8113:10000 -e ROVER_PUBLIC_URL=http://localhost:8113 rover-lab
```

Open http://localhost:8113. Start and Reset require an Origin header that matches
the configured URL. The URL accepts an optional trailing slash and must have
an http or https scheme, a hostname, and no credentials, query, fragment or
other path. A custom URL authorizes exactly that origin.

On Render, `RENDER_EXTERNAL_URL` supplies the public origin automatically.
`ROVER_PUBLIC_URL` takes precedence when set, including for a custom domain.
For another hosting provider, set `ROVER_PUBLIC_URL=https://your-public-host`.
The container listens on `PORT`, which defaults to 10000. Without either
public URL variable, both localhost and 127.0.0.1 are accepted on `PORT`.

## Verify

```sh
python -B -m unittest integration.docker.test_gateway -v
```

The regression checks local Start and Reset requests, a different published
port, Render configuration, rejected foreign origins and invalid URLs.
It exercises request handling without starting Webots.

Before merging, build and run the image, complete the red-sphere run in
Scenario 1, switch to Jury Demo and complete the blue-cylinder run. Confirm
that both front-camera and spectator images render and that Start and Reset
work through the chosen public URL. Repeat using the port-8113 example.
Container build and Webots rendering need Docker verification on the demo host.
