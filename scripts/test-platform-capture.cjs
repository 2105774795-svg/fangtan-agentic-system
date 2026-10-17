// Real browser MediaRecorder, FFmpeg and legacy persistence; synthetic ASR/model fixture.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = 'http://127.0.0.1:18998';

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, permissions: ['microphone'] });
    const page = await context.newPage();
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    const post = async (url, data) => { const r = await page.request.post(base + url, { data }); assert.ok(r.ok(), await r.text()); return r.json(); };
    let project = await post('/api/platform/projects', { prompt: '锦州商户访谈 · 录音隔离测试', scenario: 'market', mode: 'ai', request_id: 'capture-' + Date.now() });
    const read = async () => (await page.request.get(`${base}/api/platform/projects/${project.id}`)).json();
    const settled = async () => { for (let i = 0; i < 80; i++) { project = await read(); if (project.job?.status === 'ready') return; if (project.job?.status === 'failed') throw new Error(project.job.error); await page.waitForTimeout(250); } throw new Error('fixture timeout'); };
    await settled();
    const spec = await (await page.request.get(base + '/api/platform/scenarios')).json();
    const answers = Object.fromEntries(spec.market.fields.map(f => [f.key, [f.options[0]]]));
    project = await post(`/api/platform/projects/${project.id}/commands`, { action: 'brief', revision: project.revision, answers }); await settled();
    project = await post(`/api/platform/projects/${project.id}/commands`, { action: 'advance', revision: project.revision }); await settled();
    await page.goto(`${base}/?project=${project.id}&view=capture`);
    await page.getByRole('heading', { name: '确认本场要问的问题', exact: true }).waitFor();
    assert.equal(await page.locator('.outline-checklist input').count(), 3);
    await page.locator('.outline-check').first().click();
    assert.equal(await page.locator('.outline-checklist input').first().isChecked(), false);
    const retained = project.outline_questions.slice(1);
    await page.getByRole('button', { name: '确认提纲，进入现场录音', exact: true }).click();
    await page.getByText('已向受访者说明并取得录音及云端转写同意', { exact: true }).waitFor();
    project = await read(); const sid = project.active_session_id;
    assert.ok(sid); assert.equal(project.stage, 3);
    assert.deepEqual(project.interviews.find(s => s.id === sid).outline, retained);
    await page.reload();
    await page.getByText('已向受访者说明并取得录音及云端转写同意', { exact: true }).waitFor();
    await page.getByRole('checkbox', { name: '已向受访者说明并取得录音及云端转写同意' }).check();
    // A failed ASR acknowledgement must preserve audio without trapping navigation.
    const failedAudio = '**/api/sessions/*/audio';
    await page.route(failedAudio, route => route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ detail: '录音已保存，转写失败；重试不会重复保存录音' }) }));
    await page.getByRole('button', { name: '开始录音', exact: true }).click();
    await page.waitForTimeout(800);
    await page.getByRole('button', { name: '暂停录音', exact: true }).click();
    await page.getByText(/1 个分片待上传或转写/).waitFor();
    await page.getByRole('button', { name: '开始录音', exact: true }).isEnabled().then(enabled => assert.ok(enabled));
    assert.deepEqual(await page.locator('nav[aria-label="工作空间"] button span').allTextContents(), ['调研底图', '现场采集', '项目资料', '项目概览']);
    await page.getByRole('button', { name: '调研底图', exact: true }).click();
    await page.getByTestId('research-map').waitFor();
    await page.getByRole('button', { name: /^现场采集/ }).click();
    await page.getByText(/1 个分片待上传或转写/).waitFor();
    await page.reload();
    await page.getByText(/1 个分片待上传或转写/).waitFor();
    await page.unroute(failedAudio);
    await page.getByRole('button', { name: '重试上传 / 转写', exact: true }).click();
    await page.getByText(/1 个分片待上传或转写/).waitFor({ state: 'hidden' });
    await page.getByRole('checkbox', { name: '已向受访者说明并取得录音及云端转写同意' }).check();
    await page.getByRole('button', { name: '开始录音', exact: true }).click();
    await page.getByRole('button', { name: '暂停录音', exact: true }).waitFor();
    await page.waitForTimeout(11200); // Cross the independent 10-second segment boundary.
    await page.getByRole('button', { name: '暂停录音', exact: true }).click();
    await page.waitForFunction(async id => { const s = await (await fetch(`/api/sessions/${id}`)).json(); return s.utterances?.length >= 2; }, sid, { timeout: 30000 });
    const finish = page.getByRole('button', { name: '结束访谈并生成纪要', exact: true });
    await finish.click();
    await page.getByRole('button', { name: '同步逐字稿与纪要到工作台', exact: true }).waitFor();
    await page.waitForFunction(async id => { const s = await (await fetch(`/api/sessions/${id}`)).json(); return s.report_status === 'ready'; }, sid);
    await page.getByRole('button', { name: '同步逐字稿与纪要到工作台', exact: true }).click();
    await page.waitForFunction(async id => { const p = await (await fetch(`/api/platform/projects/${id}`)).json(); return p.artifacts.some(a => a.kind === 'session_report'); }, project.id);
    project = await read();
    assert.equal(project.artifacts.filter(a => a.kind === 'material' && a.source_session === sid).length, 1);
    await page.screenshot({ path: 'data/qa/platform-ai/capture-recording.png', animations: 'disabled' });
    await page.getByRole('button', { name: '返回工作台继续分析', exact: true }).click();
    await page.getByTestId('research-map').waitFor();
    await page.getByRole('button', { name: /^现场采集/ }).click();
    await page.getByRole('button', { name: '导入已有材料', exact: true }).click();
    await page.getByLabel('提交已有录音或文字稿', { exact: true }).setInputFiles({ name: '合成访谈.txt', mimeType: 'text/plain', buffer: Buffer.from('受访者：店里准备试行夜间营业。\n采访者：这个建议还需要核实。') });
    await page.waitForFunction(async id => { const p = await (await fetch(`/api/platform/projects/${id}`)).json(); return p.artifacts.filter(a => a.kind === 'material').length === 2; }, project.id, { timeout: 30000 });
    await page.getByRole('button', { name: '导入已有材料', exact: true }).click();
    // A short synthetic WAV also exercises the original audio import/transcription path.
    const audio = Buffer.alloc(44 + 16000 * 2);
    audio.write('RIFF'); audio.writeUInt32LE(audio.length - 8, 4); audio.write('WAVEfmt ', 8); audio.writeUInt32LE(16, 16); audio.writeUInt16LE(1, 20); audio.writeUInt16LE(1, 22); audio.writeUInt32LE(16000, 24); audio.writeUInt32LE(32000, 28); audio.writeUInt16LE(2, 32); audio.writeUInt16LE(16, 34); audio.write('data', 36); audio.writeUInt32LE(audio.length - 44, 40);
    await page.getByLabel('提交已有录音或文字稿', { exact: true }).setInputFiles({ name: 'synthetic.wav', mimeType: 'audio/wav', buffer: audio });
    await page.waitForFunction(async id => { const p = await (await fetch(`/api/platform/projects/${id}`)).json(); return p.artifacts.filter(a => a.kind === 'material').length === 3; }, project.id, { timeout: 30000 });
    await page.screenshot({ path: 'data/qa/platform-ai/capture-import.png', animations: 'disabled' });
    assert.deepEqual(errors, []);
    fs.writeFileSync('data/qa/platform-ai/capture-result.json', JSON.stringify({ success: true, project: project.id, session: sid, checks: ['ASR failure allows navigation', 'failed audio survives refresh and retries', 'sidebar order', 'outline subset', 'refresh recovery', 'two independently decodable recording segments', 'raw transcript sync', 'memo sync', 'text import', 'audio import'], model: 'synthetic fixture' }, null, 2));
    console.log('PASS: selected outline, persistent session, real MediaRecorder segments + FFmpeg, transcript/memo return, text and audio import. ASR/model mocked.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
