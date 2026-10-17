// 离线浏览器回归：路由提供构建产物和合成 API，不启动项目服务，不调用真实 AI。
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '..');
const buildRoot = path.resolve(root, process.env.PLATFORM_TEST_DIST || 'backend/static/platform');
const output = path.join(root, 'data/qa/auto-conversation');
fs.mkdirSync(output, { recursive: true });
const speechFile = path.join(output, 'synthetic-speech.wav');
if (!fs.existsSync(speechFile)) {
  if (process.platform !== 'win32') throw new Error('实际 VAD 回归需要在 data/qa/auto-conversation/synthetic-speech.wav 提供一段合成人声。');
  const target = speechFile.replaceAll("'", "''");
  execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', `Add-Type -AssemblyName System.Speech; $qaSynth = New-Object System.Speech.Synthesis.SpeechSynthesizer; $qaSynth.SetOutputToWaveFile('${target}'); $qaSynth.Speak('When I was young, my mother took me to the market. I remember the streets and the people who lived in our neighborhood.'); $qaSynth.Dispose()`], { windowsHide: true });
}
const origin = 'http://127.0.0.1:18881';
const project = { id: 'auto-fixture', title: '连续对谈验证', prompt: '口述史', scenario: 'memoir', mode: 'ai', stage: 0, answers: {}, context: '', artifacts: [], messages: [], revision: 1, updated_at: 1792200000, layout: { version: 0, positions: {} } };
const turn = (id, role, text) => ({ id, role, text, output: '', client_id: id, control: '', audio_ids: [], job: null });

