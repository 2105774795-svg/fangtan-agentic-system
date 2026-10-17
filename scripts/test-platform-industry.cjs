// Run against the isolated synthetic model fixture; never submits real research.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const base = process.env.PLATFORM_TEST_URL || 'http://127.0.0.1:18999';

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    async function create(prompt) {
      await page.goto(base);
      await page.getByLabel('今天您想调研什么？').fill(prompt);
      await page.getByRole('button', { name: '开始调研', exact: true }).click();
      await page.locator('.briefing-card').waitFor();
    }
    async function submit(industry) {
      const request = page.waitForRequest(request => request.method() === 'POST'
        && request.url().includes('/commands') && request.postDataJSON()?.action === 'brief');
      await page.getByRole('button', { name: '确认需求，生成计划', exact: true }).click();
      assert.deepEqual((await request).postDataJSON().answers.industry, [industry]);
      await page.getByRole('button', { name: '确认计划，生成提纲', exact: true }).waitFor();
    }
    await create('想调研锦州精品咖啡市场');
    assert.ok((await page.locator('.resolved-context').textContent()).includes('精品咖啡'));
    assert.equal(await page.locator('.brief-form').getByRole('radio').count(), 0);
    await page.getByRole('checkbox', { name: '了解市场现状', exact: true }).click();
    await page.getByRole('checkbox', { name: '消费者', exact: true }).click();
    await submit('精品咖啡');
    await create('想了解锦州本地消费市场，准备一次实地调研');
    const radios = page.locator('.brief-form').getByRole('radio');
    assert.equal(await radios.count(), 6);
    for (const radio of await radios.all()) {
      await radio.click();
      assert.equal(await page.locator('.brief-form [role="radio"][aria-checked="true"]').count(), 1);
    }
    await page.getByRole('radio', { name: '消费零售', exact: true }).click();
    await page.getByRole('checkbox', { name: '了解市场现状', exact: true }).click();
    await page.getByRole('checkbox', { name: '商户 / 店主', exact: true }).click();
    await submit('消费零售');
    assert.deepEqual(errors, []);
    console.log('PASS: refined auto industry and all six market industry radios');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
