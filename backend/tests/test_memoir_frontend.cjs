// 私密时叙录的离线浏览器回归：所有请求均被拦截，不启服务、不访问公网。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const {html, serveScript, scripts} = require('./frontend_fixture.cjs');

const root = path.resolve(__dirname, '../..');
const screenshots = path.join(root, 'data/qa/memoir');
const pid = '11111111111111111111111111111111';
const sid = '22222222222222222222222222222222';
const hid = '33333333333333333333333333333333';
const openingId = '44444444444444444444444444444444';
const nextId = '55555555555555555555555555555555';
const eventId = '66666666666666666666666666666666';
const chapterId = '77777777777777777777777777777777';
const story = '我在1985年离开家乡，第一份工作是在纺织厂维修机器。<img src=x onerror=alert(1)>';

function mockDevice() {
  window.memoirDeviceEvents = [];
  window.memoirTracks = [];
  let voices = [{name: '远程中文（禁止使用）', lang: 'zh-CN', localService: false}];
  const listeners = new Map();
  const synth = {
    getVoices: () => voices,
    cancel() {window.memoirDeviceEvents.push('speech:cancel');},
    speak(value) {
      window.memoirDeviceEvents.push('speech:' + value.voice.name);
      if (window.memoirTracks.some(track => track.readyState === 'live')) throw new Error('录音时不得同时播报');
      if (value.voice.localService !== true) throw new Error('不得使用未知远程声音');
    },
    addEventListener(event, handler) {listeners.set(event, handler);},
  };
  Object.defineProperty(window, 'speechSynthesis', {value: synth});
  window.SpeechSynthesisUtterance = class {constructor(text) {this.text = text;}};
  window.memoirEnableLocalVoice = () => {
    voices.push({name: '本地中文', lang: 'zh-CN', localService: true});listeners.get('voiceschanged')?.();
  };
  Object.defineProperty(navigator, 'mediaDevices', {value: {getUserMedia: async () => {
    window.memoirDeviceEvents.push('mic:open');
    const track = {readyState: 'live', stop() {this.readyState = 'ended';window.memoirDeviceEvents.push('mic:stop');}};
    window.memoirTracks.push(track);return {getTracks: () => [track]};
  }}});
  window.MediaRecorder = class {
    static isTypeSupported(type) {return type.includes('webm');}
    constructor(stream, options) {this.stream = stream;this.mimeType = options?.mimeType || 'audio/webm';this.state = 'inactive';}
    start() {this.state = 'recording';window.memoirDeviceEvents.push('record:start');}
    stop() {
      this.state = 'inactive';
      queueMicrotask(() => {
        this.ondataavailable?.({data: new Blob(['private-offline-audio'], {type: this.mimeType})});
        this.onstop?.();
      });
    }
  };
  window.EventSource = class {close() {}};
}

function multipartValue(request, name) {
  const value = request.postDataBuffer().toString('utf8');
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return value.match(new RegExp(`name="${escaped}"\\r\\n\\r\\n([^\\r]*)`))?.[1];
}

