// 双入口离线验收：真实页面与脚本，所有网络均拦截，不启动服务或访问线上。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const {html, serveScript} = require('./frontend_fixture.cjs');

const screenshots = path.resolve(__dirname, '../../data/qa/dual-entry');
const questions = ['这个市场近一个月客流有何变化？', '现在一个档口的月租金是多少？', '接下来准备如何调整经营？'];
function gate() {
  let release, notify;
  return {released: new Promise(resolve => {release = resolve;}), requested: new Promise(resolve => {notify = resolve;}),
    release: () => release(), notify: () => notify()};
}

function mockDevices() {
  window.testMicrophoneRequests = 0;
  window.testTracks = [];
  Object.defineProperty(navigator, 'mediaDevices', {value: {getUserMedia: async () => {
    window.testMicrophoneRequests++;
    const track = {readyState: 'live', stop() {this.readyState = 'ended';}, addEventListener() {}};
    window.testTracks.push(track);return {active: true, getTracks: () => [track]};
  }}});
  window.MediaRecorder = class {
    static isTypeSupported() {return true;}
    constructor() {this.state = 'inactive';this.mimeType = 'audio/webm';}
    start() {this.state = 'recording';}
    stop() {
      this.state = 'inactive';queueMicrotask(() => {
        this.ondataavailable?.({data: new Blob(['offline-dual-entry-audio'], {type: this.mimeType})});this.onstop?.();
      });
    }
  };
  window.EventSource = class {close() {}};
  Object.defineProperty(window, 'speechSynthesis', {value: {getVoices: () => [], cancel() {}, addEventListener() {}}});
}

async function assertScreen(page, id) {
  await page.locator(`#${id}.on`).waitFor();
  assert.deepEqual(await page.locator('.screen.on').evaluateAll(screens => screens.map(el => el.id)), [id], '只能显示一个主页面');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), '桌面和手机均不应横向溢出');
}

