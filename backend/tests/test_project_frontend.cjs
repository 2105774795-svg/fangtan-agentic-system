// 离线浏览器验证：拦截全部 API，不启动服务、不调用真实 AI、不改用户录音。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../..');
const {html, serveScript} = require('./frontend_fixture.cjs');
const preview = path.join(root, 'data/qa/projects');

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.PLAYWRIGHT_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 960}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let archived = '', generated = null, statusReads = 0;
    let fieldSettings=null,fieldChosen=[],fieldAsked=new Set();
    const fieldQuestions=['这个市场最近一月客流变了多少？','这个档口租金一个月多少？','双福市场最近哪类水果更好卖？','从产地到这个市场到货多久？','这个档口水果损耗有多少？','接下来一个月准备调整哪些品种？'];
    const project = {id: 'project-one', name: '重庆制造业调研', access: 'owner', owner_id: -1,
      interviews: 3, recordings: 3, reports: 2};
    const sessions = [
      {id: 'one', display_subject: '汽车零件工厂访谈', status: 'done', utterance_count: 56, report_status: 'ready', merge_eligible: true, created_at: 1788000000},
      {id: 'two', display_subject: '渠道与订单变化', status: 'done', utterance_count: 38, report_status: 'ready', merge_eligible: true, created_at: 1788001000},
      {id: 'live', display_subject: '尚未结束的现场录音', status: 'live', utterance_count: 8, report_status: 'none', merge_eligible: false, created_at: 1788002000},
    ];
    const record = {id: 'record-one', project_id: project.id, title: '重庆制造业综合访谈记录',
      status: 'ready', stage: '总访谈记录已生成', created_at: 1788003000,
      sources: [{id: 'one', subject: sessions[0].display_subject, reference: '访谈01'},
                {id: 'two', subject: sessions[1].display_subject, reference: '访谈02'}],
      markdown: '# 总访谈记录：重庆制造业\n## 综合摘要\n订单结构发生变化 [访谈01·段落05·00:01:20]。\n## 共识与分歧\n渠道扩展带来新增订单，但不同公司的利润变化不同 [访谈02·段落03·00:02:10]。'};
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url()), pathname = url.pathname;
      let body;
      if (pathname === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (await serveScript(route, pathname)) return;
      if (pathname.startsWith('/assets/')) return route.fulfill({status: 204});
      if (pathname === '/api/auth/me') body = {user: {id: -1, name: '本地使用者'}, auth_enabled: false};
      else if(pathname==='/api/outlines/jobs') {fieldSettings=request.postDataJSON();body={job_id:'field-job',status:'generating'}}
      else if(pathname==='/api/outlines/jobs/field-job') body={job_id:'field-job',status:'ready',settings:fieldSettings,
        background:'双福国际农贸城是本次调研指定的农产品交易市场，可重点了解果蔬批发、进货渠道与周边客流。',
        background_note:'背景为访谈准备参考，市场规模与经营现状请以现场核实为准。',background_sources:[],questions:fieldQuestions};
      else if(pathname==='/api/sessions/') {fieldChosen=request.postDataJSON().outline;body={session_id:'field-session'}}
      else if(pathname==='/api/sessions/field-session/state'||pathname==='/api/sessions/field-session/coverage'){
        const dims=fieldChosen.map((q,i)=>({id:'outline:'+i,name:`${i+1} ${q}`,item:q,icon:'问',covered:fieldAsked.has(q),subs:fieldAsked.has(q)?[]:[q]}));
        body={subject:fieldSettings.subject,status:'live',outline:fieldChosen,utterances:[],suggestions:[],coverage:{mode:'outline',dims,covered:dims.filter(d=>d.covered).map(d=>d.id),missing:dims.filter(d=>!d.covered).map(d=>d.id),ratio:fieldAsked.size/fieldChosen.length}};
        if(pathname.endsWith('/coverage'))body=body.coverage;
      }
      else if(pathname==='/api/sessions/field-session/events') return route.fulfill({contentType:'text/event-stream',body:'data: {"type":"ready"}\n\n'});
      else if(pathname==='/api/sessions/field-session/outline_asked'){fieldAsked.add(request.postDataJSON().item);body={ok:true}}
      else if (pathname === '/api/projects' && request.method() === 'POST') {
        const data = request.postDataJSON();assert.equal(data.name, '新调研项目');body = {...project, id: 'new-project', name: data.name};
      } else if (pathname === '/api/projects') body = {items: [project, ...(archived ? [{...project, id: 'new-project', name: '新调研项目'}] : [])], projects: [project.name]};
      else if (pathname === '/api/projects/project-one/records') {
        generated = request.postDataJSON();assert.deepEqual(generated.session_ids, ['one', 'two']);body = {record_id: record.id, status: 'generating'};
      } else if (pathname === '/api/projects/project-one') body = {project, items: sessions, total: 3, records: generated ? [record] : []};
      else if (pathname === '/api/project-records/record-one') body = ++statusReads === 1 ? {...record, status: 'generating', stage: '正在核对完整逐字稿', markdown: ''} : record;
      else if (pathname === '/api/sessions/new-recording/project') {
        archived = request.postDataJSON().project_id;body = {project_id: archived, project_name: archived === 'new-project' ? '新调研项目' : project.name};
      } else if (pathname === '/api/sessions/new-recording') body = {id: 'new-recording', project_id: archived, project_name: archived === 'new-project' ? '新调研项目' : archived ? project.name : ''};
      else throw new Error('未预期的请求：' + pathname);
      await route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    });

    await page.goto('https://interview.test/');
    await page.waitForFunction(() => document.getElementById('authGate').classList.contains('hidden'));
    await page.locator('#interviewEntry').click();
    await page.locator('#subject').fill('双福市场水果档口经营变化');
    await page.locator('#industry').selectOption('食品饮料');
    await page.locator('#regionCity').fill('重庆市');
    await page.locator('#regionMarket').fill('双福国际农贸城');
    await page.locator('#outlineBtn').click();await page.locator('#outlineChoices input').first().waitFor();
    assert.deepEqual(fieldSettings.location,{city:'重庆市',market:'双福国际农贸城'});
    assert.match(await page.locator('#placeBackground').innerText(),/双福国际农贸城/);
    const layout=await page.locator('#outlineChoices label').first().evaluate(row=>{const a=row.querySelector('input').getBoundingClientRect(),b=row.querySelector('span').getBoundingClientRect();return {width:a.width,sameLine:Math.abs(a.top-b.top)<12,textRight:b.left>a.right}});
    assert(layout.width<=20&&layout.sameLine&&layout.textRight,'提纲勾选框应与问题同行，不受通用表单样式影响');
    for(let i=2;i<fieldQuestions.length;i++)await page.locator('#outlineChoices input').nth(i).uncheck();
    fs.mkdirSync(preview,{recursive:true});
    await page.screenshot({path:path.join(preview,'regional-outline-desktop.png'),fullPage:true,animations:'disabled'});
    await page.locator('#outlineRecordBtn').click();await page.locator('#dims .dim').first().waitFor();
    assert.deepEqual(fieldChosen,fieldQuestions.slice(0,2),'只有已筛选的问题进入录音');
    assert.equal(await page.locator('#covTitle').innerText(),'筛选提纲摸排进度');
    await page.locator('#dims .dim').first().click();await page.locator('#dimsubs .mark').click();
    await page.waitForFunction(()=>document.getElementById('covNum').textContent==='50%');
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.screenshot({path:path.join(preview,'outline-recording-mobile.png'),fullPage:true,animations:'disabled'});
    await page.locator('#liveFieldTab').click();await page.setViewportSize({width:1280,height:960});
    await page.locator('#tabRecord').click();
    await page.locator('#recordingExtras summary').click();
    await page.locator('#captureProjectsTab').click();
    await page.locator('#recordingProjectsList .session-card').waitFor();
    assert(await page.locator('#captureProjectsPanel').isVisible());
    fs.mkdirSync(preview, {recursive: true});
    await page.screenshot({path: path.join(preview, 'project-folders-desktop.png'), fullPage: true, animations: 'disabled'});
    await page.locator('#recordingProjectsList .session-card').click();
    await page.locator('#merge_one').waitFor();
    assert(await page.locator('#merge_live').isDisabled());
    assert(await page.locator('#projectMergeBtn').isDisabled());
    await page.locator('#merge_one').check();
    assert(await page.locator('#projectMergeBtn').isDisabled());
    await page.locator('#merge_two').check();
    assert(await page.locator('#projectMergeBtn').isEnabled());
    await page.locator('#projectRecordTitle').fill(record.title);
    await page.screenshot({path: path.join(preview, 'project-merge-desktop.png'), fullPage: true, animations: 'disabled'});
    await page.locator('#projectMergeBtn').click();
    await page.locator('#projectRecordExports a').first().waitFor({timeout: 10000});
    assert.equal(generated.title, record.title);
    assert.match(await page.locator('#projectRecordBody').innerText(), /访谈01·段落05/);
    assert.equal(await page.locator('#projectRecordSources button').count(), 2);
    assert.match(await page.locator('#projectRecordExports a').first().getAttribute('href'), /format=docx$/);
    await page.setViewportSize({width: 390, height: 844});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '项目页不得横向溢出');
    await page.screenshot({path: path.join(preview, 'project-merge-mobile.png'), fullPage: true, animations: 'disabled'});

    await page.evaluate(() => {sid = 'new-recording';showScreen('scrReport');prepareReportArchive(sid);});
    await page.locator('#reportArchiveBtn').waitFor({state: 'visible'});
    await page.waitForFunction(() => !document.getElementById('reportArchiveBtn').disabled);
    await page.locator('#reportProject').selectOption(project.id);
    await page.locator('#reportArchiveBtn').click();
    await page.waitForFunction(() => document.getElementById('reportArchiveStatus').textContent.includes('已保存到'));
    assert.equal(archived, project.id);
    await page.locator('#reportNewProject').fill('新调研项目');
    await page.locator('#reportNewProjectBtn').click();
    await page.waitForFunction(() => document.getElementById('reportArchiveStatus').textContent.includes('已归档到「新调研项目」'));
    assert.equal(archived, 'new-project');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '归档页不得横向溢出');
    await page.screenshot({path: path.join(preview, 'report-archive-mobile.png'), fullPage: true, animations: 'disabled'});
    assert.deepEqual(errors, []);
    console.log('PASS: 地区提纲、问题筛选转录音与摸排标记；项目入口、合并、归档及移动端布局');
    console.log('截图目录：' + preview);
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error);process.exitCode = 1;});
