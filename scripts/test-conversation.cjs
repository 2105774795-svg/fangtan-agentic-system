const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = 'http://127.0.0.1:18998';
const output = 'data/qa/conversation'; fs.mkdirSync(output, { recursive: true });
(async () => {
 const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] });
 try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, permissions: ['microphone'] });
  await context.addInitScript(() => {
   window.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
   Object.defineProperty(window, 'speechSynthesis', { value: { getVoices: () => [{ lang: 'zh-CN', localService: true }], speak: u => { window.__spoken = (window.__spoken || []).concat(u.text); setTimeout(() => u.onend?.(), 60); }, cancel: () => {} } });
  });
  const page = await context.newPage(); const errors = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto(base);
  await page.getByLabel('今天您想调研什么？').fill('我想记录爷爷的口述史 ' + Date.now());
  await page.getByRole('button', { name: '开始调研', exact: true }).click();
  await page.getByText('您好，很高兴和您聊聊天。以后我应该怎么称呼您？', { exact: true }).waitFor();
  assert.equal(await page.getByTestId('research-map').count(), 0);
  assert.equal(await page.getByRole('button', { name: '开始聊天', exact: true }).isEnabled(), false);
  const pid = new URL(page.url()).searchParams.get('project');
  const readArchive = async () => (await page.request.get(`${base}/api/platform/projects/${pid}/conversation-archive`)).json();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: '用文字说', exact: true }).click();
  await page.getByLabel('写下您想讲的内容').fill('小时候，母亲经常带我去赶集。');
  await page.getByRole('button', { name: '发送这段话', exact: true }).click();
  await page.waitForFunction(() => { const t = document.querySelector('.conversation-current')?.textContent; return t?.startsWith('您提到') && t.length < 25; });
  await page.getByText('您提到和母亲赶集的往事，那时最盼着吃到什么？', { exact: true }).waitFor();
  await page.reload();
  await page.getByText('小时候，母亲经常带我去赶集。', { exact: false }).first().waitFor();
  await page.getByRole('button', { name: '今天先聊到这里', exact: true }).click();
  await page.getByRole('heading', { name: '谢谢您，把这些故事留了下来。' }).waitFor();
  await page.reload();
  await page.getByRole('heading', { name: '谢谢您，把这些故事留了下来。' }).waitFor();
  await page.getByRole('button', { name: '看看我的档案', exact: true }).click();
  await page.getByText('小时候，母亲经常带我去赶集。', { exact: true }).first().waitFor();
  await page.getByRole('button', { name: '查看这段的原话与录音', exact: true }).click();
  await page.getByRole('region', { name: '原话依据' }).waitFor();
  await page.screenshot({ path: `${output}/archive.png`, fullPage: true });
  await page.getByRole('button', { name: '收起原话', exact: true }).click();
  await page.getByRole('button', { name: '修改这段故事', exact: true }).click();
  await page.getByLabel('修改故事整理稿（原始讲述保持不变）').fill('小时候母亲带我赶集，这是我看过的整理稿。');
  await page.getByRole('button', { name: '保存修改', exact: true }).click();
  await page.getByText('本段经人工修改，原话依据保留供对照。', { exact: true }).waitFor();
  await page.getByRole('button', { name: '这次故事我看过了', exact: true }).click();
  await page.getByText('已审阅 · 保留原话依据', { exact: true }).waitFor();

  await page.getByRole('button', { name: '继续聊聊', exact: true }).click();
  await page.getByText('很高兴又和您聊天。您愿意接着上次的故事讲，还是聊一段新的回忆？', { exact: true }).waitFor();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: '开始聊天', exact: true }).click();
  await page.getByRole('button', { name: '我说完了', exact: true }).waitFor();
  assert.ok(await page.evaluate(() => window.__spoken.length > 0));
  await page.waitForTimeout(11200);
  const before = await readArchive();
  const current = before.sessions.find(s => s.status === 'live');
  assert.equal(current.turns.filter(t => t.role === 'human').length, 0, '10 second chunks cannot trigger AI turns');
  await page.getByRole('button', { name: '我说完了', exact: true }).click();
  await page.waitForFunction(async project => { const value = await (await fetch(`/api/platform/projects/${project}/conversation-archive`)).json(); const s = value.sessions.find(s => s.status === 'live'); return s?.turns.some(t => t.role === 'human') && s.turns.at(-1)?.job?.status === 'ready'; }, pid);
  await page.getByRole('button', { name: '我说完了', exact: true }).waitFor(); // automatic next listening after TTS
  await page.waitForTimeout(900);
  // Leave while ASR is unavailable; persisted audio remains retryable on return.
  await page.route('**/conversations/*/audio', route => route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ detail: '模拟转写暂未完成' }) }));
  await page.getByRole('button', { name: '暂停一下', exact: true }).click();
  await page.getByRole('button', { name: '重试转写并发送', exact: true }).click();
  await page.getByRole('button', { name: '返回首页', exact: true }).click();
  await page.locator('.home-composer').waitFor();
  await page.unroute('**/conversations/*/audio');
  await page.goto(`${base}/?project=${pid}`);
  await page.getByRole('button', { name: '重试转写并发送', exact: true }).waitFor();
  await page.getByRole('button', { name: '重试转写并发送', exact: true }).click();
  await page.waitForFunction(async project => { const a = await (await fetch(`/api/platform/projects/${project}/conversation-archive`)).json(); return a.sessions.find(s => s.status === 'live')?.turns.at(-1)?.job?.status === 'ready'; }, pid);
  await page.screenshot({ path: `${output}/conversation.png`, fullPage: true });
  const value = await readArchive(); const live = value.sessions.find(s => s.status === 'live');
  assert.ok(live.turns.find(t => t.role === 'human').audio_ids.length >= 2, 'independently decodable audio segments');
  const mobile = await context.newPage(); await mobile.setViewportSize({ width: 390, height: 844 }); await mobile.goto(`${base}/?project=${pid}`);
  await mobile.getByRole('button', { name: '开始聊天', exact: true }).waitFor();
  assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await mobile.screenshot({ path: `${output}/mobile.png`, fullPage: true });
  // Exercise automatic silence boundary independently from file segmentation.
  const vad = await context.newPage();
  await vad.addInitScript(() => {
    window.AudioContext = class {
      state = 'running';
      async resume() {}
      async close() { this.state = 'closed'; }
      createMediaStreamSource() { return { connect() {} }; }
      createAnalyser() { let calls = 0; return { fftSize: 1024, getFloatTimeDomainData(array) { window.__vadCalls = calls; array.fill(calls++ < 5 ? .1 : 0); } }; }
    };
  });
  const vadProjectResponse = await page.request.post(base + '/api/platform/projects', { data: { prompt: '口述史自动停顿独立测试', scenario: 'memoir', mode: 'ai', request_id: 'vad-' + Date.now() } });
  const vadPid = (await vadProjectResponse.json()).id;
  await vad.goto(`${base}/?project=${vadPid}`);
  await vad.bringToFront();
  await vad.getByRole('checkbox').check();
  const baseline = 0;
  await vad.getByRole('button', { name: '开始聊天', exact: true }).click();
  await vad.waitForFunction(async ({ pid, baseline }) => { const value = await (await fetch(`/api/platform/projects/${pid}/conversation-archive`)).json(); return value.sessions.find(s => s.status === 'live').turns.filter(t => t.role === 'human').length > baseline; }, { pid: vadPid, baseline }, { timeout: 20000 }).catch(async e => { console.log('VAD diagnostic', await vad.locator('.conversation-main').innerText(), await vad.evaluate(() => ({calls:window.__vadCalls,spoken:window.__spoken}))); throw e; });
  await vad.getByRole('button', { name: '返回首页', exact: true }).click();
  assert.deepEqual(errors, []);
  console.log('PASS: direct memoir routing, consent, text + real streaming, refresh recovery, source-based archive, cross-session continuity, real segmented recorder + synthetic ASR, automatic voice turns, failure exit/recovery, mobile.');
 } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
