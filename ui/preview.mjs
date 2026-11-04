import { readFileSync } from "node:fs";

export function readPreview(path, now = Date.now()) {
  const statusPath = `${path}.json`;
  const status = JSON.parse(readFileSync(statusPath, "utf8"));
  const age = now - status.heartbeat_ms;
  if (!status.ready || typeof status.session !== "string" || !status.session ||
      !Number.isFinite(status.heartbeat_ms) || age < 0 || age > 5000) {
    throw new Error("Preview producer unavailable");
  }
  const frame = readFileSync(path);
  // A producer restart between metadata and frame reads must not revive an old image.
  const after = JSON.parse(readFileSync(statusPath, "utf8"));
  if (!after.ready || after.session !== status.session) throw new Error("Preview session changed");
  return frame;
}