(async () => {
  const source = scripts.get('assets/memoir.js');
  assert(source && !/\blocalStorage\b|\bsessionStorage\b|indexedDB/.test(source), '时叙录不可持久化身份、正文或任务进度');
  assert(!/\.innerHTML\s*=/.test(source), '人生故事与AI内容只能以文字生成DOM');
  for (const id of ['scrSetup', 'scrLive', 'captureHub', 'liveWorkspace', 'outline', 'covwrap', 'scrProject']) {
    assert(html.includes(`id="${id}"`), `保留现有UI与访谈功能：${id}`);
  }
  assert.match(html, /id="memoirEntry"[^>]*onclick="Memoir\.open\(\)"/);
  const browser = await chromium.launch({headless: true, channel: process.env.PLAYWRIGHT_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}});
    page.setDefaultTimeout(10000);await page.addInitScript(mockDevice);
    const errors = [], submitted = [], createPayloads = [], chapterPayloads = [];
    page.on('pageerror', error => errors.push(error.message));
    let acceptDialogs = true;
    page.on('dialog', async dialog => {if (acceptDialogs) await dialog.accept();else await dialog.dismiss();});
    let authenticated = false, account = '记录者甲', failNextTurn = true, expireProfiles = false, failLogout = false;
    let profiles = [], sessions = [], turns = [], memories = [], chapters = [], tasks = [];
    let taskPolls = new Map(), finishCount = 0, deleted = false;
    const question = text => ({text, turn: {id: openingId, role: 'assistant', text}, degraded: false, paused: false});
    const routeHandler = async route => {
      const request = route.request(), pathname = new URL(request.url()).pathname;
      if (pathname === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (await serveScript(route, pathname)) return;
      if (pathname.startsWith('/assets/')) return route.fulfill({status: 204});
      const send = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (pathname === '/api/auth/me') return send({user: {id: -1, name: '原访谈使用人'}, auth_enabled: false});
      if (!pathname.startsWith('/api/memoir/')) throw new Error('不应跨入旧访谈API：' + pathname);
      if (pathname === '/api/memoir/auth/me') return authenticated ? send({user: {id: 8, name: account}}) : send({detail: '请单独登录'}, 401);
      if (pathname === '/api/memoir/auth/login') {
        assert.equal(request.postDataJSON().password, '测试密码');authenticated = true;return send({user: {id: 8, name: account}});
      }
      if (pathname === '/api/memoir/auth/logout') {if (failLogout) {failLogout = false;return route.abort('failed');}authenticated = false;return send({ok: true});}
      if (!authenticated) return send({detail: '验证已失效'}, 401);
      if (pathname === '/api/memoir/profiles' && request.method() === 'GET') {
        if (expireProfiles) {expireProfiles = false;authenticated = false;return send({detail: '验证过期'}, 401);}
        return send({profiles});
      }
      if (pathname === '/api/memoir/profiles' && request.method() === 'POST') {
        const body = request.postDataJSON();createPayloads.push(body);
        assert.equal(body.recording_consent, true);assert.equal(body.cloud_consent, true);
        profiles = [{id: pid, name: body.name, description: body.description, updated_at: 1792020000}];
        return send({profile: profiles[0]}, 201);
      }
      if (pathname === '/api/memoir/profiles/' + pid) {
        if (request.method() === 'DELETE') {profiles = [];sessions = [];turns = [];memories = [];chapters = [];tasks = [];deleted = true;return send({ok: true, audio_cleanup_pending: 0});}
        return send({profile: profiles[0], sessions, memories, chapters, tasks});
      }
      if (pathname === `/api/memoir/profiles/${pid}/sessions` && request.method() === 'POST') {
        sessions = [{id: sid, title: '第一场往事', status: 'live', created_at: 1792020000}];
        const value = question('您愿意从哪段自己想讲的经历开始？');turns = [value.turn];return send({session: sessions[0], question: value}, 201);
      }
      if (pathname === `/api/memoir/profiles/${pid}/sessions/${sid}`) return send({session: sessions[0], turns});
      if (pathname === `/api/memoir/profiles/${pid}/sessions/${sid}/turn`) {
        const id = multipartValue(request, 'client_turn_id');const text = multipartValue(request, 'text');
        const audio = request.postDataBuffer().toString('utf8').includes('private-offline-audio');
        submitted.push({id, text, audio});
        assert.match(id, /^[a-f0-9-]{36}$/);
        assert(!page.isClosed());
        const liveTracks = await page.evaluate(() => window.memoirTracks.some(track => track.readyState === 'live'));
        assert.equal(liveTracks, false, '任何上云请求之前必须先停止麦克风');
        if (failNextTurn) {failNextTurn = false;return send({detail: '模拟临时网络失败'}, 503);}
        const human = {id: hid + submitted.length, role: 'human', text: text || story,
          audio_url: audio ? `api/memoir/profiles/${pid}/audio/${hid}` : 'https://other.invalid/private.wav'};
        const follow = {text: '那份工作中，您记得最清楚的一天发生了什么？',
          turn: {id: nextId + submitted.length, role: 'assistant', text: '那份工作中，您记得最清楚的一天发生了什么？'}};
        turns.push(human, follow.turn);return send({human_turn: human, question: follow});
      }
      if (pathname === `/api/memoir/profiles/${pid}/sessions/${sid}/finish`) {
        finishCount++;sessions[0].status = 'done';tasks.push({id: 'memory-task', kind: 'memoir_memory', status: 'running', stage: '整理线索'});
        return send({session: sessions[0], task_id: 'memory-task'}, 202);
      }
      if (pathname === `/api/memoir/profiles/${pid}/chapters` && request.method() === 'POST') {
        chapterPayloads.push(request.postDataJSON());
        tasks.push({id: 'chapter-task', kind: 'memoir_chapter', status: 'running', stage: '核对原话'});
        return send({task_id: 'chapter-task'}, 202);
      }
      if (pathname === '/api/memoir/tasks/memory-task' || pathname === '/api/memoir/tasks/chapter-task') {
        const id = pathname.split('/').at(-1), count = (taskPolls.get(id) || 0) + 1;taskPolls.set(id, count);
        const task = tasks.find(task => task.id === id);task.status = count >= 2 ? 'ready' : 'running';
        if (task.status === 'ready' && id === 'memory-task') memories = [{id: eventId, title: '第一份工作', date_text: '1985年（本人回忆）',
          description: '离开家乡后在纺织厂维修机器。', status: 'candidate', evidence: [{turn_id: hid + '2', quote: '第一份工作是在纺织厂维修机器。'}]}];
        if (task.status === 'ready' && id === 'chapter-task') chapters = [{id: chapterId, title: '离开家乡', status: 'draft', source_stale: false,
          paragraphs: [{text: story, evidence: [{turn_id: hid + '2', quote: '我在1985年离开家乡'}]}]}];
        return send(task);
      }
      if (pathname === `/api/memoir/profiles/${pid}/events/${eventId}/confirm`) {memories[0].status = 'confirmed';return send({memory: memories[0]});}
      if (pathname === `/api/memoir/profiles/${pid}/chapters/${chapterId}/confirm`) {chapters[0].status = 'confirmed';return send({chapter: chapters[0]});}
      if (pathname.startsWith(`/api/memoir/profiles/${pid}/audio/`)) return route.fulfill({status: 204});
      throw new Error('未预期私密API：' + request.method() + ' ' + pathname);
    };
    await page.route('**/*', routeHandler);await page.goto('https://interview.test/');
    await page.waitForFunction(() => document.getElementById('authGate').classList.contains('hidden'));
    const baselineStorage = await page.evaluate(() => Object.keys(localStorage));
    await page.locator('#memoirEntry').click();await page.locator('#memoirLogin').waitFor({state: 'visible'});
    assert(await page.locator('#authGate').evaluate(el => el.classList.contains('hidden')), '独立401不应遮盖原站登录');
    assert.match(await page.locator('#memoirLogin').innerText(), /单独验证/);
    await page.locator('#memoirLoginName').fill(account);await page.locator('#memoirLoginPassword').fill('测试密码');
    await page.locator('#memoirLoginSubmit').click();await page.locator('#memoirLibrary').waitFor({state: 'visible'});
    assert.equal(await page.locator('#memoirLoginPassword').inputValue(), '');
    await page.locator('#memoirCreateDetails summary').click();
    await page.locator('#memoirProfileName').fill('妈妈的人生故事');await page.locator('#memoirProfileDescription').fill('留下值得记住的往事。');
    assert.equal(await page.locator('#memoirRecordingConsent').isChecked(), false);
    assert.equal(await page.locator('#memoirCloudConsent').isChecked(), false);
    await page.locator('#memoirCreateSubmit').click();assert.equal(createPayloads.length, 0, '未单独同意不得建档');
    await page.locator('#memoirRecordingConsent').check();await page.locator('#memoirCloudConsent').check();
    await page.locator('#memoirCreateSubmit').click();await page.locator('#memoirProfile').waitFor({state: 'visible'});
    assert.equal(createPayloads.length, 1);
    await page.locator('#memoirStartSession').click();await page.locator('#memoirQuestion').filter({hasText: '愿意'}).waitFor();
    assert(await page.locator('#memoirSpeak').isDisabled(), '远程中文音色不可用于私密追问');
    assert.match(await page.locator('#memoirVoiceNote').innerText(), /本地中文音色/);
    await page.evaluate(() => window.memoirEnableLocalVoice());await page.locator('#memoirSpeak').click();
    assert((await page.evaluate(() => window.memoirDeviceEvents)).includes('speech:本地中文'));
    await page.locator('#memoirRecord').click();
    await page.waitForFunction(() => document.getElementById('memoirRecord').getAttribute('aria-pressed') === 'true');
    assert(await page.locator('#memoirSpeak').isDisabled());assert(await page.locator('#memoirText').isDisabled());
    await page.locator('#memoirRecord').click();
    await page.waitForFunction(() => document.getElementById('memoirStatus').textContent.includes('模拟临时网络失败'));
    assert.equal(submitted.length, 1);assert.equal(submitted[0].audio, true);
    assert(await page.locator('#memoirPreview').isVisible(), '失败保留当前Blob用于回听和重试');
    assert(await page.locator('#memoirText').isDisabled(), '失败重试锁定同一份原始载荷，避免标识复用覆盖');
    acceptDialogs = false;await page.locator('#memoirExit').click();assert(await page.locator('#scrMemoir').isVisible());acceptDialogs = true;
    await page.locator('#memoirSend').click();await page.waitForFunction(() => document.getElementById('memoirTurns').textContent.includes('纺织厂'));
    assert.equal(submitted[0].id, submitted[1].id, '失败重试必须复用client_turn_id');
    assert.equal(await page.locator('#memoirTurns img, #memoirTurns script').count(), 0, '原话中的HTML不能执行');
    assert.equal(await page.locator('#memoirPreview').isVisible(), false);
    assert(await page.locator('#memoirText').isEnabled());
    const events = await page.evaluate(() => window.memoirDeviceEvents);
    assert(events.indexOf('speech:cancel', events.indexOf('speech:本地中文')) < events.indexOf('mic:open'));
    assert(await page.evaluate(() => window.memoirTracks.every(track => track.readyState === 'ended')));
    await page.locator('#memoirText').fill('我后来换过工作，但具体年份记不清了。');await page.locator('#memoirSend').click();
    await page.waitForFunction(() => document.getElementById('memoirTurns').textContent.includes('具体年份记不清'));
    assert.notEqual(submitted[1].id, submitted[2].id, '成功保存后下一轮使用新标识');
    await page.locator('#memoirFinish').click();
    await page.waitForFunction(() => document.getElementById('memoirStatus').textContent.includes('记忆整理已完成'));
    assert.equal(finishCount, 1);assert(await page.locator('#memoirFinish').isDisabled());
    await page.locator('#memoirTimelineTab').click();await page.locator('#memoirMemories').getByRole('button', {name: '核对原话后确认'}).click();
    await page.waitForFunction(() => document.getElementById('memoirMemories').textContent.includes('已人工确认'));
    await page.locator('#memoirBookTab').click();await page.locator('#memoirGenerateChapter').click();
    await page.waitForFunction(() => document.getElementById('memoirChapters').textContent.includes('离开家乡'));
    assert.deepEqual(chapterPayloads, [{}], '空主题应使用后端默认，不发送无效空字符串');
    assert.equal(await page.locator('#memoirChapters a').count(), 0, '未确认草稿不得提供导出');
    await page.locator('#memoirChapters').getByRole('button', {name: '核对后确认本版本'}).click();
    await page.waitForFunction(() => document.querySelectorAll('#memoirChapters a').length === 2);
    assert.match(await page.locator('#memoirChapters a').first().getAttribute('href'), /api\/memoir\/profiles\/.*\/export\?format=md/);
    assert.equal(await page.locator('#memoirChapters img').count(), 0);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '手机布局不得横向溢出');
    fs.mkdirSync(screenshots, {recursive: true});
    await page.screenshot({path: path.join(screenshots, 'memoir-book-mobile.png'), fullPage: true, animations: 'disabled'});
    await page.locator('#memoirTimelineTab').click();
    await page.locator('#memoirMemories').getByRole('button', {name: '查看原始讲述'}).click();
    await page.locator('#memoirStoryPanel').waitFor({state: 'visible'});
    await page.waitForFunction(() => document.getElementById('memoirTurns').textContent.includes('纺织厂'));
    assert(await page.locator('#memoirStoryPanel').isVisible());assert.equal(await page.locator('#memoirRecordControls').isVisible(), false, '已结束讲述只读回看');
    assert.equal(await page.locator('#memoirTurns audio').count(), 1);

    expireProfiles = true;await page.locator('#memoirProfilesBack').click();await page.locator('#memoirLogin').waitFor({state: 'visible'});
    assert.equal(await page.locator('#memoirProfileTitle').textContent(), '');
    assert.equal(await page.locator('#memoirTurns').textContent(), '');assert.equal(await page.locator('#memoirChapters').textContent(), '');
    assert(await page.locator('#authGate').evaluate(el => el.classList.contains('hidden')));
    assert(await page.locator('#memoirLoginSubmit').isEnabled(), '401之后不能把登录按钮锁死');
    await page.locator('#memoirLoginName').fill(account);await page.locator('#memoirLoginPassword').fill('测试密码');await page.locator('#memoirLoginSubmit').click();
    await page.locator('#memoirProfiles').getByRole('button', {name: '打开人物档案'}).click();
    await page.locator('#memoirStartSession').click();await page.locator('#memoirRecord').click();
    await page.waitForFunction(() => document.getElementById('memoirRecord').getAttribute('aria-pressed') === 'true');
    await page.evaluate(() => {Object.defineProperty(document, 'hidden', {configurable: true, value: true});document.dispatchEvent(new Event('visibilitychange'));});
    await page.waitForFunction(() => document.getElementById('memoirStatus').textContent.includes('页面进入后台'));
    assert(await page.locator('#memoirPreview').isVisible());assert(await page.evaluate(() => window.memoirTracks.every(track => track.readyState === 'ended')));
    await page.evaluate(() => {Object.defineProperty(document, 'hidden', {configurable: true, value: false});document.dispatchEvent(new Event('visibilitychange'));});
    const submitsBeforeExit = submitted.length;
    await page.locator('#memoirExit').click();await page.locator('#scrSetup.on').waitFor();
    assert.equal(submitted.length, submitsBeforeExit, '离开不能悄悄上传未提交录音');
    assert.equal(await page.locator('#memoirProfileTitle').textContent(), '');assert.equal(await page.locator('#memoirText').inputValue(), '');
    assert.equal(await page.locator('#memoirPreview').getAttribute('src'), null);
    assert.deepEqual(await page.evaluate(() => Object.keys(localStorage)), baselineStorage, '不得往旧访谈localStorage写入私密内容');
    await page.locator('#memoirEntry').click();await page.locator('#memoirProfiles').getByRole('button', {name: '打开人物档案'}).click();
    await page.locator('#memoirDeleteProfile').click();await page.waitForFunction(() => document.getElementById('memoirStatus').textContent.includes('已永久删除'));
    assert(deleted);assert.equal(await page.locator('#memoirProfileTitle').textContent(), '');
    failLogout = true;await page.locator('#memoirLogout').click();
    await page.waitForFunction(() => document.getElementById('memoirStatus').textContent.includes('服务器退出尚未确认'));
    assert(await page.locator('#memoirLogout').isVisible(), '退出网络失败必须允许重试，不可假称Cookie已撤销');
    assert.equal(await page.locator('#memoirIdentity').textContent(), '');
    await page.locator('#memoirLogout').click();await page.waitForFunction(() => document.getElementById('memoirStatus').textContent.includes('已退出私密空间'));
    assert.equal(await page.locator('#memoirIdentity').textContent(), '');
    account = '记录者乙';await page.locator('#memoirLoginName').fill(account);await page.locator('#memoirLoginPassword').fill('测试密码');await page.locator('#memoirLoginSubmit').click();
    await page.locator('#memoirLibrary').waitFor({state: 'visible'});
    assert.match(await page.locator('#memoirProfiles').textContent(), /还没有人物档案/);
    assert.equal(await page.locator('#memoirProfileTitle').textContent(), '');
    await page.evaluate(() => {
      dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}));
      dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}));
    });
    await page.locator('#scrSetup.on').waitFor();
    await page.locator('#memoirEntry').click();await page.locator('#memoirLibrary').waitFor({state: 'visible'});
    assert.match(await page.locator('#memoirIdentity').textContent(), /记录者乙/, 'BFCache恢复后可安全重新验证，不得卡成无法提交的死页面');
    assert.deepEqual(errors, []);
    const duplicateIds = await page.evaluate(() => {
      const seen = new Set();return [...document.querySelectorAll('[id]')].filter(element => seen.has(element.id) || !seen.add(element.id)).map(element => element.id);
    });
    assert.deepEqual(duplicateIds, []);
    console.log('PASS: 原UI保留、独立身份与401清空、分项同意、仅本地中文播报、半双工停麦后上传、幂等重试、后台暂停保留、原文防注入、记忆确认与章节导出、退出/删除隐私清理、手机布局');
    console.log('截图目录：' + screenshots);
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode = 1;});