(async () => {
  assert(!html.includes('人生之书'), '页面所有旧名称均应改为时叙录');
  for (const id of ['scrSetup', 'scrInterview', 'interviewEntry', 'interviewExit', 'memoirEntry',
    'scrRecordPrepare', 'scrLive', 'liveHomeBtn', 'setupFormHost', 'liveForm', 'memoirExit']) {
    assert(html.includes(`id="${id}"`), `必须保留或新增稳定入口与原功能：${id}`);
  }
  fs.mkdirSync(screenshots, {recursive: true});
  const browser = await chromium.launch({headless: true, channel: process.env.PLAYWRIGHT_BROWSER_CHANNEL || undefined});
  try {
    for (const width of [1440, 390, 320]) {
      const page = await browser.newPage({viewport: {width, height: width >= 1000 ? 1000 : 844}});
      page.setDefaultTimeout(10000);await page.addInitScript(mockDevices);
      const errors = [], unexpected = [], privateRequests = [];
      const state = {settings: null, creates: 0, uploads: 0, imports: 0, authenticated: false, holdOutline: true, holdSession: false,
        holdAudio: false, holdRecentMeta: false, holdRecentState: false, importReady: false};
      const gates = {audio: gate(), recentMeta: gate(), recentState: gate(), report: gate()};
      let releaseOutline, outlineRequested;
      const heldOutline = new Promise(resolve => {releaseOutline = resolve;});
      const outlineStarted = new Promise(resolve => {outlineRequested = resolve;});
      let releaseSession, sessionRequested;
      const heldSession = new Promise(resolve => {releaseSession = resolve;});
      const sessionStarted = new Promise(resolve => {sessionRequested = resolve;});
      const session = {id: 'dual-entry-session', status: 'live', subject: '', outline: [], utterances: [], suggestions: []};
      let acceptDialogs = true;
      page.on('pageerror', error => errors.push(error.message));
      page.on('dialog', async dialog => acceptDialogs ? dialog.accept() : dialog.dismiss());
      await page.route('**/*', async route => {
        const request = route.request(), pathname = new URL(request.url()).pathname;
        if (pathname === '/') return route.fulfill({contentType: 'text/html', body: html});
        if (await serveScript(route, pathname)) return;
        if (pathname.startsWith('/assets/')) return route.fulfill({status: 204});
        const send = (value, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(value)});
        if (pathname === '/api/auth/me') return send({user: {id: -1, name: '离线测试用户'}, auth_enabled: false});
        if (pathname === '/api/outlines/jobs' && request.method() === 'POST') {
          state.settings = request.postDataJSON();return send({job_id: 'dual-outline', status: 'generating'}, 202);
        }
        if (pathname === '/api/outlines/jobs/dual-outline') {
          outlineRequested();if (state.holdOutline) await heldOutline;
          return send({job_id: 'dual-outline', status: 'ready', settings: state.settings, questions,
            background: '本次指定市场以农产品交易为主，具体经营情况需结合访谈核实。', background_note: '离线合成测试背景。', sources: []});
        }
        if (pathname === '/api/sessions/' && request.method() === 'POST') {
          const data = request.postDataJSON();state.creates++;session.subject = data.subject;session.outline = data.outline;
          if (state.holdSession) {sessionRequested();await heldSession;}
          return send({session_id: session.id});
        }
        const coverage = () => ({mode: 'outline', ratio: 0, covered: [], missing: session.outline.map((_, i) => `outline:${i}`),
          dims: session.outline.map((q, i) => ({id: `outline:${i}`, item: q, name: q, icon: '问', covered: false, subs: [q]}))});
        if (pathname === `/api/sessions/${session.id}/state`) {
          if (state.holdRecentState) {gates.recentState.notify();await gates.recentState.released;}
          return send({...session, coverage: coverage()});
        }
        if (pathname === `/api/sessions/${session.id}/coverage`) return send(coverage());
        if (pathname === `/api/sessions/${session.id}/audio`) {
          state.uploads++;if (state.holdAudio) {gates.audio.notify();await gates.audio.released;}
          return send({ok: true, seq: state.uploads});
        }
        if (pathname === `/api/sessions/${session.id}`) {
          if (state.holdRecentMeta) {gates.recentMeta.notify();await gates.recentMeta.released;}
          return send(session);
        }
        if (pathname === '/api/sessions/') return send({items: [], total: 0});
        if (pathname === '/api/projects') return send({items: [], projects: []});
        if (pathname === '/api/management/overview') return send({todos: [], stats: {interviews: 0, recordings: 0, reports: 0, duration_minutes: 0}});
        if (pathname === '/api/imports/analyze' && request.method() === 'POST') {
          state.imports++;return send({job_id: 'dual-import', status: state.importReady ? 'ready' : 'running', session_id: 'imported-session',
            stage: '后台分析测试材料', progress: 15}, 202);
        }
        if (pathname === '/api/imports/dual-import') return send({job_id: 'dual-import', status: 'running', stage: '后台整理原文', progress: 30});
        if (pathname === '/api/sessions/imported-session/report.md') {
          gates.report.notify();await gates.report.released;
          return route.fulfill({contentType: 'text/markdown', body: '# 离线合成纪要，不得抢占私密空间'});
        }
        if (pathname.startsWith('/api/memoir/')) {
          privateRequests.push({pathname, method: request.method()});
          if (pathname === '/api/memoir/auth/me') return state.authenticated ? send({user: {id: 8, name: '私密记录者'}}) : send({detail: '需要单独验证'}, 401);
          if (pathname === '/api/memoir/auth/login') {
            assert.deepEqual(request.postDataJSON(), {name: '私密记录者', password: '离线测试密码'});
            state.authenticated = true;return send({user: {id: 8, name: '私密记录者'}});
          }
          if (pathname === '/api/memoir/auth/logout') {state.authenticated = false;return send({ok: true});}
          if (pathname === '/api/memoir/profiles' && state.authenticated) return send({profiles: []});
        }
        unexpected.push(request.method() + ' ' + pathname);return send({detail: '禁止未模拟的网络请求'}, 500);
      });
      await page.goto('https://interview.test/');
      await page.waitForFunction(() => document.getElementById('userName').value === '离线测试用户');
      await assertScreen(page, 'scrSetup');
      assert.equal(await page.locator('#scrSetup input:visible, #scrSetup textarea:visible, #scrSetup select:visible, #scrSetup .tabs:visible').count(), 0,
        '首页不直接暴露实战表单、录音模块和内部标签');
      assert.equal(await page.locator('#scrSetup #liveForm, #scrSetup #tabField, #scrSetup #tabRecord').count(), 0,
        '原功能应真正置于汇言笔页面，而非只用局部样式隐藏');
      assert.match(await page.locator('#interviewEntry').innerText(), /进入汇言笔/);
      assert.match(await page.locator('#memoirEntry').innerText(), /进入时叙录/);
      const cards = await page.locator('#interviewEntry, #memoirEntry').evaluateAll(buttons => buttons.map(button => {
        const card = button.closest('section'), box = card.getBoundingClientRect();
        const content = card.firstElementChild.getBoundingClientRect(), action = button.parentElement.getBoundingClientRect();
        return {top: box.top, bottom: box.bottom, left: box.left, width: box.width, height: box.height, title: card.querySelector('h2')?.textContent,
          contentWidth: content.width, contentBottom: content.bottom, actionTop: action.top};
      }));
      assert.equal(cards.length, 2);assert(cards[0].bottom < cards[1].top, '首页两个同级大入口应始终上下排列');
      assert(Math.abs(cards[0].left - cards[1].left) < 1 && Math.abs(cards[0].width - cards[1].width) < 1, '两个入口宽度与对齐一致');
      assert.deepEqual(cards.map(card => card.title), ['汇言笔', '时叙录']);
      assert(cards.every(card => card.height >= 120));
      if (width <= 600) {
        assert(cards.every(card => card.contentWidth >= card.width * 0.7), '手机两个入口的正文都应占满卡片，不得挤成单字窄列');
        assert(cards.every(card => card.actionTop >= card.contentBottom), '手机两个入口的按钮区域都应在文字下方单列排列');
        assert(cards.every(card => card.height <= 500), '手机入口高度不得因错误的桌面双列布局而异常膨胀');
      }
      await page.screenshot({path: path.join(screenshots, `${width}-landing.png`), fullPage: true, animations: 'disabled'});

      await page.locator('#interviewEntry').click();await assertScreen(page, 'scrInterview');
      assert(await page.locator('#tabField').isVisible());assert(await page.locator('#tabRecord').isVisible());
      await page.locator('#subject').fill('双福市场水果档口经营变化');
      await page.locator('#industry').selectOption('食品饮料');await page.locator('#regionCity').fill('重庆市');
      await page.locator('#regionMarket').fill('双福国际农贸城');await page.locator('#focus').fill('成本与客流');
      await page.locator('#outlineBtn').click();await outlineStarted;
      await page.locator('#interviewExit').click();await assertScreen(page, 'scrSetup');
      assert.equal(privateRequests.length, 0, '切换汇言笔与首页不得触发或借用时叙录身份');
      state.holdOutline = false;releaseOutline();
      await page.waitForFunction(() => document.querySelectorAll('#outlineChoices input').length === 3);
      await page.locator('#interviewEntry').click();await assertScreen(page, 'scrInterview');
      assert.equal(await page.locator('#subject').inputValue(), '双福市场水果档口经营变化');
      assert.equal(await page.locator('#outlineChoices input:checked').count(), 3, '返回首页不应取消后台提纲或丢失结果');
      await page.locator('#outlineChoices input').last().uncheck();
      await page.locator('#interviewExit').click();await page.reload();
      await page.waitForFunction(() => document.getElementById('userName').value === '离线测试用户');
      await assertScreen(page, 'scrSetup');await page.locator('#interviewEntry').click();
      assert.equal(await page.locator('#outlineChoices input:checked').count(), 2, '刷新后的首页仍能恢复汇言笔草稿和问题筛选');
      assert.equal(await page.locator('#regionMarket').inputValue(), '双福国际农贸城');
      await page.screenshot({path: path.join(screenshots, `${width}-interview.png`), fullPage: true, animations: 'disabled'});

      await page.locator('#tabRecord').click();await assertScreen(page, 'scrLive');
      assert(await page.locator('#recordEntryBtn').isVisible());assert.equal(await page.locator('#recordingExtras').evaluate(el => el.open), false);
      await page.locator('#recordEntryBtn').click();await assertScreen(page, 'scrRecordPrepare');
      assert.equal(await page.locator('#prepareFormHost #liveForm').count(), 1);
      await page.locator('#prepareBackBtn').click();await assertScreen(page, 'scrLive');
      assert.equal(state.creates, 0, '准备页返回不创建空会话');
      await page.locator('#recordEntryBtn').click();await page.locator('#outlineRecordBtn').click();
      await page.waitForFunction(() => document.getElementById('orb').classList.contains('rec'));
      assert.equal(state.creates, 1);assert.deepEqual(session.outline, questions.slice(0, 2));
      acceptDialogs = false;await page.locator('#liveHomeBtn').click();await assertScreen(page, 'scrLive');
      assert(await page.locator('#orb').evaluate(el => el.classList.contains('rec')), '取消返回应保留录音');
      acceptDialogs = true;state.holdAudio = true;await page.locator('#liveHomeBtn').click();await gates.audio.requested;
      await assertScreen(page, 'scrLive');assert(await page.locator('#orb').isDisabled(), '尾段上传期间不得再次点录音制造新的隐藏录音');
      const privateCountBeforeSaving = privateRequests.length;
      await page.evaluate(() => Memoir.open());assert.equal(privateRequests.length, privateCountBeforeSaving, '安全停录上传未完成时不得切换私密空间');
      gates.audio.release();await assertScreen(page, 'scrSetup');
      assert.equal(state.uploads, 1, '从现场返回首页须完成尾段上传');
      assert(await page.evaluate(() => window.testTracks.every(track => track.readyState === 'ended')), '返回首页应释放麦克风');
      await page.locator('#interviewEntry').click();await page.locator('#tabRecord').click();
      await page.locator('#recordingExtras summary').click();
      for (const name of ['历史会话', '全部待办', '继续最近访谈']) assert(await page.getByRole('button', {name, exact: true}).isVisible());
      assert(await page.locator('#captureImportTab').isVisible());assert(await page.locator('#captureProjectsTab').isVisible());
      await page.screenshot({path: path.join(screenshots, `${width}-recording.png`), fullPage: true, animations: 'disabled'});
      await page.locator('#captureImportTab').click();
      await page.locator('#importFile').setInputFiles({name: '离线双入口文字稿.txt', mimeType: 'text/plain', buffer: Buffer.from('甲：最近客流怎样？\n乙：比上个月有回升。')});
      await page.locator('#importBtn').click();await page.waitForFunction(() => currentImportJob === 'dual-import');
      await page.locator('#liveHomeBtn').click();await assertScreen(page, 'scrSetup');
      assert.equal(state.imports, 1, '返回首页不重复提交或取消后台导入任务');
      assert.match(await page.locator('#importStatus').textContent(), /后台处理/);

      if (width === 1440) {
        // 两个 await 位置分别延迟，确保续访旧响应不会将新打开的私密空间切回现场。
        for (const phase of ['recentMeta', 'recentState']) {
          await page.locator('#interviewEntry').click();await page.locator('#tabRecord').click();
          await page.locator('#recordingExtras summary').click();
          state.holdRecentMeta = phase === 'recentMeta';state.holdRecentState = phase === 'recentState';
          await page.evaluate(() => {
            window.testResumeDone = false;const original = window.resumeRecent;
            window.resumeRecent = async () => {await original();window.testResumeDone = true;};
          });
          await page.getByRole('button', {name: '继续最近访谈', exact: true}).click();await gates[phase].requested;
          await page.locator('#liveHomeBtn').click();await assertScreen(page, 'scrSetup');
          await page.locator('#memoirEntry').click();await assertScreen(page, 'scrMemoir');
          gates[phase].release();await page.waitForFunction(() => window.testResumeDone === true);
          await assertScreen(page, 'scrMemoir');
          assert.equal(await page.evaluate(() => sid), session.id, '过期续访响应不得修改当前会话归属');
          await page.locator('#memoirExit').click();await assertScreen(page, 'scrSetup');
        }
        state.holdRecentMeta = false;state.holdRecentState = false;state.importReady = true;
        await page.locator('#interviewEntry').click();await page.locator('#tabRecord').click();
        await page.locator('#recordingExtras summary').click();await page.locator('#captureImportTab').click();
        await page.locator('#importFile').setInputFiles({name: '延迟响应测试.txt', mimeType: 'text/plain', buffer: Buffer.from('仅测试最终纪要读取竞态。')});
        await page.evaluate(() => {
          window.testImportDone = false;const original = window.importMaterial;
          window.importMaterial = async () => {await original();window.testImportDone = true;};
        });
        await page.locator('#importBtn').click();await gates.report.requested;
        await page.locator('#liveHomeBtn').click();await assertScreen(page, 'scrSetup');
        await page.locator('#memoirEntry').click();await assertScreen(page, 'scrMemoir');
        gates.report.release();await page.waitForFunction(() => window.testImportDone === true);
        await assertScreen(page, 'scrMemoir');
        assert.equal(await page.evaluate(() => sid), session.id, '过期纪要响应不得覆盖当前会话ID');
        assert.equal(await page.locator('#md').textContent(), '', '过期纪要响应不得将原访谈内容渲染到私密空间');
        await page.locator('#memoirExit').click();await assertScreen(page, 'scrSetup');
      }

      const storageBefore = await page.evaluate(() => Object.fromEntries(Object.entries(localStorage)));
      await page.locator('#memoirEntry').click();await assertScreen(page, 'scrMemoir');
      await page.locator('#memoirLogin').waitFor({state: 'visible'});
      assert.match(await page.locator('#scrMemoir').innerText(), /时叙录/);
      assert.equal(await page.locator('#scrMemoir #liveForm, #scrMemoir #captureHub').count(), 0);
      assert(await page.locator('#authGate').evaluate(el => el.classList.contains('hidden')), '私密验证不应改变旧访谈门禁');
      await page.screenshot({path: path.join(screenshots, `${width}-shixulu.png`), fullPage: true, animations: 'disabled'});
      await page.locator('#memoirLoginName').fill('私密记录者');await page.locator('#memoirLoginPassword').fill('离线测试密码');
      await page.locator('#memoirLoginSubmit').click();await page.locator('#memoirLibrary').waitFor({state: 'visible'});
      await page.locator('#memoirLogout').click();await page.locator('#memoirLogin').waitFor({state: 'visible'});
      assert.equal(await page.locator('#memoirIdentity').textContent(), '');assert.equal(state.authenticated, false);
      await page.locator('#memoirExit').click();await assertScreen(page, 'scrSetup');
      assert.deepEqual(await page.evaluate(() => Object.fromEntries(Object.entries(localStorage))), storageBefore,
        '时叙录不得写入旧访谈草稿或浏览器持久存储');
      await page.locator('#interviewEntry').click();assert.equal(await page.locator('#subject').inputValue(), '双福市场水果档口经营变化');
      state.holdSession = true;
      const privateCountBeforeRace = privateRequests.length;
      await page.locator('#outlineRecordBtn').click();await sessionStarted;
      await page.locator('#interviewExit').click();await assertScreen(page, 'scrInterview');
      await page.evaluate(() => Memoir.open());await assertScreen(page, 'scrInterview');
      assert.equal(privateRequests.length, privateCountBeforeRace, '创建会话尚未返回时，不得先进入私密空间再被旧请求抢占');
      releaseSession();await assertScreen(page, 'scrLive');
      await page.waitForFunction(() => !creatingSession);
      assert.equal(state.creates, 2, '延迟提交始终只创建一次，不因导航重试重复创建');
      assert.equal(await page.evaluate(() => window.testMicrophoneRequests), 1, '实战提纲创建会话不应自动重启录音');
      await page.locator('#liveHomeBtn').click();await assertScreen(page, 'scrSetup');
      const ids = await page.locator('[id]').evaluateAll(nodes => nodes.map(node => node.id));
      assert.equal(new Set(ids).size, ids.length);assert.deepEqual(errors, []);assert.deepEqual(unexpected, []);
      await page.close();
    }
    console.log('PASS: 上下双入口、汇言笔内部访谈与录音、后台提纲/导入与刷新草稿、准备页返回、安全停录上传、取消返回、时叙录更名与独立身份/退出隔离、桌面和手机布局');
    console.log('截图目录：' + screenshots);
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode = 1;});
