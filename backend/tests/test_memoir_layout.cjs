// 时叙录独立版面的离线视觉回归：不启动服务，拦截全部网络请求。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const {html, serveScript} = require('./frontend_fixture.cjs');

const screenshots = path.resolve(__dirname, '../../data/qa/memoir-layout');
const profileId = '11111111111111111111111111111111';
const sessionId = '22222222222222222222222222222222';
const liveId = '33333333333333333333333333333333';
const humanId = '44444444444444444444444444444444';
const longText = '1985 年，我第一次离开家乡，在纺织厂开始了维修机器的工作。那时厂里的老师傅带着我，我至今记得第一天上班的情景。';
const profile = {id: profileId, name: '样例 · 妈妈的人生故事 · 从故乡到城市的岁月',
  description: '离线合成示例，非真实人物资料。记录家乡、第一次工作、家人与一路走来的往事。模糊的年份和不同说法，慢慢核对，不急着给出结论。', updated_at: 1792020000};
const sessions = [{id: sessionId, title: '第一场讲述 · 离开故乡的那一年', status: 'done', created_at: 1792020000}];
const evidence = [{turn_id: humanId, quote: '我第一次离开家乡，在纺织厂开始了维修机器的工作。'}];
const memories = [{id: '55555555555555555555555555555555', title: '在纺织厂开始第一份工作', date_text: '1985 年前后（本人回忆，具体月份待确认）',
  description: longText + longText, status: 'candidate', evidence}];
const chapters = [{id: '66666666666666666666666666666666', title: '第一章 · 那些离开故乡又始终记得的日子',
  status: 'draft', paragraphs: [{text: longText + '\n\n' + longText, evidence}]},
  {id: '77777777777777777777777777777777', title: '已确认的整理版本', status: 'confirmed',
    paragraphs: [{text: longText, evidence}]}];
const turns = [{id: 'opening-question', role: 'assistant', text: '那一年离开家乡时，您最记得的一件小事是什么？'},
  {id: humanId, role: 'human', text: longText + '\n' + '超长文件标识：' + 'original_story_without_spaces_'.repeat(12),
    audio_url: `api/memoir/profiles/${profileId}/audio/${humanId}`}];

function mockDevices() {
  Object.defineProperty(window, 'speechSynthesis', {value: {getVoices: () => [], cancel() {}, addEventListener() {}}});
  window.EventSource = class {close() {}};
}

async function assertLayout(page, label, width) {
  const result = await page.evaluate(() => {
    const visible = element => !!element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden';
    const active = document.querySelector('.screen.on');
    const outOfBounds = [...active.querySelectorAll('button,input,textarea,summary,a,[role="tabpanel"],.memoir-card,.memoir-panel,.memoir-question')]
      .filter(visible).filter(element => {
        const box = element.getBoundingClientRect();return box.left < -1 || box.right > innerWidth + 1;
      }).map(element => element.id || element.className || element.tagName);
    const undersized = [...active.querySelectorAll('button,.memoir-actions a,summary')].filter(visible)
      .filter(element => element.getBoundingClientRect().height < 43.5)
      .map(element => ({id: element.id || element.textContent.trim(), height: element.getBoundingClientRect().height}));
    const duplicateIds = [...document.querySelectorAll('[id]')].map(element => element.id)
      .filter((id, index, ids) => ids.indexOf(id) !== index);
    return {documentWidth: document.documentElement.scrollWidth, viewport: innerWidth,
      outOfBounds, undersized, duplicateIds, activeWidth: active.getBoundingClientRect().width};
  });
  assert(result.documentWidth <= result.viewport + 1, `${label}: 文档横向溢出 ${JSON.stringify(result)}`);
  assert.deepEqual(result.outOfBounds, [], `${label}: 控件或内容超出屏幕`);
  assert.deepEqual(result.undersized, [], `${label}: 主要交互触控高度应不少于 44px`);
  assert.deepEqual(result.duplicateIds, [], `${label}: 不得出现重复 ID`);
  if (width >= 1000) assert(result.activeWidth > 840, `${label}: 独立工作区应明显宽于原 840px 版面`);
  await page.screenshot({path: path.join(screenshots, `${width}-${label}.png`), fullPage: true, animations: 'disabled'});
}

