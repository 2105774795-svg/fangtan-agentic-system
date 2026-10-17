// 完全离线：构建资源、传记 API 和合成人物由浏览器路由提供，不启动服务。
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const buildRoot = path.resolve(root, process.env.PLATFORM_TEST_DIST || 'backend/static/platform');
const output = path.join(root, 'data/qa/biography'); fs.mkdirSync(output, { recursive: true });
const evidence = [{ turn_id: 'human-one', quote: '1980年我第一次进厂工作。' }];
const source = { id: 'human-one', role: 'human', text: evidence[0].quote, output: '', client_id: 'human-client', control: '', audio_ids: [], job: null };
const firstChapter = { id: 'chapter-one', title: '走进工厂', paragraphs: [{ text: '1980年，我第一次进厂工作。后来想起那些日子，记得最清楚的，还是刚进厂时的事。', evidence }], locked: false };
const fixture = {
  sessions: [{ id: 'session-one', kind: 'memoir', status: 'done', created_at: 1792200000, turns: [source], audio: [], archive_job: null, archive: { revision: 1, status: 'draft', events: [], paragraphs: [{ text: '我第一次进厂工作。', evidence }] } }], legacy_artifacts: [],
  memories: [{ id: 'memory-one', category: 'event', title: '第一次进厂', description: '本人讲到第一次进厂的经历。', date_text: '1980年', status: 'candidate', revision: 1, evidence, related_ids: [], note: '' }],
  book_settings: { revision: 0, title: '在岁月里走过', voice_note: '', style_approved: false, feedback: [], style: { name: '《从文自传》式朴素回忆体', principles: ['保留传主自己的措辞与性情', '通过具体人物和地方生活展开，情感节制'], boundaries: '不补造天气、对白或心理' } },
  book_gaps: [{ id: 'gap-one', title: '走进工厂', question: '您还记得初到车间时先看见的是什么吗？', status: 'open', focused: false }],
  book: { revision: 1, format: 'biography-v1', title: '在岁月里走过', stage: 'sample', status: 'draft', stale: false, outline: [{ id: 'chapter-one', title: '走进工厂' }, { id: 'chapter-two', title: '学会一门手艺' }], chapters: [structuredClone(firstChapter)], coverage: { source_turns: 1, cited_turns: 1, uncited_turn_ids: [], notice: '引用用于回查，不代表独立事实核验。' } }, book_job: null,
};
const project = { id: 'memoir-fixture', title: '合成人物的传记', prompt: '口述史', scenario: 'memoir', mode: 'ai', stage: 0, answers: {}, context: '', artifacts: [], messages: [], revision: 1, updated_at: 1792200000, layout: { version: 0, positions: {} } };
const changes = [];
const history = [];
const live = { id: 'session-next', kind: 'memoir', status: 'live', created_at: 1792200001, turns: [{ ...source, id: 'welcome', role: 'assistant', text: '我们接着聊聊初到车间的往事。' }], audio: [], archive: null, archive_job: null };

