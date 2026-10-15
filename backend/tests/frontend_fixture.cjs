// 同时供 VM 单元测试与离线浏览器测试读取真实外置脚本，避免重构后执行空源码。
const fs = require('node:fs');
const path = require('node:path');

const staticRoot = path.resolve(__dirname, '../static');
const html = fs.readFileSync(path.join(staticRoot, 'index.html'), 'utf8');
const scriptPaths = [...html.matchAll(/<script\s+src="(assets\/[^"]+\.js)"\s*><\/script>/g)]
  .map(match => match[1]);
const scripts = new Map(scriptPaths.map(name => [name, fs.readFileSync(path.join(staticRoot, name), 'utf8')]));
const main = scripts.get('assets/main.js');
const recording = scripts.get('assets/recording.js');

function section(source, start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from);
  if (from < 0 || to < from) throw new Error(`前端函数边界不存在：${start} → ${end}`);
  return source.slice(from, to);
}

async function serveScript(route, pathname) {
  const source = scripts.get(pathname.replace(/^\//, ''));
  if (source === undefined) return false;
  await route.fulfill({contentType: 'application/javascript', body: source});
  return true;
}

module.exports = {html, main, recording, scripts, section, serveScript};
