// 路由提供合成项目和构建产物；不启动 HTTP/Worker，不调用真实 AI 或麦克风。
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const build = path.resolve(root, process.env.PLATFORM_TEST_DIST || 'data/qa/workspace-integration/frontend-build');
const output = path.join(root, 'data/qa/workspace-integration/browser'); fs.mkdirSync(output, { recursive: true });
const origin = 'http://127.0.0.1:18883';
const steps = ['目标确认', '背景与材料', '问题与策略', '模拟练习', '复盘改进', '成果归档'];
const cfg = { goal: '准备合成产品经理面试，讲清个人贡献', template: 'job', language: '中文', practice_mode: 'coaching', question_count: 2, max_followups: 1, followup_mode: 'adaptive', include_candidate_questions: false, duration_minutes: 20, company_name: '', role_name: '', company_background: '', job_requirements: '', background_source: '', interview_stage: '' };
const plan = { summary: '围绕真实经历说明个人行动。', preparation_tips: ['说明做过的事'], questions: [{ question: '请介绍你负责的项目？', target: '个人贡献' }, { question: '你如何验证结果？', target: '结果依据' }] };

// 模拟浏览器后退已改变 URL，再触发同文档 popstate；不创建真实服务器连接。
const backToHome = page => page.evaluate(() => { history.pushState({}, '', '/'); dispatchEvent(new PopStateEvent('popstate')); });

