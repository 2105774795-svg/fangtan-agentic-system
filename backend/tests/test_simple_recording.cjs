// 离线交互回归：仅使用被拦截的 API 和模拟麦克风，不启服务、不访问公网。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '../..');
const {html, serveScript} = require('./frontend_fixture.cjs');
const screenshots = path.join(root, 'data/qa/simple-recording');
const questions = ['这个市场最近一月客流变化了多少？', '进货成本和去年同期相比如何？',
  '当前档口租金和人工大约占多少？', '接下来准备怎样调整经营？'];
const background = '双福国际农贸城是本次指定的农产品交易市场。本次访谈将围绕水果批发、进货渠道和客流变化展开。';
const backgroundNote = '背景仅供调研准备，具体业态和经营现状请现场核实。';
const mockRecordingBrowser = () => {
  window.testMicrophoneRequests = 0;
  window.testRecorderStarts = 0;
  Object.defineProperty(navigator, 'mediaDevices', {value: {getUserMedia: async () => {
    window.testMicrophoneRequests += 1;
    const track = {readyState: 'live', stop() {this.readyState = 'ended';}, addEventListener() {}};
    return {active: true, getTracks: () => [track]};
  }}});
  window.MediaRecorder = class {
    static isTypeSupported() {return true;}
    constructor() {this.state = 'inactive';}
    start() {window.testRecorderStarts += 1;this.state = 'recording';}
    stop() {
      this.state = 'inactive';
      queueMicrotask(() => {
        this.ondataavailable?.({data: new Blob(['offline-audio'], {type: 'audio/webm'})});
        this.onstop?.();
      });
    }
  };
  window.EventSource = class {close() {}};
};

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.PLAYWRIGHT_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}});
    page.setDefaultTimeout(10000);
    const errors = [], dialogs = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('dialog', async dialog => {dialogs.push(dialog.message());await dialog.accept();});
    await page.addInitScript(mockRecordingBrowser);

    let settings = null, submitted = null, sessionCreates = 0, uploads = 0;
    let holdStartupState = false, releaseStartupState, notifyStartupState;
    const startupStateRequested = new Promise(resolve => {notifyStartupState = resolve;});
    const liveSession = {id: 'simple-session', status: 'live', display_subject: '水果档口调研',
      subject: '水果档口调研', outline: [], utterances: [], suggestions: []};
    const fixtureRoute = async route => {
      const request = route.request(), pathname = new URL(request.url()).pathname;
      let body;
      if (pathname === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (await serveScript(route, pathname)) return;
      if (pathname.startsWith('/assets/')) return route.fulfill({status: 204});
      if (pathname === '/api/auth/me') body = {user: {id: -1, name: '离线测试用户'}, auth_enabled: false};
      else if (pathname === '/api/outlines/jobs' && request.method() === 'POST') {
        settings = request.postDataJSON();body = {job_id: 'simple-outline', status: 'generating'};
      } else if (pathname === '/api/outlines/jobs/simple-outline') body = {job_id: 'simple-outline', status: 'ready',
        settings, questions, background, background_note: backgroundNote, background_sources: []};
      else if (pathname === '/api/sessions/' && request.method() === 'POST') {
        submitted = request.postDataJSON();sessionCreates += 1;
        await new Promise(resolve => setTimeout(resolve, 75));
        liveSession.subject = submitted.subject;liveSession.outline = submitted.outline;
        body = {session_id: liveSession.id};
      } else if (pathname === '/api/sessions/simple-session/state') {
        if (holdStartupState) {
          await new Promise(resolve => {releaseStartupState = resolve;notifyStartupState();});
        }
        body = {...liveSession, coverage: {
        mode: 'outline', dims: liveSession.outline.map((q, i) => ({id: 'outline:' + i, name: q, item: q,
          icon: '问', covered: false, subs: [q]})), ratio: 0, covered: [],
        missing: liveSession.outline.map((_, i) => 'outline:' + i)}};}
      else if (pathname === '/api/sessions/simple-session/coverage') body={mode:'outline',dims:liveSession.outline.map((q,i)=>({id:'outline:'+i,name:q,item:q,icon:'问',covered:false,subs:[q]})),ratio:0,covered:[],missing:liveSession.outline.map((_,i)=>'outline:'+i)};
      else if (pathname === '/api/sessions/simple-session/audio') {uploads += 1;body = {ok: true, seq: uploads};}
      else if (pathname === '/api/sessions/simple-session') body = liveSession;
      else if (pathname === '/api/sessions/') body = {items: [], total: 0};
      else if (pathname === '/api/projects') body = {items: [], projects: []};
      else if (pathname === '/api/management/overview') body = {todos: [], stats: {
        interviews: 0, recordings: 0, reports: 0, duration_minutes: 0}};
      else throw new Error('未预期的离线请求：' + request.method() + ' ' + pathname);
      return route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    };
    await page.route('**/*', fixtureRoute);

    await page.goto('https://interview.test/');
    await page.waitForFunction(() => document.getElementById('userName').value === '离线测试用户' && document.getElementById('authGate').classList.contains('hidden'));
    await page.locator('#interviewEntry').click();
    assert.equal(await page.locator('#regionDistrict, #regionTown, #regionAddress').count(), 0, '不再显示区县、街道、村镇和具体地址输入框');
    await page.locator('#tabRecord').click();
    await page.locator('#recordEntryBtn').waitFor({state: 'visible'});
    assert.equal(await page.locator('#captureRecordPanel button').count(), 1, '主录音区域只保留一个录音按键');
    assert.equal(await page.locator('#recordingExtras').evaluate(el => el.open), false, '次要功能默认折叠');
    assert.equal(await page.locator('#captureRecordTab').count(), 0, '去掉重复的实时录音页签');
    assert.equal(await page.locator('#captureHub input:visible, #captureHub textarea:visible, #captureHub select:visible').count(), 0,
      '录音首页不直接显示地区和提纲表单');
    assert(!/继续最近一场未结束/.test(await page.locator('#captureHub').textContent()), '去掉重复的续访入口');
    fs.mkdirSync(screenshots, {recursive: true});
    await page.screenshot({path: path.join(screenshots, 'recording-home-mobile.png'), fullPage: true, animations: 'disabled'});
    console.log('CHECK: 录音首页与默认折叠');

    await page.locator('#recordEntryBtn').click();
    await page.locator('#scrRecordPrepare.on').waitFor();
    assert.equal(await page.locator('#prepareFormHost #liveForm').count(), 1, '点击录音后才显示准备表单');
    assert.equal(sessionCreates, 0, '打开准备页不能提前创建访谈');
    await page.locator('#subject').fill('水果档口调研');
    await page.locator('#industry').selectOption('食品饮料');
    await page.locator('#regionCity').fill('重庆市');
    await page.locator('#regionMarket').fill('双福国际农贸城');
    await page.locator('#focus').fill('水果批发、客流与进货成本');
    await page.locator('#outlineBtn').click();
    await page.locator('#outlineChoices input').last().waitFor();
    assert.deepEqual(settings.location, {city: '重庆市', market: '双福国际农贸城'});
    assert.equal(await page.locator('#placeBackground').innerText(), background);
    assert.equal(await page.locator('#placeBackgroundNote').innerText(), backgroundNote);
    assert(await page.locator('#placeBackgroundCard').isVisible());
    assert(await page.locator('#placeBackgroundCard').evaluate(card => Boolean(card.compareDocumentPosition(document.getElementById('outline')) & Node.DOCUMENT_POSITION_FOLLOWING)),
      '调研地点背景应展示在采访提纲之前');
    for (let i = 2; i < questions.length; i++) await page.locator('#outlineChoices input').nth(i).uncheck();
    await page.screenshot({path: path.join(screenshots, 'recording-preparation-mobile.png'), fullPage: true, animations: 'disabled'});
    await page.locator('#prepareBackBtn').click();
    await page.locator('#recordEntryBtn').waitFor({state: 'visible'});
    assert.equal(sessionCreates, 0, '退出准备页不能产生空访谈');
    assert.equal(await page.locator('#recordingExtras').evaluate(el => el.open), false);
    console.log('CHECK: 准备页生成地点背景与返回保持草稿');

    await page.reload();
    await page.waitForFunction(() => document.getElementById('userName').value === '离线测试用户' && document.getElementById('authGate').classList.contains('hidden'));
    await page.locator('#interviewEntry').click();
    await page.locator('#tabRecord').click();await page.locator('#recordEntryBtn').click();
    assert.equal(await page.locator('#subject').inputValue(), '水果档口调研', '刷新后保留准备草稿');
    assert.equal(await page.locator('#regionMarket').inputValue(), '双福国际农贸城');
    assert.equal(await page.locator('#outlineChoices input:checked').count(), 2, '刷新后保留问题筛选');
    assert.equal(await page.locator('#placeBackground').innerText(), background, '刷新后保留调研地点背景');
    assert.equal(await page.locator('#liveForm').count(), 1, '实战和录音准备共享表单，不复制同名 DOM');
    console.log('CHECK: 刷新恢复背景、提纲与筛选');
    holdStartupState = true;
    await page.locator('#outlineRecordBtn').evaluate(button => {button.click();button.click();});
    await startupStateRequested;
    await page.locator('#liveWorkspace').waitFor({state:'visible'});
    assert(await page.locator('#orb').isDisabled(),'启动及恢复状态期间不可再次切换录音');
    assert(await page.locator('#liveFieldTab').isDisabled(),'授权麦克风之前不能离开并在隐藏页启动录音');
    holdStartupState = false;releaseStartupState();
    await page.waitForFunction(() => document.getElementById('orb').classList.contains('rec'));
    assert.equal(sessionCreates, 1, '快速重复点击只创建一场访谈');
    assert.equal(await page.evaluate(() => window.testMicrophoneRequests), 1, '只请求一次麦克风');
    assert.equal(await page.evaluate(() => window.testRecorderStarts), 1, '只启动一次录音');
    assert.deepEqual(submitted.outline, questions.slice(0, 2), '录音进度只接收已勾选问题');
    assert.deepEqual(submitted.location, settings.location);
    assert.equal(await page.locator('#covTitle').innerText(), '筛选提纲摸排进度');
    await page.locator('#orb').click();
    await page.waitForFunction(() => document.getElementById('stTxt').textContent === '已停止');
    await page.waitForFunction(() => !uploading && pending.length === 0);
    assert.equal(uploads, 1, '停止时的尾段照常上传');
    console.log('CHECK: 单次建档、启动和停止录音');

    await page.evaluate(() => showCaptureHub('record'));
    await page.locator('#recordingExtras summary').click();
    assert.equal(await page.locator('#recordingExtras').evaluate(el => el.open), true);
    await page.locator('#recordingExtras').getByRole('button', {name: '历史会话', exact: true}).click();
    await page.locator('#historyList .empty').waitFor();
    await page.locator('#scrHistory .backlink').first().click();
    await page.locator('#recordingExtras summary').click();
    await page.locator('#recordingExtras').getByRole('button', {name: '全部待办', exact: true}).click();
    await page.locator('#allTodos .empty').waitFor();
    await page.evaluate(() => showCaptureHub('record'));
    await page.evaluate(() => selectCaptureMode('import'));
    assert.equal(await page.locator('#recordingExtras').evaluate(el => el.open), true, '程序进入导入时自动展开');
    await page.locator('#importFile').setInputFiles({name: '访谈文字稿.txt', mimeType: 'text/plain', buffer: Buffer.from('甲：最近客流怎样？\n乙：本月有所回升。')});
    assert.match(await page.locator('#importPicked').innerText(), /访谈文字稿/);
    assert(await page.locator('#importBtn').isEnabled());
    await page.locator('#captureProjectsTab').click();
    await page.locator('#recordingProjectsList .desc').waitFor();
    assert(await page.locator('#captureProjectsPanel').isVisible(), '折叠后的项目入口仍可使用');
    await page.locator('#recordingExtras').getByRole('button', {name: '继续最近访谈', exact: true}).click();
    await page.locator('#liveWorkspace').waitFor({state: 'visible'});
    assert.equal(sessionCreates, 1, '继续最近访谈不重复新建会话');
    assert.equal(await page.evaluate(() => window.testMicrophoneRequests), 1, '续访只恢复现场，不自动索取麦克风');
    await page.locator('#liveFieldTab').click();
    assert.equal(await page.locator('#setupFormHost #liveForm').count(), 1, '返回实战时共享表单移回原位');
    assert.equal(await page.locator('#subject').inputValue(), '水果档口调研');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '手机界面不得横向溢出');
    const duplicateIds = await page.evaluate(() => {
      const seen = new Set();return [...document.querySelectorAll('[id]')].filter(node => seen.has(node.id) || !seen.add(node.id)).map(node => node.id);
    });
    assert.deepEqual(duplicateIds, []);
    const blankPage = await browser.newPage({viewport: {width: 390, height: 844}});
    blankPage.setDefaultTimeout(10000);
    blankPage.on('pageerror', error => errors.push(error.message));
    blankPage.on('dialog', async dialog => {dialogs.push(dialog.message());await dialog.accept();});
    await blankPage.addInitScript(mockRecordingBrowser);await blankPage.route('**/*', fixtureRoute);
    await blankPage.goto('https://interview.test/');
    await blankPage.waitForFunction(() => document.getElementById('userName').value === '离线测试用户');
    await blankPage.locator('#interviewEntry').click();
    await blankPage.locator('#tabRecord').click();await blankPage.locator('#recordEntryBtn').click();
    await blankPage.locator('#subject').fill('无需预设提纲的录音');
    await blankPage.locator('#outline').fill('');
    assert(await blankPage.locator('#outlineRecordBtn').isEnabled(), '不生成、不选择提纲也能直接录音');
    await blankPage.locator('#outlineRecordBtn').click();
    await blankPage.waitForFunction(() => document.getElementById('orb').classList.contains('rec'));
    assert.equal(sessionCreates, 2);assert.deepEqual(submitted.outline, []);
    assert.equal(await blankPage.evaluate(() => window.testMicrophoneRequests), 1);
    await blankPage.locator('#orb').click();
    await blankPage.waitForFunction(() => !uploading && pending.length === 0);
    assert.equal(uploads, 2, '无提纲录音也照常保存尾段');
    assert.deepEqual(errors, []);assert.deepEqual(dialogs, []);
    console.log('PASS: 极简录音首页、折叠管理、准备页与草稿、地点背景、单次建档录音、无提纲录音和旧功能入口');
    console.log('截图目录：' + screenshots);
  } catch (error) {console.error(error);throw error;}
  finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode = 1;});