(async () => {
  const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_BROWSER_CHANNEL || 'msedge', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' });
    const unexpected = [];
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== 'http://memoir.test') { unexpected.push(url.origin); return route.abort(); }
      const p = url.pathname;
      const json = value => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
      if (p.endsWith('/scenarios')) return json({ memoir: { name: '口述史', steps: [], fields: [] } });
      if (p === '/api/platform/projects') return json([project]);
      if (p === '/api/interview-prep/projects') return json([]);
      if (p === '/api/platform/projects/memoir-fixture') return json(project);
      if (p.endsWith('/conversation-archive')) return json(fixture);
      if (p.endsWith('/conversation-book/versions')) return json({ versions: history });
      if (p.endsWith('/conversation-book/settings')) {
        const body = route.request().postDataJSON(); changes.push(body);
        assert.equal(body.revision, fixture.book_settings.revision);
        const prefs = fixture.book_settings;
        if (body.action === 'approve_style') prefs.style_approved = true;
        if (body.action === 'settings') { prefs.title = body.title; prefs.voice_note = body.voice_note; }
        if (body.action === 'feedback') { prefs.feedback.push(body); fixture.book.stale = true; }
        if (body.action === 'skip_gap') fixture.book_gaps[0].status = 'skipped';
        if (body.action === 'focus_gap') { fixture.book_gaps[0].status = 'open'; fixture.book_gaps[0].focused = true; }
        prefs.revision++;
        return json(prefs);
      }
      if (p.endsWith('/conversation-book/review')) {
        const body = route.request().postDataJSON(); changes.push(body);
        assert.equal(body.revision, fixture.book.revision);
        history.unshift({ revision: fixture.book.revision, created_at: 1792200000, document: structuredClone(fixture.book) });
        const chapter = fixture.book.chapters.find(c => c.id === body.chapter_id) || fixture.book.chapters[body.chapter_index || 0];
        if (body.action === 'edit') { chapter.paragraphs[body.paragraph_index].text = body.text; chapter.paragraphs[body.paragraph_index].edited = true; }
        if (body.action === 'lock_chapter') chapter.locked = true;
        if (body.action === 'unlock_chapter') chapter.locked = false;
        if (body.action === 'adopt_chapter') { Object.assign(chapter, chapter.pending_revision); delete chapter.pending_revision; delete chapter.needs_review; delete fixture.book.pending_draft; }
        if (body.action === 'review') fixture.book.status = 'reviewed';
        fixture.book.revision++;
        return json({ book: fixture.book, job: fixture.book_job });
      }
      if (p.endsWith('/conversation-book') && route.request().method() === 'POST') {
        const body = route.request().postDataJSON(); changes.push(body);
        const second = { id: 'chapter-two', title: '学会一门手艺', paragraphs: [{ text: '那些日子里的工作，我后来还常常说起。', evidence }], locked: false };
        if (!fixture.book.chapters.some(c => c.id === second.id)) fixture.book.chapters.push(second);
        if (fixture.book.chapters[0].paragraphs[0].edited) {
          fixture.book.chapters[0].pending_revision = { ...structuredClone(firstChapter), paragraphs: [{ text: '这一版根据补充讲述修订，仍保留原话依据。', evidence }] };
          fixture.book.chapters[0].needs_review = true;
        }
        fixture.book.revision++; fixture.book.stale = false; fixture.book.stage = 'growing';
        fixture.book_job = { id: 'book-job', status: 'ready', stage: 'ready', error: '' };
        return json({ task_id: 'book-job', status: 'queued' });
      }
      if (p.endsWith('/conversation-memories/memory-one/review')) {
        const body = route.request().postDataJSON(); changes.push(body);
        assert.equal(body.revision, fixture.memories[0].revision);
        fixture.memories[0].revision++; fixture.memories[0].status = 'confirmed'; return json(fixture.memories[0]);
      }
      if (p.endsWith('/conversations') || p.endsWith('/conversations/session-next')) return json(live);
      if (p.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: 'event: snapshot\ndata: ' + JSON.stringify(live) + '\n\n' });
      if (p.startsWith('/api/')) { unexpected.push(p); return route.fulfill({ status: 404, body: '{}' }); }
      const target = path.resolve(buildRoot, p === '/' ? 'index.html' : p.slice(1));
      if (!target.startsWith(buildRoot + path.sep) || !fs.existsSync(target)) return route.fulfill({ status: 404, body: '' });
      const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.ttf': 'font/ttf' };
      return route.fulfill({ contentType: types[path.extname(target)] || 'application/octet-stream', body: fs.readFileSync(target) });
    });
    const page = await context.newPage(); const errors = [];
    page.on('pageerror', e => errors.push(e.message)); page.on('dialog', d => d.accept());
    await page.goto('http://memoir.test/?project=memoir-fixture&view=life-archive');
    await page.getByRole('heading', { name: '在岁月里走过', exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '核对这段原话', exact: true }).count(), 0, '正文默认不插入核对工具');
    assert.equal(await page.getByRole('region', { name: '讲述档案' }).count(), 0);
    await page.getByRole('button', { name: '认可这个写法，继续写', exact: true }).click();
    await page.getByRole('heading', { name: '学会一门手艺', exact: true }).waitFor();
    await page.getByRole('button', { name: '核对与编辑', exact: true }).click();
    await page.getByRole('button', { name: '核对这段原话', exact: true }).first().click();
    await page.getByRole('region', { name: '原话依据' }).getByText(evidence[0].quote, { exact: true }).waitFor();
    await page.getByRole('button', { name: '收起原话', exact: true }).click();
    await page.getByRole('button', { name: '修改这一段', exact: true }).first().click();
    await page.getByLabel('修改传记正文').fill('这段文字经过本人修改。');
    await page.getByRole('button', { name: '保存正文修改', exact: true }).click();
    await page.getByText('这段文字经过本人修改。', { exact: true }).waitFor();
    await page.getByRole('button', { name: '这里写得不像我', exact: true }).first().click();
    await page.getByLabel('告诉我们具体想改哪里').fill('少用抒情，保留我说话的直爽。');
    await page.getByRole('button', { name: '保存这条意见', exact: true }).click();
    await page.getByRole('button', { name: '根据新讲述续写', exact: true }).click();
    await page.getByText('有待核对的修订', { exact: true }).waitFor();
    assert.equal(fixture.book.chapters[0].paragraphs[0].text, '这段文字经过本人修改。');
    await page.getByText('对照本章修订稿', { exact: true }).click();
    await page.getByRole('region', { name: '本章修订稿' }).getByText('这一版根据补充讲述修订，仍保留原话依据。', { exact: true }).waitFor();
    await page.getByRole('button', { name: '采用本章修订稿', exact: true }).click();
    await page.getByText('有待核对的修订', { exact: true }).waitFor({ state: 'detached' });
    await page.getByRole('button', { name: '这一章先定下来', exact: true }).first().click();
    await page.getByText('本章已定稿保护', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '修改这一段', exact: true }).first().isDisabled(), true);
    await page.getByRole('button', { name: '返回连贯阅读', exact: true }).click();
    for (const [label, filename, audit] of [['导出传记正文', '传记正文.md', false], ['导出审校版', '传记审校.md', true]]) {
      const promise = page.waitForEvent('download'); await page.getByRole('button', { name: label, exact: true }).click();
      await (await promise).saveAs(path.join(output, filename));
      const content = fs.readFileSync(path.join(output, filename), 'utf8');
      assert.equal(content.includes('> 原话：'), audit);
    }
    await page.getByRole('button', { name: '查看书稿版本', exact: true }).click();
    await page.getByRole('list', { name: '书稿版本记录' }).waitFor();
    await page.getByRole('button', { name: '资料与核对', exact: true }).click();
    await page.getByRole('button', { name: '确认这条线索', exact: true }).click();
    await page.getByText('本人已确认 · 1980年', { exact: true }).waitFor();
    await page.getByRole('button', { name: '待补充的往事', exact: true }).click();
    await page.getByRole('button', { name: '这段先不聊', exact: true }).click();
    await page.getByText('暂时跳过的往事', { exact: true }).click();
    await page.getByRole('button', { name: '现在愿意聊', exact: true }).click();
    await page.getByText('我们接着聊聊初到车间的往事。', { exact: true }).waitFor();
    await page.getByRole('button', { name: '我的传记', exact: true }).click();
    await page.getByRole('button', { name: '我的传记', exact: true }).click();
    await page.getByRole('heading', { name: '在岁月里走过', exact: true }).waitFor();
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: path.join(output, 'desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(output, 'mobile.png'), fullPage: true });
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    assert.ok(changes.some(c => c.action === 'approve_style') && changes.some(c => c.action === 'lock_chapter') && changes.some(c => c.action === 'feedback') && changes.some(c => c.action === 'focus_gap'));
    console.log('PASS: 样章认可、续写、正文阅读、原话核对、人工编辑、文风反馈、局部修订对照、定稿保护、两种整书导出、版本、资料确认、缺口选择/跳过、桌面和手机。未启动服务。');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
