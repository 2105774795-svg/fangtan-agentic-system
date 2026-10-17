// Isolated fixture only. Verifies two sequential sync commands use new revisions.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const base = process.env.PLATFORM_TEST_URL || 'http://127.0.0.1:19001';
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage();
    const request = async (url, data, method = 'post') => {
      const r = await page.request[method](base + url, { data });
      assert.ok(r.ok(), await r.text()); return r.json();
    };
    let p = await request('/api/platform/projects', { prompt: '锦州批量同步·合成验收', scenario: 'market', mode: 'ai', request_id: 'bulk-' + Date.now() });
    const read = async () => (await page.request.get(`${base}/api/platform/projects/${p.id}`)).json();
    const settle = async () => {
      for (let i = 0; i < 100; i++) {
        p = await read();
        if (p.job?.status === 'ready') return;
        assert.notEqual(p.job?.status, 'failed');
        await page.waitForTimeout(150);
      }
      throw Error('fixture did not settle');
    };
    await settle();
    const spec = await (await page.request.get(base + '/api/platform/scenarios')).json();
    const answers = Object.fromEntries(spec.market.fields.map(f => [f.key, [f.options[0]]]));
    p = await request(`/api/platform/projects/${p.id}/commands`, { action: 'brief', revision: p.revision, answers }); await settle();
    p = await request(`/api/platform/projects/${p.id}/commands`, { action: 'advance', revision: p.revision }); await settle();
    const wav = Buffer.alloc(44 + 32000);
    wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16);
    wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(16000, 24); wav.writeUInt32LE(32000, 28);
    wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(32000, 40);
    for (const subject of ['首访', '补访']) {
      const { session_id: sid } = await request('/api/sessions/', { subject });
      await request(`/api/sessions/${sid}/project`, { project_id: p.id }, 'patch');
      const upload = await page.request.post(`${base}/api/sessions/${sid}/audio`, { multipart: { chunk_id: 'bulk-' + Date.now(), audio: { name: 'test.wav', mimeType: 'audio/wav', buffer: wav } } });
      assert.equal(upload.status(), 200);
      await request(`/api/sessions/${sid}/finish?background=true`, {});
    }
    await page.goto(`${base}/?project=${p.id}&view=capture`);
    await page.getByRole('button', { name: '同步已结束访谈', exact: true }).click();
    for (let i = 0; i < 100; i++) {
      p = await read();
      if (p.interviews.every(item => item.synced)) break;
      await page.waitForTimeout(150);
    }
    assert.equal(p.artifacts.filter(a => a.kind === 'material').length, 2);
    assert.ok(p.interviews.every(item => item.synced));
    await page.getByRole('button', { name: '同步已结束访谈', exact: true }).waitFor({ state: 'hidden' });
    console.log('PASS: one bulk-sync action saves both ended interviews without revision conflicts or omitted sources.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
