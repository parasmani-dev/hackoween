import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { readPreview } from "./preview.mjs";

const pages = {
  "/": ["index.html", "text/html; charset=utf-8"],
  "/operator.js": ["operator.js", "text/javascript; charset=utf-8"],
};
const port = Number(process.env.ROVER_UI_PORT ?? 8090);
const enginePort = Number(process.env.ROVER_ENGINE_PORT ?? 8080);
const previewFile = process.env.ROVER_PREVIEW_FILE;
const offline = process.env.ROVER_OFFLINE_DEMO === "1";
const demoCommand = "Fetch the red sphere and drop it in the green zone";
let demoStatus = "idle";
let lifecyclePending = false;

createServer(async (request, response) => {
  const path = new URL(request.url, "http://localhost").pathname;
  const lifecycle = path === "/run/start" || path === "/run/reset";
  const method = lifecycle ? "POST" : "GET";
  if (request.method !== method) {
    response.writeHead(405, { Allow: method }).end();
    return;
  }
  const page = Object.hasOwn(pages, path) ? pages[path] : null;
  if (page) {
    const [file, type] = page;
    response.writeHead(200, { "Content-Type": type });
    response.end(readFileSync(new URL(file, import.meta.url)));
    return;
  }
  if (path === "/preview" && previewFile) {
    try {
      const frame = readPreview(previewFile);
      response.writeHead(200, { "Content-Type": "image/jpeg", "Cache-Control": "no-store" });
      response.end(frame);
    } catch { response.writeHead(503).end(); }
    return;
  }
  if (path === "/offline-demo") {
    response.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
    response.end(JSON.stringify({ enabled: offline, preview: Boolean(previewFile), command: demoCommand, status: demoStatus }));
    return;
  }
  if (!lifecycle && path !== "/state" && path !== "/look") {
    response.writeHead(404).end();
    return;
  }
  let payload;
  if (lifecycle) {
    if (request.headers.origin !== `http://127.0.0.1:${port}` ||
        request.headers["content-type"] !== "application/json") {
      response.writeHead(403).end();
      return;
    }
    const size = Number(request.headers["content-length"]);
    if (!Number.isInteger(size) || size < 1 || size > 1024) {
      response.writeHead(400).end();
      return;
    }
  }
  if (offline && lifecycle && (lifecyclePending || demoStatus === "running")) {
    response.writeHead(409).end();
    return;
  }
  if (offline && lifecycle) lifecyclePending = true;
  try {
    if (lifecycle) {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      payload = Buffer.concat(chunks);
      if (offline && path === "/run/start") {
        if (JSON.parse(payload)?.command !== demoCommand) {
          response.writeHead(400).end();
          return;
        }
        payload = JSON.stringify({ command: demoCommand, expected_fixture_key: "item_1" });
      }
    }
    const upstream = await fetch(`http://127.0.0.1:${enginePort}${path}`, {
      method,
      body: payload,
      headers: lifecycle ? { "Content-Type": "application/json" } : undefined,
      signal: AbortSignal.timeout(lifecycle ? 10000 : 2500),
      redirect: "error",
    });
    if (!lifecycle && !upstream.ok) throw new Error("Engine returned an error");
    const body = Buffer.from(await upstream.arrayBuffer());
    if (offline && upstream.ok && path === "/run/start") {
      demoStatus = "running";
      const script = fileURLToPath(new URL("../integration/offline_fetch.py", import.meta.url));
      execFile(process.env.ROVER_PYTHON ?? "python", [script, String(enginePort)], (error) => {
        if (error) console.error(error.message);
        demoStatus = error ? "fail" : "pass";
      });
    }
    if (offline && upstream.ok && path === "/run/reset") demoStatus = "idle";
    response.writeHead(upstream.status, {
      "Content-Type": path === "/look" ? "image/jpeg" : "application/json",
      "Cache-Control": "no-store",
    });
    response.end(body);
  } catch {
    response.writeHead(502).end("Engine unavailable");
  } finally {
    if (offline && lifecycle) lifecyclePending = false;
  }
}).listen(port, "127.0.0.1", () => {
  console.log(`Rover operator at http://127.0.0.1:${port}`);
});
