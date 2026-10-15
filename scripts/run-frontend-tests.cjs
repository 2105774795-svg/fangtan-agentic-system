// 各测试自行拦截网络；此脚本不启动服务，也不读取真实用户资料。
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');

const root = path.resolve(__dirname, '..');
const tests = fs.readdirSync(path.join(root, 'backend/tests'))
  .filter(name => name.startsWith('test_') && name.endsWith('.cjs'))
  .sort();

if (!tests.length) throw new Error('未找到前端回归测试，不能把空运行视为通过');
for (const name of tests) {
  console.log(name);
  const result = spawnSync(process.execPath, [path.join(root, 'backend/tests', name)], {
    cwd: root, env: process.env, stdio: 'inherit',
  });
  if (result.error) throw result.error;
  if (result.signal || result.status !== 0) process.exit(result.status || 1);
}
console.log(`PASS: ${tests.length} 组前端回归测试全部通过`);
