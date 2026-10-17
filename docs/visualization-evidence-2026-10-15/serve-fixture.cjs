// Local-only production browser verification server. Usage: node serve-fixture.cjs ROOT PORT
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const root = path.resolve(process.argv[2]);
const mime = {'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.png':'image/png','.woff2':'font/woff2'};
http.createServer((req,res) => {
  const file = path.resolve(root, '.' + new URL(req.url, 'http://localhost').pathname);
  if (!file.startsWith(root + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {res.writeHead(404).end();return;}
  let body = fs.readFileSync(file);
  const headers = {'Content-Type':mime[path.extname(file)] || 'application/octet-stream','Cache-Control':'no-store'};
  if (/gzip/.test(req.headers['accept-encoding'] || '') && /\.(js|css|html|svg)$/.test(file)) {body=zlib.gzipSync(body);headers['Content-Encoding']='gzip';}
  headers['Content-Length']=body.length;
  res.writeHead(200,headers).end(body);
}).listen(Number(process.argv[3]), '127.0.0.1', () => console.log('Fixture server ready'));
