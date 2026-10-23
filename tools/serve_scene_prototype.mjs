// Throwaway, loopback-only server for the standalone scene factory prototype.
import http from 'node:http';
import { readFile } from 'node:fs/promises';
const html = new URL('../apps/web/prototypes/scene-factory.prototype.html', import.meta.url);
const port = Number(process.env.SCENE_PROTOTYPE_PORT || 5217);
const server = http.createServer(async (req, res) => {
  if (new URL(req.url, 'http://localhost').pathname === '/favicon.ico') { res.writeHead(204); res.end(); return; }
  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(await readFile(html));
});
server.listen(port, '127.0.0.1', () => console.log(`Scene factory prototype: http://127.0.0.1:${port}/?variant=A`));
process.on('SIGINT', () => server.close(() => process.exit(0)));
process.on('SIGTERM', () => server.close(() => process.exit(0)));
