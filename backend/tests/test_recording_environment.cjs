const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('../../frontend/node_modules/typescript');
const source = fs.readFileSync('frontend/src/recordingSupport.ts', 'utf8');
function load(secure, media = true, recorder = true) {
  const context = { exports: {}, window: { isSecureContext: secure, MediaRecorder: recorder ? class {} : undefined }, navigator: { mediaDevices: media ? { getUserMedia() {} } : undefined } };
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, context);
  return context.exports;
}
assert.match(load(false).recordingUnavailableReason(), /HTTP.*禁止访问麦克风/);
assert.match(load(false, false).recordingUnavailableReason(), /HTTPS/);
assert.match(load(true, false).recordingUnavailableReason(), /缺少录音能力/);
assert.match(load(true, true, false).recordingUnavailableReason(), /缺少录音能力/);
const support = load(true);
assert.equal(support.recordingUnavailableReason(), '');
assert.match(support.recordingFailureMessage({ name: 'NotAllowedError' }), /权限未开启/);
assert.match(support.recordingFailureMessage({ name: 'NotFoundError' }), /未找到麦克风/);
assert.match(support.recordingFailureMessage({ name: 'NotReadableError' }), /其他应用占用/);
assert.equal(support.recordingFailureMessage(new Error('上传失败')), '上传失败');
assert.match(support.recordingFailureMessage(null), /录音启动失败/);
console.log('PASS: HTTP, missing browser APIs and microphone permission/device failures are distinguished.');
