// 不启服务：模拟浏览器录音事件，验证每段独立封装、停止竞态和失败提示。
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {recording: source} = require('./frontend_fixture.cjs');
const timers = new Map();
let timerId = 0;
const elements = new Map();
const uploads = [];
let fail = false;
class Recorder {
  static isTypeSupported() { return true; }
  constructor() { this.state = 'inactive'; this.starts = []; }
  start(...args) { this.starts.push(args); this.state = 'recording'; }
  stop() {
    assert.equal(this.state, 'recording', '不能对已停止的录音重复 stop');
    this.state = 'inactive';
    queueMicrotask(() => {
      this.ondataavailable({data: new Blob(['complete-container'], {type: 'audio/webm'})});
      this.onstop();
    });
  }
}
const context = vm.createContext({
  Blob, FormData, MediaRecorder: Recorder, console,
  navigator: {mediaDevices: {getUserMedia: async () => ({active: true, getTracks: () => [{stop() {}}]})}},
  setTimeout(fn, ms) { const id = ++timerId; timers.set(id, {fn, ms}); return id; },
  clearTimeout(id) { timers.delete(id); }, setInterval() { return 1; }, clearInterval() {},
  $(id) { if (!elements.has(id)) elements.set(id, {style: {}, classList: {add() {}, remove() {}}}); return elements.get(id); },
  alert(message) { throw new Error(message); }, fmtMs() { return ''; },
  responseError: async r => r.detail,
  fetch: async (_, options) => { uploads.push(options.body.get('audio')); return {ok: !fail, detail: '测试服务不可用'}; },
});
vm.runInContext(`let sid='test',recorder=null,stream=null,recordingTimer=null,uploading=false,tick=null,secs=0;
  let stopWait=Promise.resolve(),segmentWait=Promise.resolve(); const pending=[],failedUploads=[];
  let nativeRecording=false,nativeQueueCount=0,wakeLock=null,recordClockStart=0,recordClockOffset=0,recoveringAudio=false;
  let audioOrder=0,inFlightChunkId=null;
  ${source}`, context);
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
async function rotate() {
  const entry = [...timers.entries()].find(([, t]) => t.ms === 10000);
  assert.ok(entry, '应安排下一段录音');
  timers.delete(entry[0]); entry[1].fn();
  await flush();
}
(async () => {
  await vm.runInContext('toggleRec()', context);
  await rotate(); await rotate();
  await vm.runInContext('stopRec()', context); await flush();
  assert.equal(uploads.length, 3, '两段完整录音及停止时的尾段均应上传');
  assert.ok(uploads.every(blob => blob.type === 'audio/webm' && blob.name === 'chunk.webm'));
  assert.equal(vm.runInContext('recorder', context), null);
  assert.equal(timers.size, 0, '停止后不应继续录音');

  await vm.runInContext('toggleRec()', context);
  const active = vm.runInContext('recorder', context);
  assert.ok(active.starts.every(args => args.length === 0), '不使用不可独立解码的 timeslice 分片');
  active.stop(); // 轮转 stop 已排队，用户同时点停止。
  await vm.runInContext('stopRec()', context); await flush();
  assert.equal(active.starts.length, 1, '用户停止后不得重新开始下一段');

  fail = true;
  vm.runInContext("pending.push({blob:new Blob(['audio'],{type:'audio/mp4'}),attempts:2})", context);
  await vm.runInContext('pump()', context);
  assert.equal(uploads.at(-1).name, 'chunk.mp4');
  assert.equal(vm.runInContext('failedUploads.length', context), 1);
  assert.match(elements.get('hint').textContent, /测试服务不可用/);
  console.log('PASS: independent segments, final chunk, stop race, MIME extension, failure reason');
})().catch(error => { console.error(error); process.exitCode = 1; });