async function fixture(browser, { model = false, noVoice = false, missingEnd = false, slowPrepare = false } = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' });
  const data = { id: 'session-auto', kind: 'memoir', status: 'live', created_at: 1792200000, turns: [turn('welcome', 'assistant', '您好，您愿意先讲讲小时候的故事吗？')], audio: [], archive: null, archive_job: null };
  const stats = { submissions: [], uploads: [], attempts: 0, replyRetries: 0, failUploads: 0, failSubmit: 0, empty: false, delayed: false, unexpected: [], modelAssets: [] };
  let page;
  await context.addInitScript(({ noVoice, missingEnd, model }) => {
    window.__speech = false; window.__tts = []; window.__micCalls = 0; window.__contexts = 0; window.__streams = []; window.__recorders = [];
    const Recorder = window.MediaRecorder;
    window.MediaRecorder = class extends Recorder {
      constructor(...args) { super(...args); window.__recorders.push(this); }
    };
    window.__events = [];
    window.EventSource = class extends EventTarget {
      constructor() { super(); window.__events.push(this); }
      close() { window.__events = window.__events.filter(item => item !== this); }
    };
    window.__snapshot = value => window.__events.forEach(item => item.dispatchEvent(new MessageEvent('snapshot', { data: JSON.stringify(value) })));
    window.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
    Object.defineProperty(window, 'speechSynthesis', { value: {
      getVoices: () => noVoice ? [] : [{ lang: 'zh-CN', localService: true }],
      addEventListener() {}, removeEventListener() {}, cancel() {},
      speak: utterance => {
        if (window.__recorders.some(r => r.state === 'recording')) throw new Error('AI 播报期间不能录制用户声音');
        window.__tts.push(utterance.text); if (!missingEnd) setTimeout(() => utterance.onend?.(), 40);
      },
    } });
    const Audio = window.AudioContext;
    if (model && window.AudioWorklet) {
      const load = AudioWorklet.prototype.addModule;
      AudioWorklet.prototype.addModule = async function(url, options) {
        // Chromium 的 worklet 请求绕过 Playwright route；通过同源 fetch 后仍运行真正的 AudioWorklet。
        const response = await fetch(url);
        const blob = URL.createObjectURL(new Blob([await response.text()], { type: 'text/javascript' }));
        try { return await load.call(this, blob, options); }
        finally { URL.revokeObjectURL(blob); }
      };
    }
    window.AudioContext = class extends Audio {
      constructor(...args) { super(...args); window.__contexts++; }
      createAnalyser() {
        const analyser = super.createAnalyser();
        if (!model) analyser.getFloatTimeDomainData = array => array.fill(window.__speech ? .12 : 0);
        return analyser;
      }
    };
    navigator.mediaDevices.getUserMedia = async () => {
      window.__micCalls++;
      const audio = new Audio(); await audio.resume();
      const gain = audio.createGain(); gain.gain.value = 0;
      const oscillator = audio.createOscillator(); oscillator.connect(gain); oscillator.start();
      const destination = audio.createMediaStreamDestination(); gain.connect(destination);
      window.__inputAudio = audio; window.__inputDestination = destination;
      window.__streams.push(destination.stream);
      return destination.stream;
    };
  }, { noVoice, missingEnd, model });
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    const pathname = url.pathname;
    const json = value => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
    if (url.origin !== origin) { stats.unexpected.push(url.href); return route.abort(); }
    if (pathname.endsWith('/scenarios')) return json({ memoir: { name: '口述史', steps: [], fields: [] } });
    if (pathname === '/api/platform/projects') return json([project]);
    if (pathname === '/api/interview-prep/projects') return json([]);
    if (pathname === '/api/platform/projects/auto-fixture') return json(project);
    if (pathname.endsWith('/conversations') || pathname.endsWith('/conversations/session-auto')) return json(data);
    if (pathname.endsWith('/audio')) {
      stats.attempts++;
      if (stats.failUploads-- > 0) return route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"合成暂时故障"}' });
      const body = route.request().postDataBuffer().toString('latin1');
      const field = name => body.match(new RegExp('name="' + name + '"\\r\\n\\r\\n([^\\r]+)'))?.[1];
      stats.uploads.push({ turn: field('turn_key'), chunk: field('chunk_id'), index: Number(field('part_index')), bytes: route.request().postDataBuffer().length });
      return json({ text: '这是一段合成讲述。' });
    }
    if (pathname.endsWith('/turns')) {
      const body = route.request().postDataJSON();
      stats.submissions.push(body);
      if (stats.failSubmit-- > 0) return route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"合成提交故障"}' });
      if (stats.empty) { stats.empty = false; return json({ ...data, no_speech: true }); }
      if (!data.turns.some(t => t.client_id === body.client_id)) {
        data.turns.push({ ...turn('human-' + data.turns.length, 'human', body.text || '这是一段合成讲述。'), client_id: body.client_id, audio_ids: body.audio_ids || [] });
        const reply = turn('reply-' + data.turns.length, 'assistant', '您刚才提到的那段经历，后来怎么样了？');
        if (stats.delayed) { reply.text = ''; reply.job = { id: 'reply-job', status: 'running' }; }
        data.turns.push(reply);
      }
      return json(data);
    }
    if (pathname.endsWith('/retry')) {
      stats.replyRetries++;
      const reply = data.turns.at(-1);
      reply.text = '您刚才说的那件事，后来怎么样了？'; reply.job = { id: 'manual-retry', status: 'ready' };
      return json(data);
    }
    if (pathname.endsWith('/finish')) { data.status = 'done'; return json(data); }
    if (pathname.startsWith('/api/')) { stats.unexpected.push(pathname); return route.fulfill({ status: 404, body: '{}' }); }
    if (pathname.startsWith('/platform-assets/vad/')) {
      stats.modelAssets.push(pathname);
      if (!model) {
        if (slowPrepare) await new Promise(resolve => setTimeout(resolve, 400));
        return route.fulfill({ status: 404, body: '' });
      }
    }
    if (pathname === '/synthetic-speech.wav') return route.fulfill({ contentType: 'audio/wav', body: fs.readFileSync(path.join(output, 'synthetic-speech.wav')) });
    const base = buildRoot;
    const target = pathname === '/assets/recording-support.js' ? path.join(root, 'backend/static/assets/recording-support.js') : path.resolve(base, pathname === '/' ? 'index.html' : pathname.slice(1));
    if ((!target.startsWith(base + path.sep) && pathname !== '/assets/recording-support.js') || !fs.existsSync(target)) return route.fulfill({ status: 404, body: '' });
    const types = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.wasm': 'application/wasm', '.css': 'text/css', '.woff2': 'font/woff2', '.wav': 'audio/wav' };
    return route.fulfill({ contentType: types[path.extname(target)] || 'application/octet-stream', body: fs.readFileSync(target) });
  });
  page = await context.newPage();
  if (model) page.on('console', message => console.log('[本地 VAD]', message.type(), message.text()));
  const errors = []; page.on('pageerror', e => errors.push(e.message)); page.on('dialog', d => d.accept());
  if (!model) await page.clock.install();
  await page.goto(origin + '/?project=auto-fixture');
  await page.getByRole('button', { name: '开始自动对谈', exact: true }).waitFor();
  await page.getByRole('checkbox').check();
  async function advance(ms) { await page.clock.runFor(ms); await page.waitForTimeout(25); }
  async function until(condition, label) {
    for (let i = 0; i < 350; i++) {
      if (await condition()) return;
      if (!model) await advance(100); else await page.waitForTimeout(100);
    }
    throw new Error(label + '\n' + await page.locator('.conversation-main').innerText() + '\n' + errors.join('\n'));
  }
  const listening = () => page.locator('.conversation-state').getAttribute('data-phase').then(v => v === 'listening' || v === 'user-speaking');
  async function start() {
    await page.locator('.conversation-preferences summary').click();
    await page.getByLabel('说话停顿等待').selectOption('3000');
    await page.getByRole('button', { name: '开始自动对谈', exact: true }).click();
    await until(listening, '没有自动开始收音');
  }
  async function voice(ms = 600) { await page.evaluate(() => { window.__speech = true; }); await advance(ms); await page.evaluate(() => { window.__speech = false; }); }
  async function sayRound() {
    const baseline = stats.submissions.length;
    await voice(); await advance(3500);
    await until(() => stats.submissions.length > baseline, '没有自动提交');
    await until(listening, '没有自动接续收音');
  }
  return { context, page, data, stats, errors, advance, until, listening, start, voice, sayRound };
}

