// Run against serve-platform-test.py: isolated database and model fixture only.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    const base = 'http://127.0.0.1:18998';
    const specs = await (await page.request.get(base + '/api/platform/scenarios')).json();
    let source = await (await page.request.post(base + '/api/platform/projects', { data: {
      prompt: '锦州市场调研', scenario: 'market', mode: 'demo', request_id: 'demo-upgrade-' + Date.now(),
    } })).json();
    const answers = Object.fromEntries(specs.market.fields.map(f => [f.key, [f.options[0]]]));
    source = await (await page.request.post(`${base}/api/platform/projects/${source.id}/commands`, {
      data: { action: 'brief', revision: source.revision, answers, context: '保留我的调研需求' },
    })).json();
    await page.goto(`${base}/?project=${source.id}`);
    await page.getByRole('region', { name: '历史示例提示' }).waitFor();
    await page.getByRole('button', { name: '转为真实 AI 调研', exact: true }).click();
    await page.getByText('已识别锦州。请选择行业、目标和受访对象。', { exact: true }).waitFor();
    const id = new URL(page.url()).searchParams.get('project');
    assert.notEqual(id, source.id);
    await page.locator('.briefing-card').waitFor();
    const migrated = await (await page.request.get(`${base}/api/platform/projects/${id}`)).json();
    assert.equal(migrated.mode, 'ai');
    assert.deepEqual(migrated.answers, answers);
    assert.equal(migrated.context, source.context);
    assert.equal(migrated.artifacts.length, 0);
    assert.equal(await page.locator('.demo-upgrade').count(), 0);
    assert.equal(await page.locator('.map-branch').count(), 1);
    assert.equal(await page.getByRole('radio', { name: answers.industry[0], exact: true }).getAttribute('aria-checked'), 'true');
    assert.equal(await page.getByLabel('给体察助手补充信息', { exact: true }).inputValue(), source.context);
    const unchanged = await (await page.request.get(`${base}/api/platform/projects/${source.id}`)).json();
    assert.deepEqual(unchanged, source);
    await page.screenshot({ path: 'data/qa/platform-ai/demo-upgrade.png', animations: 'disabled' });
    console.log('PASS: historical example creates a real AI project with inputs only; original remains intact.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
