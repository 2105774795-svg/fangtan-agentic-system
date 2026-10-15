// 静态校验不替代交互测试：确认外置顺序、脚本语法及真实 DOM 没有重复 ID。
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {html, main, recording, scripts} = require('./frontend_fixture.cjs');

assert.deepEqual([...scripts.keys()], [
  'assets/recording-support.js', 'assets/recording.js', 'assets/main.js', 'assets/memoir.js',
], '录音支持库和函数模块必须先于页面初始化加载');
assert(!/<script>\s*\S/.test(html), '主业务逻辑应使用外置脚本');
for (const [name, source] of scripts) new vm.Script(source, {filename: name});
assert.match(main, /bootstrapAuth\(\);\s*$/);
assert.match(recording, /^async function toggleRec\(\)/);
assert(!main.includes('async function toggleRec()'), '录音函数不得在主模块重复定义');
const functionNames = [...`${recording}\n${main}`.matchAll(/^(?:async )?function (\w+)\(/gm)].map(match => match[1]);
assert.equal(new Set(functionNames).size, functionNames.length, '页面函数名称不得重复');
const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
assert.equal(new Set(ids).size, ids.length, '页面 DOM ID 不得重复');
console.log('PASS: 外置脚本加载顺序、语法、唯一函数和 DOM ID');
