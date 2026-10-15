// 后台纪要只提交一次；兼容旧同步接口，不将断网或等待超时误报为任务失败。
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {main, section} = require('./frontend_fixture.cjs');
const source = section(main, 'async function requestReport(', 'async function importMaterial(){');
const response = (payload, status = 200) => ({ok: status < 400, status,
  json: async () => payload, text: async () => typeof payload === 'string' ? payload : JSON.stringify(payload)});

function harness(respond) {
  let now = 0;
  const calls = [], progress = [], waits = [];
  const context = vm.createContext({Promise, Error, Date: {now: () => now},
    onProgress: state => progress.push(state),
    setTimeout(callback, delay) {waits.push(delay);now += delay;callback();return 1;},
    responseError: async res => (await res.json()).detail || '接口处理失败',
    fetch: async (url, options) => {calls.push({url, options});return respond(url, options, calls.length);},
  });
  vm.runInContext(source, context);
  return {calls, progress, waits, run: () => vm.runInContext('requestReport("session-one",onProgress)', context)};
}

(async () => {
  const cached = harness((url, options) => {
    assert.equal(url, 'api/sessions/session-one/finish');
    assert.equal(options.method, 'POST');assert.equal(options.headers['X-Background-Task'], '1');
    return response({report: '# 缓存纪要', report_status: 'ready'});
  });
  assert.equal(await cached.run(), '# 缓存纪要');assert.equal(cached.calls.length, 1);

  let reads = 0;
  const background = harness(url => {
    if (url.endsWith('/finish')) return response({task_id: 'task-one', session_id: 'session-one', report_status: 'generating'}, 202);
    if (url.endsWith('/report-job')) return response(++reads === 1
      ? {report_status: 'generating', stage: '正在核对完整逐字稿', progress: 40}
      : {report_status: 'ready', stage: '纪要已完成', progress: 100});
    if (url.endsWith('/report.md')) return response('# 后台纪要');
    throw new Error('意外请求：' + url);
  });
  assert.equal(await background.run(), '# 后台纪要');
  assert.equal(background.calls.filter(call => call.options?.method === 'POST').length, 1);
  assert.equal(background.calls.filter(call => call.url.endsWith('/report-job')).length, 2);
  assert.deepEqual(background.waits, [1200]);
  assert.match(background.progress[0].stage, /后台生成/);
  assert(background.progress.some(state => state.progress === 40));

  const failed = harness(url => url.endsWith('/finish') ? response({task_id: 'task-two'}, 202)
    : response({report_status: 'failed', error: '模型额度不足，请稍后重试'}));
  await assert.rejects(failed.run(), error => /模型额度不足/.test(error.message) && !error.reportPending);
  assert(!failed.calls.some(call => call.url.endsWith('/report.md')));

  const offline = harness(url => {
    if (url.endsWith('/finish')) return response({task_id: 'task-three'}, 202);
    throw new Error('网络中断');
  });
  await assert.rejects(offline.run(), error => error.reportPending && /已提交后台.*历史会话/.test(error.message));
  assert.equal(offline.calls.filter(call => call.options?.method === 'POST').length, 1);

  const interruptedBody = harness(() => ({ok: true, status: 202, json: async () => {throw new Error('响应传输中断');}}));
  await assert.rejects(interruptedBody.run(), error => error.reportPending && /已提交后台.*响应读取中断/.test(error.message));
  assert.equal(interruptedBody.calls.length, 1);

  const timeout = harness(url => url.endsWith('/finish') ? response({task_id: 'task-four'}, 202)
    : response({report_status: 'generating', stage: '后台处理中', progress: 70}));
  await assert.rejects(timeout.run(), error => error.reportPending && /仍在后台处理中.*无需重复提交/.test(error.message));
  assert.equal(timeout.waits.reduce((sum, delay) => sum + delay, 0), 15 * 60 * 1000);
  assert.equal(timeout.calls.filter(call => call.options?.method === 'POST').length, 1);
  console.log('PASS: 后台纪要提交一次、进度轮询、就绪读取、明确失败、同步兼容及断网/超时保留后台任务');
})().catch(error => {console.error(error);process.exitCode = 1;});