(async () => {
  for (const id of ['scrSetup', 'scrLive', 'captureHub', 'liveWorkspace', 'outline', 'covwrap', 'scrProject',
    'subject', 'industry', 'regionCity', 'regionMarket']) {
    assert(html.includes(`id="${id}"`), `原有访谈功能控件必须保留：${id}`);
  }
  assert.match(html, /memoir-entry-section/, '首页需要单独的时叙录版块');
  assert.match(html, /memoir-workspace/, '人物档案需要独立工作区');
  fs.mkdirSync(screenshots, {recursive: true});
  const browser = await chromium.launch({headless: true, channel: process.env.PLAYWRIGHT_BROWSER_CHANNEL || undefined});
  try {
    for (const width of [1440, 1280, 390, 320]) {
      const page = await browser.newPage({viewport: {width, height: width >= 1000 ? 1000 : 844}});
      page.setDefaultTimeout(10000);await page.addInitScript(mockDevices);
      const errors = [], unexpected = [];let authenticated = false;
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', async route => {
        const request = route.request(), pathname = new URL(request.url()).pathname;
        if (pathname === '/') return route.fulfill({contentType: 'text/html', body: html});
        if (await serveScript(route, pathname)) return;
        if (pathname.startsWith('/assets/')) return route.fulfill({status: 204});
        const send = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
        if (pathname === '/api/auth/me') return send({user: {id: -1, name: '原访谈使用人'}, auth_enabled: false});
        if (pathname === '/api/memoir/auth/me') return authenticated ? send({user: {id: 8, name: '记录者甲'}}) : send({detail: '请单独验证'}, 401);
        if (pathname === '/api/memoir/auth/login') {authenticated = true;return send({user: {id: 8, name: '记录者甲'}});}
        if (pathname === '/api/memoir/profiles') return send({profiles: [profile, {...profile, id: '88888888888888888888888888888888', name: '爸爸的故乡与工作往事'}]});
        if (pathname === `/api/memoir/profiles/${profileId}`) return send({profile, sessions, memories, chapters, tasks: []});
        if (pathname === `/api/memoir/profiles/${profileId}/sessions` && request.method() === 'POST') {
          return send({session: {id: liveId, title: '新的讲述 · 回到记忆开始的地方', status: 'live'},
            question: {text: '回到第一份工作，您愿意讲讲那时最难忘的一天吗？', turn: {id: 'new-question', role: 'assistant', text: '您愿意从哪里开始讲？'}}}, 201);
        }
        if (pathname === `/api/memoir/profiles/${profileId}/sessions/${sessionId}`) return send({session: sessions[0], turns});
        if (pathname.startsWith(`/api/memoir/profiles/${profileId}/audio/`)) return route.fulfill({status: 204});
        unexpected.push(request.method() + ' ' + pathname);return route.fulfill({status: 500, contentType: 'application/json', body: '{"detail":"不允许未模拟请求"}'});
      });
      await page.goto('https://interview.test/');
      await page.waitForFunction(() => document.getElementById('authGate').classList.contains('hidden'));
      const entry = page.locator('.memoir-entry-section');await entry.waitFor({state: 'visible'});
      assert(await entry.locator('#memoirEntry').isVisible(), '大版块中的入口应能直接点击');
      const entryBox = await entry.boundingBox(), setupBox = await page.locator('#scrSetup').boundingBox();
      assert(entryBox.width >= (setupBox.width - 44) * 0.8, `${width}: 时叙录不应退化为角落文字链接`);
      assert(entryBox.height >= 120, `${width}: 首页独立版块高度应足以承载标题和入口`);
      await page.screenshot({path: path.join(screenshots, `${width}-entry.png`), fullPage: true, animations: 'disabled'});
      if (width === 1440) await entry.screenshot({path: path.join(screenshots, 'entry-card-preview.png'), animations: 'disabled'});

      await page.locator('#memoirEntry').click();await page.locator('#memoirLogin').waitFor({state: 'visible'});
      await assertLayout(page, 'login', width);
      await page.locator('#memoirLoginName').fill('记录者甲');await page.locator('#memoirLoginPassword').fill('测试密码');
      await page.locator('#memoirLoginSubmit').click();await page.locator('#memoirLibrary').waitFor({state: 'visible'});
      await assertLayout(page, 'library', width);
      await page.locator('#memoirCreateDetails summary').click();await assertLayout(page, 'create-profile', width);
      await page.locator('#memoirProfiles').getByRole('button', {name: '打开人物档案'}).first().click();
      await page.locator('#memoirProfile').waitFor({state: 'visible'});
      await assertLayout(page, 'profile', width);
      if (width >= 1000) {
        const nav = await page.locator('.memoir-tabs').boundingBox(), panel = await page.locator('#memoirStoryPanel').boundingBox();
        assert(nav.x + nav.width <= panel.x + 1, `${width}: 桌面档案导航应在内容左侧，避免挤成窄条`);
        assert(panel.width >= 600, `${width}: 桌面讲述内容应有足够阅读宽度`);
      }
      await page.locator('#memoirSessions').getByRole('button', {name: '查看原话与回听'}).click();
      await page.locator('#memoirConversation').waitFor({state: 'visible'});await assertLayout(page, 'story-readonly', width);
      await page.locator('#memoirTimelineTab').click();await page.locator('#memoirTimelinePanel').waitFor({state: 'visible'});
      await assertLayout(page, 'timeline', width);
      await page.locator('#memoirBookTab').click();await page.locator('#memoirBookPanel').waitFor({state: 'visible'});
      await assertLayout(page, 'book', width);
      if (width === 1440) {
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.screenshot({path: path.join(screenshots, 'book-workspace-preview.png'), fullPage: true, animations: 'disabled'});
      }
      await page.locator('#memoirProfilesBack').click();await page.locator('#memoirProfiles').getByRole('button', {name: '打开人物档案'}).first().click();
      await page.locator('#memoirStartSession').click();await page.locator('#memoirRecordControls').waitFor({state: 'visible'});
      await assertLayout(page, 'story-live', width);
      const typeSizes = await page.locator('#memoirText').evaluate(element => ({text: parseFloat(getComputedStyle(element).fontSize),
        note: parseFloat(getComputedStyle(document.querySelector('#scrMemoir .memoir-note')).fontSize)}));
      assert(typeSizes.text >= 18, '讲述正文应放大至至少 18px');assert(typeSizes.note >= 16, '说明文字应放大至至少 16px');
      assert.deepEqual(errors, [], `${width}: 页面脚本异常`);assert.deepEqual(unexpected, [], `${width}: 所有请求均须离线拦截`);
      await page.close();
    }
    console.log('PASS: 独立醒目入口、桌面宽版与侧边导航、登录/资料库/建档/档案/讲述/时间线/草稿四屏宽度、手机无横向溢出、44px触控、字号与原访谈功能保留');
    console.log('截图目录：' + screenshots);
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode = 1;});
