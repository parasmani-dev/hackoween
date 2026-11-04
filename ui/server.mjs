import { readFileSync } from "node:fs";
import { createServer } from "node:http";

const pages = {
  "/": ["index.html", "text/html; charset=utf-8"],
  "/operator.js": ["operator.js", "text/javascript; charset=utf-8"],
};
const port = Number(process.env.ROVER_UI_PORT ?? 8090);

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
  try {
    if (lifecycle) {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      payload = Buffer.concat(chunks);
    }
    const upstream = await fetch(`http://127.0.0.1:8080${path}`, {
      method,
      body: payload,
      headers: lifecycle ? { "Content-Type": "application/json" } : undefined,
      signal: AbortSignal.timeout(lifecycle ? 10000 : 2500),
      redirect: "error",
    });
    if (!lifecycle && !upstream.ok) throw new Error("Engine returned an error");
    const body = Buffer.from(await upstream.arrayBuffer());
    response.writeHead(upstream.status, {
      "Content-Type": path === "/look" ? "image/jpeg" : "application/json",
      "Cache-Control": "no-store",
    });
    response.end(body);
  } catch {
    response.writeHead(502).end("Engine unavailable");
  }
}).listen(port, "127.0.0.1", () => {
  console.log(`Rover operator at http://127.0.0.1:${port}`);
});