(async () => {
  const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_BROWSER_CHANNEL || 'msedge', headless: true, args: ['--autoplay-policy=no-user-gesture-required'] });
  const passed = [];
  const check = async (name, task) => { if (process.env.AUTO_TEST_FILTER && !name.includes(process.env.AUTO_TEST_FILTER)) return; await task(); passed.push(name); console.log('PASS:', name); };
  try {
    await check('一次开始连续十轮；麦克风和 AudioContext 复用；没有重复播报', async () => {
      const f = await fixture(browser);
      try {
        await f.start();
        for (let i = 0; i < 10; i++) await f.sayRound();
        assert.equal(f.stats.submissions.length, 10);
        assert.equal(new Set(f.stats.submissions.map(x => x.client_id)).size, 10);
        const counters = await f.page.evaluate(() => ({ mic: window.__micCalls, contexts: window.__contexts, tts: window.__tts.length }));
        assert.deepEqual(counters, { mic: 1, contexts: 1, tts: 11 });
        await f.page.evaluate(data => { window.__snapshot(data); window.__snapshot(data); }, f.data);
        await f.advance(500);
        assert.equal(await f.page.evaluate(() => window.__tts.length), 11, '重复 SSE 快照不能重复播报');
        assert.equal(await f.page.getByRole('button', { name: '开始自动对谈', exact: true }).count(), 0);
        await f.page.screenshot({ path: path.join(output, 'continuous-desktop.png'), fullPage: true });
        await f.page.setViewportSize({ width: 390, height: 844 });
        assert.ok(await f.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await f.page.screenshot({ path: path.join(output, 'continuous-mobile.png'), fullPage: true });
        await f.page.getByRole('button', { name: '暂停对谈', exact: true }).click();
        await f.advance(12000);
        assert.ok(await f.page.evaluate(() => window.__streams.every(s => s.getTracks().every(t => t.readyState === 'ended'))));
        assert.deepEqual(f.errors, []); assert.deepEqual(f.stats.unexpected, []);
      } finally { await f.context.close(); }
    });
    await check('30 秒安静不上传不提交；停顿内继续说重置倒计时；跨 10 秒分片只提交一轮', async () => {
      const f = await fixture(browser);
      try {
        await f.start(); await f.advance(30000);
        assert.equal(f.stats.uploads.length, 0); assert.equal(f.stats.submissions.length, 0);
        await f.voice(); await f.advance(2000);
        await f.page.getByText(/等待您继续说/).waitFor();
        await f.voice(12000);
        assert.equal(f.stats.submissions.length, 0);
        await f.advance(3500);
        await f.until(() => f.stats.submissions.length === 1, '长讲述没有提交');
        await f.until(f.listening, '长讲述后没有继续收音');
        assert.ok(f.stats.submissions[0].audio_ids.length >= 2);
        assert.deepEqual(f.stats.uploads.map(x => x.index), f.stats.uploads.map((_, i) => i));
        assert.deepEqual(f.errors, []);
      } finally { await f.context.close(); }
    });
    await check('空转写自动继续；转写和提交临时故障按原 ID 重试', async () => {
      const f = await fixture(browser, { noVoice: true });
      try {
        await f.start(); f.stats.empty = true; await f.sayRound();
        assert.equal(f.data.turns.length, 1);
        f.stats.failUploads = 1; f.stats.failSubmit = 1; await f.sayRound();
        assert.equal(f.data.turns.filter(t => t.role === 'human').length, 1);
        const attempts = f.stats.submissions.slice(-2);
        assert.equal(attempts[0].client_id, attempts[1].client_id);
        assert.deepEqual(f.errors, []);
      } finally { await f.context.close(); }
    });
    await check('AI 临时失败自动恢复后接续收音，原话不会重复提交', async () => {
      const f = await fixture(browser);
      try {
        await f.start(); f.stats.delayed = true;
        await f.voice(); await f.advance(3500);
        await f.until(() => f.stats.submissions.length === 1, '未提交');
        const last = f.data.turns.at(-1);
        last.job = { id: 'reply-job', status: 'retry', error_code: 'model_timeout', attempts: 1, max_attempts: 2 };
        await f.page.evaluate(data => window.__snapshot(data), f.data); await f.advance(6000);
        assert.match(await f.page.locator('.conversation-state').innerText(), /正在自动重试/);
        assert.equal(await f.listening(), false); assert.equal(f.stats.submissions.length, 1);
        last.text = '您记得集市上最热闹的地方吗？'; last.job = { id: 'reply-job', status: 'ready', attempts: 2, max_attempts: 2 };
        await f.page.evaluate(data => window.__snapshot(data), f.data);
        await f.until(f.listening, '重试成功后没有继续收音');
        assert.equal(f.stats.submissions.length, 1); assert.equal(f.stats.replyRetries, 0);
        assert.equal(f.data.turns.filter(t => t.role === 'human').length, 1);
        assert.deepEqual(f.errors, []); assert.deepEqual(f.stats.unexpected, []);
      } finally { await f.context.close(); }
    });
    await check('AI 重试耗尽显示错误，手动重试只接续同一条原话', async () => {
      const f = await fixture(browser);
      try {
        await f.start(); f.stats.delayed = true;
        await f.voice(); await f.advance(3500);
        await f.until(() => f.stats.submissions.length === 1, '未提交');
        f.data.turns.at(-1).job = { id: 'reply-job', status: 'failed', error_code: 'model_timeout', error: 'AI 回应等待超时，您的讲述已保存。', attempts: 2, max_attempts: 2 };
        await f.page.evaluate(data => window.__snapshot(data), f.data); await f.advance(12000);
        assert.equal(await f.listening(), false);
        assert.match(await f.page.getByRole('alert').innerText(), /等待超时/);
        assert.equal(f.stats.submissions.length, 1); assert.equal(f.stats.replyRetries, 0);
        await f.page.getByRole('button', { name: '原话已保存，重试 AI 回应', exact: true }).click();
        await f.until(f.listening, '手动重试后没有继续收音');
        assert.equal(f.stats.replyRetries, 1); assert.equal(f.stats.submissions.length, 1);
        assert.equal(f.data.turns.filter(t => t.role === 'human').length, 1);
        assert.deepEqual(f.errors, []); assert.deepEqual(f.stats.unexpected, []);
      } finally { await f.context.close(); }
    });
    await check('系统接续明确标注，随后继续听取下一轮讲述', async () => {
      const f = await fixture(browser);
      try {
        await f.start(); f.stats.delayed = true;
        await f.voice(); await f.advance(3500);
        await f.until(() => f.stats.submissions.length === 1, '未提交');
        const last = f.data.turns.at(-1); last.text = '您可以接着往下讲，我在听。';
        last.job = { id: 'reply-job', status: 'ready', notice: '本轮使用简短系统接续，未生成完整 AI 回复；您的讲述已保存，可以继续讲。' };
        await f.page.evaluate(data => window.__snapshot(data), f.data);
        await f.until(f.listening, '系统接续后没有继续收音');
        assert.match(await f.page.locator('.conversation-notice').innerText(), /简短系统接续/);
        assert.equal(f.stats.submissions.length, 1); assert.deepEqual(f.errors, []);
      } finally { await f.context.close(); }
    });
    await check('播报结束事件丢失后自动恢复；主动打断播报进入收音', async () => {
      const f = await fixture(browser, { missingEnd: true });
      try {
        await f.start();
        await f.voice(); await f.advance(3500);
        await f.until(() => f.page.locator('.conversation-state').getAttribute('data-phase').then(p => p === 'speaking'), 'AI 没有进入播报');
        await f.page.getByRole('button', { name: '我来说', exact: true }).click();
        await f.until(f.listening, '打断后未进入收音'); assert.deepEqual(f.errors, []);
      } finally { await f.context.close(); }
    });
    await check('等待 AI 时暂停，迟到回复不会开麦；设置保存；结束释放设备', async () => {
      const f = await fixture(browser);
      try {
        await f.start(); f.stats.delayed = true;
        await f.voice(); await f.advance(3500);
        await f.until(() => f.stats.submissions.length === 1, '未提交');
        f.data.turns.at(-1).job = { id: 'reply-job', status: 'retry' };
        await f.page.evaluate(data => window.__snapshot(data), f.data); await f.advance(100);
        await f.page.getByRole('button', { name: '暂停对谈', exact: true }).click();
        const last = f.data.turns.at(-1); last.text = '这是迟到的回应。'; last.job = { status: 'ready' };
        await f.page.evaluate(data => window.__snapshot(data), f.data);
        await f.advance(10000);
        assert.equal(await f.page.locator('.conversation-state').getAttribute('data-phase'), 'paused');
        assert.equal(await f.page.evaluate(() => window.__micCalls), 1);
        await f.page.getByRole('button', { name: '继续自动对谈', exact: true }).click();
        await f.until(f.listening, '恢复失败');
        await f.page.getByLabel('说话停顿等待').selectOption('8000');
        await f.page.getByRole('button', { name: '今天先聊到这里', exact: true }).click();
        await f.page.getByRole('heading', { name: '谢谢您，今天的讲述已经留下。' }).waitFor();
        assert.ok(await f.page.evaluate(() => window.__streams.every(s => s.getTracks().every(t => t.readyState === 'ended'))));
        await f.page.reload();
        await f.page.getByRole('button', { name: '再聊一会', exact: true }).waitFor();
        assert.equal(await f.page.evaluate(() => localStorage.getItem('conversation-silence-ms')), '8000');
        assert.deepEqual(f.errors, []);
      } finally { await f.context.close(); }
    });
    await check('准备中暂停与立即恢复；设备断开后不自动反复申请权限', async () => {
      const f = await fixture(browser, { slowPrepare: true });
      try {
        await f.page.getByRole('button', { name: '开始自动对谈', exact: true }).click();
        await f.page.getByRole('button', { name: '暂停对谈', exact: true }).click();
        await f.page.getByRole('button', { name: '继续自动对谈', exact: true }).click();
        await f.until(f.listening, '准备中暂停后不能恢复');
        await f.page.evaluate(() => { const track = window.__streams.at(-1).getTracks()[0]; track.stop(); track.dispatchEvent(new Event('ended')); });
        await f.until(() => f.page.locator('.conversation-state').getAttribute('data-phase').then(p => p === 'error'), '没有报告设备断开');
        const calls = await f.page.evaluate(() => window.__micCalls);
        await f.advance(12000);
        assert.equal(await f.page.evaluate(() => window.__micCalls), calls);
        await f.page.getByRole('button', { name: '重试自动对谈', exact: true }).click();
        await f.until(f.listening, '设备重试后不能恢复');
        assert.deepEqual(f.errors, []);
      } finally { await f.context.close(); }
    });
    await check('5 秒与 8 秒停顿设置下一轮生效；暂停指令可以恢复', async () => {
      const f = await fixture(browser);
      try {
        await f.start();
        await f.page.getByLabel('说话停顿等待').selectOption('5000');
        await f.sayRound(); // 当前轮仍采用启动时的 3 秒。
        await f.voice(); await f.advance(3500);
        assert.equal(f.stats.submissions.length, 1);
        await f.advance(2000); await f.until(() => f.stats.submissions.length === 2, '5 秒设置未生效');
        await f.until(f.listening, '未接续');
        await f.page.getByLabel('说话停顿等待').selectOption('8000');
        await f.page.getByRole('button', { name: '我说完了', exact: true }).click();
        await f.until(f.listening, '空轮次未接续');
        await f.voice(); await f.advance(5500);
        assert.equal(f.stats.submissions.length, 2);
        await f.advance(3000); await f.until(() => f.stats.submissions.length === 3, '8 秒设置未生效');
        await f.until(f.listening, '未接续');
        // 模拟服务器对明确暂停口令的响应；它必须只消费一次。
        const control = turn('pause-control', 'assistant', '好的，我们先停一停。'); control.control = 'pause';
        f.data.turns.push(control);
        await f.page.evaluate(data => window.__snapshot(data), f.data);
        await f.until(() => f.page.locator('.conversation-state').getAttribute('data-phase').then(p => p === 'paused'), '口令未暂停');
        await f.page.getByRole('button', { name: '继续自动对谈', exact: true }).click();
        await f.until(f.listening, '暂停指令导致无法恢复');
        assert.deepEqual(f.errors, []);
      } finally { await f.context.close(); }
    });
    await check('多次转写故障保留原录音，刷新后以同一轮次恢复发送', async () => {
      const f = await fixture(browser);
      try {
        await f.start(); f.stats.failUploads = 99;
        await f.voice(); await f.advance(3500);
        await f.until(() => f.page.locator('.conversation-state').getAttribute('data-phase').then(p => p === 'error'), '没有进入可恢复错误状态');
        const saved = await f.page.evaluate(() => JSON.parse(localStorage.getItem('conversation-pending-session-auto')));
        assert.ok(saved.id);
        await f.page.getByRole('button', { name: '暂停对谈', exact: true }).click();
        await f.page.reload();
        await f.page.getByRole('button', { name: '重试转写并发送', exact: true }).waitFor();
        f.stats.failUploads = 0;
        await f.page.getByRole('button', { name: '重试转写并发送', exact: true }).click();
        await f.until(() => f.stats.submissions.length === 1, '刷新后录音不能恢复');
        assert.equal(f.stats.submissions[0].client_id, saved.id);
        assert.equal(await f.page.evaluate(() => localStorage.getItem('conversation-pending-session-auto')), null);
        assert.deepEqual(f.errors, []);
      } finally { await f.context.close(); }
    });
    await check('实际 WebAudio + AudioWorklet + 本地 Silero/WASM 识别人声并自动轮转', async () => {
      const f = await fixture(browser, { model: true });
      try {
        await f.start();
        assert.equal(await f.page.getByText(/已切换到音量检测/).count(), 0, '不能把回退冒充模型通过');
        await f.page.evaluate(async () => {
          const audio = window.__inputAudio;
          const buffer = await audio.decodeAudioData(await (await fetch('/synthetic-speech.wav')).arrayBuffer());
          const source = audio.createBufferSource(); source.buffer = buffer;
          source.connect(window.__inputDestination); source.start();
        });
        await f.until(() => f.stats.submissions.length === 1, '真实推理没有自动提交');
        await f.until(f.listening, '真实推理后没有继续收音');
        assert.ok(f.stats.modelAssets.some(s => s.endsWith('.wasm')));
        assert.ok(f.stats.modelAssets.some(s => s.endsWith('.onnx')));
        assert.deepEqual(f.stats.unexpected, []); assert.deepEqual(f.errors, []);
      } finally { await f.context.close(); }
    });
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify({ passed, fixture: '合成 API、合成声音；未使用真人录音或真实模型接口', realMicrophoneVerified: false }, null, 2));
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