async function fixture(browser, legacy = false, research = false) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' });
  await context.addInitScript(() => {
    window.__events = []; window.__openEvents = 0; window.__mic = 0;
    window.EventSource = class extends EventTarget {
      constructor(url) { super(); this.url = url; window.__events.push(this); window.__openEvents++; }
      close() { window.__events = window.__events.filter(e => e !== this); }
    };
    window.__event = (kind, value, cursor = '') => window.__events.forEach(e => e.dispatchEvent(new MessageEvent(kind, { data: JSON.stringify(value), lastEventId: String(cursor) })));
    const chunks = new Map();
    window.RecordingStore = { put: async c => chunks.set(c.id, c), remove: async id => chunks.delete(id), list: async sid => [...chunks.values()].filter(c => c.sid === sid) };
    Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia: async () => {
      window.__mic++; return { getTracks: () => [{ readyState: 'live', stop() {}, addEventListener() {} }] };
    } } });
    window.MediaRecorder = class {
      static isTypeSupported() { return true; }
      state = 'inactive'; mimeType = 'audio/webm';
      start() { this.state = 'recording'; }
      stop() { this.state = 'inactive'; queueMicrotask(() => { this.ondataavailable?.({ data: new Blob(['synthetic-audio'], { type: this.mimeType }) }); this.onstop?.(); }); }
    };
  });
  const preparation = { id: 'ip_fixture', workspace_project_id: 'ip_fixture', title: '合成面试项目', revision: 1, config: structuredClone(cfg), plan: null, plan_stale: false, materials: [], question_bank: [], practices: [], job: null };
  const project = { id: 'ip_fixture', title: preparation.title, prompt: cfg.goal, scenario: 'interview', stage: 0, answers: { goal: [cfg.goal] }, context: '', artifacts: [], messages: [], revision: 2, updated_at: 1792200000, mode: 'ai', layout: { version: 0, positions: {} }, tasks: [], job: null, event_cursor: 0, scene_state: { preparation_id: 'ip_fixture', domain_revision: 1, config: preparation.config, practices: [], insights: [], plan_stale: false }, workflow: { current_step: 'goal', steps: steps.map(title => ({ key: title, title })) } };
  if (research) { project.scenario = 'market'; project.title = '合成市场调研'; project.prompt = '了解合成市场经营情况'; project.mode = 'demo'; project.messages.push({ id: 'greeting', role: 'assistant', text: '请确认调研关注方向。', at: 1 }); }
  const stats = { actions: [], requests: [], unexpected: [], uploads: 0, materialUploads: [], failUpload: null, holdUpload: null, typed: [], cursors: 0, history: [], task: 0, dialogs: [], rejectDialogs: false, failAction: null };
  const errors = []; const page = await context.newPage(); page.on('pageerror', e => errors.push(e.message)); page.on('dialog', dialog => {
    stats.dialogs.push({ type: dialog.type(), message: dialog.message() });
    return dialog.type() === 'confirm' && stats.rejectDialogs ? dialog.dismiss() : dialog.accept();
  });
  function sync() {
    project.revision++; project.scene_state.domain_revision = preparation.revision; project.scene_state.config = preparation.config;
    project.scene_state.practices = preparation.practices.map(p => ({ id: p.id, status: p.status, created_at: p.created_at, has_review: !!p.review }));
  }
  const snapshot = async () => { project.event_cursor = Math.max(project.event_cursor, ++stats.cursors); await page.evaluate(value => window.__event('snapshot', value), project); };
  const current = () => preparation.practices[0];
  await context.route('**/*', async route => {
    const url = new URL(route.request().url()); const p = url.pathname; const req = route.request();
    const json = (value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (url.origin !== origin) { stats.unexpected.push(url.href); return route.abort(); }
    stats.requests.push(req.method() + ' ' + p);
    if (p === '/api/platform/scenarios') return json({ interview: { name: '面试准备', description: '', noun: '面试', steps, fields: [] }, market: { name: '市场调研', description: '', noun: '调研', steps: ['定义调研目标', '研究背景与方案', '访谈提纲', '开展访谈', '洞察分析', '研究成果'], fields: [{ key: 'industry', label: '关注哪个行业？', options: ['消费零售'], multiple: false }] } });
    if (p === '/api/platform/projects') return json([project]);
    if (p === '/api/platform/projects/ip_fixture') return json(project);
    if (p === '/api/platform/projects/ip_fixture/interview' || p === '/api/interview-prep/projects/ip_fixture') return json(preparation);
    if (p === '/api/platform/projects/ip_fixture/materials') {
      const form = await new Request(req.url(), { method: 'POST', headers: req.headers(), body: req.postDataBuffer() }).formData();
      const file = form.get('file');
      const entry = { name: file.name, kind: form.get('kind'), revision: Number(form.get('revision')), request_id: form.get('request_id') };
      stats.materialUploads.push(entry);
      if (stats.holdUpload) await stats.holdUpload;
      if (stats.failUpload) { const failure = stats.failUpload; stats.failUpload = null; return json({ detail: failure }, 422); }
      assert.equal(entry.revision, preparation.revision, '上传使用当前领域版本');
      if (!stats.materialUploads.slice(0, -1).some(value => value.request_id === entry.request_id && value.saved)) {
        const id = 'uploaded-' + preparation.materials.length;
        preparation.materials.push({ id, title: file.name, kind: entry.kind, content: '合成简历正文：我做过需求分析。', version: 1 });
        project.artifacts.push({ id, step: 1, title: file.name, content: '合成简历正文：我做过需求分析。', version: 1, kind: 'material', resource: { type: 'material', id }, readonly: true, versions: [], demo: false });
        preparation.revision++; project.stage = 1; sync(); entry.saved = true;
      }
      return json(project);
    }
    if (p.endsWith('/layout')) { project.layout = { version: project.layout.version + 1, positions: req.postDataJSON().positions }; return json(project.layout); }
    if (p === '/api/interview-prep/answers/answer-one/audio') { stats.uploads++; return json({ ok: true, text: '我组织了合成需求验证。' }); }
    if (/^\/api\/interview-prep\/answers\/answer-(one|two)\/recording$/.test(p)) {
      const wav = Buffer.alloc(1644); wav.write('RIFF'); wav.writeUInt32LE(1636, 4); wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(8000, 24); wav.writeUInt32LE(16000, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(1600, 40);
      return route.fulfill({ contentType: 'audio/wav', body: wav });
    }
    if (p.endsWith('/commands')) {
      const body = req.postDataJSON(); const action = body.scene_command;
      if (!action) {
        stats.typed.push(body);
        if (body.action === 'message') {
          project.messages.push({ id: 'user-' + stats.typed.length, role: 'user', text: body.content, at: 1 });
          project.job = { id: 'chat-' + (++stats.task), message_id: 'assistant-' + stats.task, kind: 'interview_chat', status: 'running', stage: '正在回复', output: '', output_version: 0, attempt: 1, error: '', progress: .1 };
        } else if (body.action === 'cancel') project.job.status = 'cancelled';
        else if (body.action === 'retry') project.job = { ...project.job, id: 'chat-' + (++stats.task), message_id: 'assistant-' + stats.task, status: 'running', output: '', output_version: 0, attempt: 1 };
        else if (body.action === 'archive') { project.stage = 5; project.completed = true; }
        sync(); return json(project);
      }
      assert.equal(body.action, 'scene', '专业操作必须符合统一命令接口契约');
      stats.actions.push(action);
      if (stats.failAction === action.action) { stats.failAction = null; return json({ detail: '合成保存冲突，请保留草稿后重试' }, 409); }
      if (action.action === 'configure') preparation.config = action.config;
      if (action.action === 'material') {
        preparation.materials.push({ id: 'material-one', title: action.title, content: action.content, kind: action.kind, version: 1 }); project.stage = 1;
        project.artifacts.push({ id: 'material-one', step: 1, title: action.title, content: action.content, version: 1, kind: 'material', resource: { type: 'material', id: 'material-one' }, readonly: true, versions: [], demo: false });
      }
      if (action.action === 'prepare') { preparation.plan = structuredClone(plan); preparation.plan_stale = false; project.stage = 2; project.artifacts.push({ id: 'plan', step: 2, title: '面试准备计划', content: plan.summary, version: 1, versions: [], demo: false, resource: { type: 'plan', id: 'plan' } }); }
      if (['start', 'retry_practice'].includes(action.action)) {
        const previous = current();
        preparation.practices.unshift({ id: 'practice-' + preparation.practices.length, status: 'active', config: structuredClone(preparation.config), created_at: 1792200000 + preparation.practices.length, source_practice_id: action.action === 'retry_practice' ? previous.id : null, review_stale: false, review: null, candidate_review: null, job: null, turns: [{ id: 'turn-one', position: 0, question: plan.questions[0].question, target: '个人贡献', status: 'waiting', parent_id: null, feedback: null, answers: [] }] }); project.stage = 3;
      }
      if (action.action === 'answer') current().turns[0].answers.push({ id: 'answer-one', session_id: 'answer-session', status: 'recording', transcript: '', original_transcript: '', version: 1, segments: [], chunk_ids: [] });
      if (action.action === 'prepare_answer') Object.assign(current().turns[0].answers[0], { status: 'confirming', transcript: '我组织了合成需求验证。', original_transcript: '我组织了合成需求验证。', chunk_ids: action.chunk_ids });
      if (action.action === 'confirm_answer') { current().turns[0].status = 'answered'; Object.assign(current().turns[0].answers[0], { status: 'ready', transcript: action.content }); }
      if (action.action === 'correct') {
        const answer = current().turns.flatMap(t => t.answers).find(a => a.id === action.answer_id);
        answer.transcript = action.content; answer.version++; current().review_stale = !!current().review;
      }
      if (action.action === 'finish') { current().status = 'done'; project.stage = 4; }
      if (action.action === 'review') {
        current().review = { summary: '本轮已说明具体行动，可补充验证依据。', findings: [{ dimension: '个人贡献', observation: '回答说明了个人行动', suggestion: '补充验证结果', quote: current().turns[0].answers[0].transcript, answer_id: 'answer-one', segment_ids: [] }], retry_questions: [plan.questions[0]] };
        project.scene_state.insights = [{ text: '回答说明了个人行动', artifact_id: 'review' }];
        project.artifacts.push({ id: 'review', step: 4, title: '整场复盘', content: current().review.summary, version: 1, resource: { type: 'review', id: 'review', practice_id: current().id }, versions: [], demo: false });
      }
      preparation.revision++; sync(); return json(project);
    }
    if (p.startsWith('/api/')) { stats.unexpected.push(req.method() + ' ' + p); return json({}, 404); }
    const target = path.resolve(build, p === '/' ? 'index.html' : p.slice(1));
    if (!target.startsWith(build + path.sep) || !fs.existsSync(target)) return route.fulfill({ status: 404, body: '' });
    const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.ttf': 'font/ttf' };
    return route.fulfill({ contentType: types[path.extname(target)] || 'application/octet-stream', body: fs.readFileSync(target) });
  });
  await page.goto(origin + (legacy ? '/?prep=ip_fixture' : '/?project=ip_fixture'));
  await page.getByRole('navigation', { name: '工作空间' }).waitFor();
  return { page, context, project, preparation, stats, errors, snapshot, sync };
}

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true }); const passed = [];
  const check = async (name, task) => { if (process.env.INTERVIEW_TEST_FILTER && !name.includes(process.env.INTERVIEW_TEST_FILTER)) return; await task(); passed.push(name); console.log('PASS:', name); };
  try {
    await check('旧面试链接进入统一工作台；画布、资料、任务和移动端保留', async () => {
      const f = await fixture(browser, true);
      try {
        assert.equal(new URL(f.page.url()).search, '?project=ip_fixture');
        assert.equal(await f.page.locator('.prep-shell').count(), 0);
        await f.page.getByTestId('research-map').waitFor();
        await f.page.getByRole('tab', { name: '面试对话', exact: true }).waitFor();
        assert.equal(await f.page.evaluate(() => window.__events.length), 1);
        assert.equal(await f.page.evaluate(() => window.__openEvents), 1);
        await f.page.screenshot({ path: path.join(output, 'workspace-desktop.png'), fullPage: true });
        await f.page.setViewportSize({ width: 390, height: 844 });
        assert.ok(await f.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        await f.page.screenshot({ path: path.join(output, 'workspace-mobile.png'), fullPage: true });
        assert.deepEqual(f.stats.unexpected, []); assert.deepEqual(f.errors, []);
      } finally { await f.context.close(); }
    });
    await check('原调研继续使用同一外壳、画布与侧栏，面试专用界面不串入', async () => {
      const f = await fixture(browser, false, true);
      try {
        await f.page.getByTestId('research-map').waitFor();
        await f.page.getByRole('button', { name: '调研路径', exact: true }).waitFor();
        await f.page.getByText('定义调研目标', { exact: true }).first().waitFor();
        await f.page.getByRole('button', { name: '确认目标，生成研究方案', exact: true }).waitFor();
        await f.page.getByRole('tab', { name: '用户洞察', exact: true }).click();
        await f.page.getByRole('heading', { name: '研究证据', exact: true }).waitFor();
        await f.page.getByText('添加访谈原文后，分析将保留段落引用。研究发现描述现象，洞察解释原因，需求建议与待验证假设仍需核实。', { exact: true }).waitFor();
        await f.page.getByRole('tab', { name: '研究助手', exact: true }).waitFor();
        await f.page.getByRole('button', { name: '新建研究项目', exact: true }).waitFor();
        await f.page.getByRole('button', { name: '研究资料', exact: true }).click();
        await f.page.getByRole('heading', { name: '研究资料', exact: true }).waitFor();
        await f.page.getByRole('button', { name: '开展访谈', exact: true }).click();
        await f.page.getByRole('heading', { name: '先审阅访谈提纲', exact: true }).waitFor();
        await f.page.screenshot({ path: path.join(output, 'research-language-desktop.png'), fullPage: true });
        await f.page.getByRole('button', { name: '体察首页', exact: true }).click();
        await f.page.getByLabel('这次想研究什么问题？', { exact: true }).waitFor();
        await f.page.getByRole('button', { name: '创建研究项目', exact: true }).waitFor();
        await f.page.getByRole('heading', { name: '以访谈，唤出知识与经验。', exact: true }).waitFor();
        await f.page.setViewportSize({ width: 390, height: 844 });
        assert.ok(await f.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        await f.page.screenshot({ path: path.join(output, 'research-language-home-mobile.png'), fullPage: true });
        assert.ok(!f.stats.requests.some(r => r.includes('/interview-prep/') || r.endsWith('/interview')));
        assert.deepEqual(f.errors, []); assert.deepEqual(f.stats.unexpected, []);
      } finally { await f.context.close(); }
    });
    await check('正文在完成前分批显示；重复事件、断线快照、停止与重试不重复消息', async () => {
      const f = await fixture(browser);
      try {
        const input = f.page.getByRole('textbox', { name: '给体察助手补充信息' });
        await input.fill('如何说明个人贡献？'); await f.page.getByRole('button', { name: '发送消息', exact: true }).click();
        await f.page.getByRole('region', { name: 'AI 实时回复' }).waitFor();
        const job = f.project.job;
        const delta = { task_id: job.id, message_id: job.message_id, attempt: 1, seq: 1, delta: '先讲清楚你亲自做了什么。' };
        await f.page.evaluate(d => window.__event('message.delta', d, 1), delta);
        await f.page.locator('.streamed-response').getByText(delta.delta, { exact: true }).waitFor();
        assert.equal(f.project.messages.filter(m => m.role === 'assistant').length, 0, '未完成时已能看到正文');
        await f.page.evaluate(d => window.__event('message.delta', d, 1), delta);
        assert.equal(await f.page.locator('.streamed-response').innerText(), delta.delta);
        // 重连权威快照恢复已保存的前缀，不重新拼接一次。
        f.project.job.output = delta.delta; f.project.job.output_version = 1; f.project.event_cursor = 2; f.stats.cursors = 2;
        await f.snapshot();
        const second = { ...delta, seq: 2, delta: '再说明依据。' };
        await f.page.evaluate(d => window.__event('message.delta', d, 4), second);
        await f.page.locator('.streamed-response').getByText('先讲清楚你亲自做了什么。再说明依据。', { exact: true }).waitFor();
        await f.page.getByRole('button', { name: '停止生成', exact: true }).click();
        await f.page.getByRole('button', { name: '重试', exact: true }).click();
        const next = f.project.job;
        await f.page.evaluate(d => window.__event('message.completed', d, 5), { task_id: next.id, message_id: next.message_id, attempt: 1, text: '可以从一项具体行动开始讲。' });
        await f.page.getByText('可以从一项具体行动开始讲。', { exact: true }).waitFor();
        assert.equal(f.project.messages.filter(m => m.role === 'user').length, 1);
        assert.deepEqual(f.errors, []); assert.deepEqual(f.stats.unexpected, []);
      } finally { await f.context.close(); }
    });
    await check('加号选择简历和拖拽上传；错误重试、文件校验与项目资料同步', async () => {
      const f = await fixture(browser);
      const composer = f.page.locator('.studio-composer');
      const drop = async (files, event = 'drop') => {
        const transfer = await f.page.evaluateHandle(values => {
          const data = new DataTransfer();
          for (const value of values) data.items.add(new File([value.content || '合成附件'], value.name, { type: value.type || 'application/pdf' }));
          return data;
        }, files);
        await composer.dispatchEvent(event, { dataTransfer: transfer });
        await transfer.dispose();
      };
      try {
        await f.page.getByRole('textbox', { name: '给体察助手补充信息' }).fill('保留这段尚未发送的讨论。');
        await f.page.getByRole('button', { name: '添加简历附件或引用成果' }).click();
        const chooser = f.page.waitForEvent('filechooser');
        await f.page.getByRole('button', { name: '上传简历附件', exact: true }).click();
        await (await chooser).setFiles({ name: '合成简历.pdf', mimeType: 'application/pdf', buffer: Buffer.from('synthetic-pdf') });
        await f.page.getByRole('status').filter({ hasText: '已加入项目资料' }).waitFor();
        assert.equal(f.stats.materialUploads[0].kind, 'resume');
        assert.equal(await f.page.getByRole('textbox', { name: '给体察助手补充信息' }).inputValue(), '保留这段尚未发送的讨论。');
        await f.page.getByRole('button', { name: '查看资料', exact: true }).click();
        await f.page.locator('.side-file').filter({ hasText: '合成简历.pdf' }).waitFor();
        await drop([{ name: '拖拽简历.DOCX' }], 'dragenter');
        await f.page.getByText('松开以上传简历', { exact: true }).waitFor();
        await drop([{ name: '拖拽简历.DOCX' }]);
        await f.page.getByRole('status').filter({ hasText: '拖拽简历.DOCX' }).filter({ hasText: '已加入项目资料' }).waitFor();
        assert.equal(f.preparation.materials.length, 2);
        f.stats.failUpload = 'PDF 未提取到正文，请先 OCR。';
        await drop([{ name: '失败后重试.pdf' }]);
        await f.page.getByRole('alert').filter({ hasText: 'PDF 未提取到正文' }).waitFor();
        await f.page.getByRole('button', { name: '重试上传', exact: true }).click();
        await f.page.getByRole('status').filter({ hasText: '失败后重试.pdf' }).filter({ hasText: '已加入项目资料' }).waitFor();
        assert.equal(f.stats.materialUploads[2].request_id, f.stats.materialUploads[3].request_id, '失败重试沿用幂等编号');
        const count = f.stats.materialUploads.length;
        await drop([{ name: '不支持.exe' }]);
        await f.page.getByRole('alert').filter({ hasText: '支持 PDF' }).waitFor();
        await drop([{ name: '一.pdf' }, { name: '二.pdf' }]);
        await f.page.getByRole('alert').filter({ hasText: '一次上传一份' }).waitFor();
        await f.page.getByLabel('选择简历附件').setInputFiles({ name: '过大.pdf', mimeType: 'application/pdf', buffer: Buffer.alloc(20 * 1024 * 1024 + 1, 1) });
        await f.page.getByRole('alert').filter({ hasText: '最多 20MB' }).waitFor();
        assert.equal(f.stats.materialUploads.length, count, '无效文件不发起请求');
        let release;
        f.stats.holdUpload = new Promise(resolve => { release = resolve; });
        await drop([{ name: '正在上传.pdf' }]);
        await f.page.getByRole('status').filter({ hasText: '正在上传并解析' }).waitFor();
        await f.page.getByRole('button', { name: '体察首页' }).click();
        assert.ok(f.stats.dialogs.some(value => value.message.includes('正在上传解析')));
        release(); f.stats.holdUpload = null;
        await f.page.getByRole('status').filter({ hasText: '正在上传.pdf' }).filter({ hasText: '已加入项目资料' }).waitFor();
        await f.page.screenshot({ path: path.join(output, 'resume-upload-desktop.png'), fullPage: true });
        await f.page.setViewportSize({ width: 390, height: 844 });
        assert.equal(await f.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
        assert.deepEqual(f.errors, []); assert.deepEqual(f.stats.unexpected, []);
      } finally { await f.context.close(); }
    });
    await check('多份校对草稿独立保护；保存失败保留输入且业务操作不能绕过', async () => {
      const f = await fixture(browser);
      try {
        const answer = (id, transcript) => ({ id, session_id: id + '-session', status: 'ready', transcript, original_transcript: transcript, version: 1, segments: [], chunk_ids: [] });
        f.preparation.practices.push({ id: 'practice-drafts', status: 'done', config: structuredClone(cfg), created_at: 1792200000, source_practice_id: null, review_stale: false, review: null, candidate_review: null, job: null, turns: [
          { id: 'turn-one', position: 0, question: plan.questions[0].question, target: '个人贡献', status: 'answered', parent_id: null, feedback: null, answers: [answer('answer-one', '第一题原识别稿。')] },
          { id: 'turn-two', position: 1, question: plan.questions[1].question, target: '结果依据', status: 'answered', parent_id: null, feedback: null, answers: [answer('answer-two', '第二题原识别稿。')] },
        ] });
        f.preparation.revision++; f.project.stage = 4; f.sync(); await f.snapshot();
        await f.page.getByRole('button', { name: '模拟练习', exact: true }).click();
        await f.page.getByRole('button', { name: '查看回答与复盘', exact: true }).click();
        await f.page.getByRole('button', { name: '校对转写', exact: true }).nth(0).click();
        await f.page.getByRole('button', { name: '校对转写', exact: true }).click();
        const inputs = f.page.getByRole('textbox', { name: '校对回答转写', exact: true });
        await inputs.nth(0).fill('第一题仍需保留的校对草稿。');
        await inputs.nth(1).fill('第二题独立草稿。');
        const reviews = f.stats.actions.filter(a => a.action === 'review').length;
        await f.page.getByRole('button', { name: '生成整场复盘', exact: true }).click();
        await f.page.getByRole('alert').filter({ hasText: '请先保存或取消当前转写修改，再继续生成或推进练习。' }).waitFor();
        assert.equal(f.stats.actions.filter(a => a.action === 'review').length, reviews);
        f.stats.failAction = 'correct';
        await f.page.getByRole('button', { name: '保存校对稿', exact: true }).nth(0).click();
        await f.page.getByText('合成保存冲突，请保留草稿后重试', { exact: true }).first().waitFor();
        assert.equal(await inputs.nth(0).inputValue(), '第一题仍需保留的校对草稿。');
        assert.equal(await inputs.count(), 2, '保存失败不得关闭编辑框');
        await f.page.locator('.prep-answer').nth(1).getByRole('button', { name: '取消', exact: true }).click();
        f.stats.rejectDialogs = true;
        await f.page.getByRole('button', { name: '体察首页', exact: true }).click();
        assert.equal(await inputs.inputValue(), '第一题仍需保留的校对草稿。', '取消其他答案草稿不能清除本题离页保护');
        assert.equal(f.stats.dialogs.filter(d => d.type === 'confirm').length, 1, '一次导航只提示一次');
        await backToHome(f.page);
        assert.equal(new URL(f.page.url()).search, '?project=ip_fixture', '拒绝后退应恢复原项目 URL');
        assert.equal(await inputs.inputValue(), '第一题仍需保留的校对草稿。', '后退不得卸载未保存转写');
        assert.equal(f.stats.dialogs.filter(d => d.type === 'confirm').length, 2, '后退只追加一次确认');
        await f.page.getByRole('button', { name: '保存校对稿', exact: true }).click();
        await inputs.waitFor({ state: 'detached' });
        await f.page.getByText('第一题仍需保留的校对草稿。', { exact: true }).waitFor();
        await f.page.getByRole('button', { name: '体察首页', exact: true }).click();
        await f.page.getByRole('navigation', { name: '工作空间' }).waitFor({ state: 'detached' });
        assert.equal(f.stats.dialogs.filter(d => d.type === 'confirm').length, 2, '保存成功应清除离页保护');
        assert.deepEqual(f.errors, []); assert.deepEqual(f.stats.unexpected, []);
      } finally { await f.context.close(); }
    });
    await check('材料到计划、录音确认、复盘证据、重练在同一工作台完成', async () => {
      const f = await fixture(browser);
      try {
        await f.page.getByRole('button', { name: '项目资料', exact: true }).click();
        await f.page.getByLabel('准备材料类型').selectOption('job_description');
        await f.page.getByRole('textbox', { name: '材料正文', exact: true }).fill('合成简历：我组织过需求验证。');
        await f.page.getByRole('button', { name: '保存材料', exact: true }).click();
        await f.page.getByText(/v1 ·/).waitFor(); await f.snapshot();
        await f.page.getByRole('button', { name: '面试底图', exact: true }).click();
        await f.page.getByRole('button', { name: '打开此阶段', exact: true }).click();
        if (await f.page.getByRole('button', { name: '生成准备方案', exact: true }).count() === 0) {
          await f.page.getByRole('button', { name: '项目概览', exact: true }).click();
          await f.page.getByRole('button', { name: '调整面试目标与设置', exact: true }).click();
        }
        await f.page.getByRole('button', { name: '生成准备方案', exact: true }).click();
        await f.page.getByRole('button', { name: '开始录音练习', exact: true }).click();
        await f.page.getByRole('checkbox', { name: '我同意录音及云端转写、分析我的练习内容' }).check();
        await f.page.getByRole('button', { name: '准备录音回答', exact: true }).click();
        await f.page.getByRole('button', { name: '开始 / 继续录音', exact: true }).click();
        await f.page.getByRole('button', { name: '暂停录音', exact: true }).waitFor();
        await f.page.getByRole('navigation', { name: '工作空间' }).getByRole('button', { name: /^项目资料/ }).click();
        await f.page.getByRole('button', { name: '暂停录音', exact: true }).waitFor();
        assert.ok(f.stats.dialogs.some(d => d.type === 'alert' && d.message.includes('暂停录音')));
        const alerts = f.stats.dialogs.filter(d => d.type === 'alert').length;
        await backToHome(f.page);
        assert.equal(new URL(f.page.url()).search, '?project=ip_fixture', '录音中后退应恢复原项目 URL');
        await f.page.getByRole('button', { name: '暂停录音', exact: true }).waitFor();
        assert.equal(f.stats.dialogs.filter(d => d.type === 'alert').length, alerts + 1, '录音中后退只提示一次');
        await f.page.getByRole('button', { name: '回答完成', exact: true }).click();
        const transcript = f.page.getByRole('textbox', { name: '本题回答转写', exact: true }); await transcript.waitFor();
        await transcript.fill('我亲自组织了合成需求验证。');
        f.stats.rejectDialogs = true;
        await f.page.getByRole('button', { name: '面试底图', exact: true }).click();
        assert.equal(await transcript.inputValue(), '我亲自组织了合成需求验证。', '确认前的校对草稿应受离页保护');
        f.stats.rejectDialogs = false;
        await f.page.getByRole('button', { name: '确认转写并继续', exact: true }).click();
        await f.page.getByRole('button', { name: '结束本轮练习', exact: true }).click();
        await f.page.getByRole('button', { name: '生成整场复盘', exact: true }).click();
        await f.page.getByText('本轮已说明具体行动，可补充验证依据。', { exact: true }).waitFor();
        await f.page.getByRole('button', { name: '回听本题原音（按全文核对）', exact: true }).waitFor();
        await f.page.screenshot({ path: path.join(output, 'review-desktop.png'), fullPage: true });
        await f.page.getByRole('button', { name: '按复盘建议开始重练', exact: true }).click();
        assert.equal(f.preparation.practices.length, 2); assert.equal(f.preparation.practices[0].source_practice_id, f.preparation.practices[1].id);
        assert.equal(f.stats.uploads, 1); assert.equal(await f.page.evaluate(() => window.__mic), 1);
        assert.equal(f.stats.actions.find(action => action.action === 'material').kind, 'job_description');
        assert.equal(await f.page.evaluate(() => window.__events.length), 1);
        assert.deepEqual(f.errors, []); assert.deepEqual(f.stats.unexpected, []);
      } finally { await f.context.close(); }
    });
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify({ passed, fixture: '合成资料、合成音频、拦截 API；不启动服务' }, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
