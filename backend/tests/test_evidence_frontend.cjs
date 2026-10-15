// 离线 VM 交互：问透证据、安全来源、草稿恢复和直接项目下载，不启动服务、不访问网络。
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {html, main, section} = require('./frontend_fixture.cjs');

function dom() {
  const all = [], byId = new Map(), htmlWrites = [];
  function element(tag = 'div') {
    const classes = new Set();
    let text = '';
    const el = {tagName: tag.toUpperCase(), children: [], value: '', style: {}, dataset: {}, className: '',
      clickCount: 0, disabled: false,
      classList: {add: name => classes.add(name), remove: name => classes.delete(name),
        contains: name => classes.has(name), toggle(name, force) {
          const enabled = force === undefined ? !classes.has(name) : force;
          enabled ? classes.add(name) : classes.delete(name);return enabled;
        }},
      append(...children) {this.children.push(...children);},
      appendChild(child) {this.children.push(child);return child;},
      addEventListener() {}, focus() {}, click() {this.clickCount++;return this.onclick?.();},
      get textContent() {return text + this.children.map(child => typeof child === 'string' ? child : child.textContent).join('');},
      set textContent(value) {text = String(value);this.children = [];},
      get innerHTML() {return text;},
      set innerHTML(value) {htmlWrites.push(String(value));text = String(value);this.children = [];},
    };
    all.push(el);return el;
  }
  const $ = id => {if (!byId.has(id)) byId.set(id, element());return byId.get(id);};
  const document = {createElement: element, createTextNode: text => ({textContent: String(text)}),
    querySelectorAll(selector) {return selector === '.dim' ? all.filter(el => el.className.split(' ').includes('dim')) : [];}};
  return {$, document, all, byId, htmlWrites};
}

async function coverageTest() {
  const ui = dom(), requests = [];
  const cov = {engine: 'ai', mode: 'outline', analysis_status: 'ready', ratio: 1, covered: ['outline:0'], missing: [],
    dims: [{id: 'outline:0', item: '一个月房租多少？', name: '01 一个月房租多少？', icon: '问', covered: true,
      status: 'sufficient', reason: '受访者明确给出了月租金额。', manual_asked: false,
      evidence: [{seq: 4, start_ms: 65000, quote: '一个月房租一万元。<img src=x onerror=alert(1)>'}], subs: [], follow_up: ''}]};
  const context = vm.createContext({...ui, console, sid: 'session-one', Math, Number,
    fetch: async (url, options) => {requests.push({url, options});return {ok: true};},
    responseError: async () => '失败', hint() {}, refreshCoverage: async () => {}, syncLiveState: async () => {},
  });
  vm.runInContext(section(main, 'let lastCoverage=null;', 'function addUtt(') +
    section(main, 'function fmtMs(', 'function showSug('), context);
  context.cov = cov;
  vm.runInContext('renderCoverage(cov)', context);
  assert.equal(ui.$('covNum').textContent, '100%');
  assert.equal(ui.$('covTitle').textContent, 'AI 问透进度');
  const completed = ui.$('dims').children[0];
  assert.match(completed.textContent, /已问透/);
  completed.click();
  assert(!ui.$('dimsubs').classList.contains('hidden'), '已充分项仍可打开证据，不能像旧卡片一样直接 return');
  assert.match(ui.$('dimsubs').textContent, /明确给出了月租金额/);
  assert.match(ui.$('dimsubs').textContent, /01:05 · 段落5/);
  assert.match(ui.$('dimsubs').textContent, /<img src=x onerror=alert\(1\)>/);
  assert(!ui.all.some(el => el.tagName === 'IMG'), '原话中的 HTML 应是文字，不得变成 DOM');

  const partial = {...cov, ratio: 0, covered: [], missing: ['outline:0'], dims: [{...cov.dims[0],
    covered: false, status: 'partial', reason: '只说租金涨了，还没有具体金额。',
    evidence: [{seq: 0, start_ms: 2000, quote: '房租最近涨了一些。'}], subs: ['一个月实际要交多少租金？']}]};
  context.cov = partial;
  let synced = 0;
  context.syncLiveState = async () => {
    synced++;partial.dims[0].manual_asked = true;context.renderCoverage(partial);
  };
  vm.runInContext('renderCoverage(cov)', context);
  ui.$('dims').children[0].click();
  const manual = ui.$('dimsubs').children.find(el => /标记问过/.test(el.textContent));
  assert.match(manual.textContent, /不等于问透/);
  await manual.click();
  assert.equal(requests[0].url, 'api/sessions/session-one/outline_asked');
  assert.equal(JSON.parse(requests[0].options.body).item, '一个月房租多少？');
  assert.equal(synced, 1);
  assert.equal(ui.$('covNum').textContent, '0%', '手动标记后必须保留服务端 AI 充分度，不能视觉改为 100%');
  assert(!ui.$('dims').children[0].className.includes(' hit'));
  ui.$('dims').children[0].click();
  assert.match(ui.$('dimsubs').textContent, /已人工标记为问过（不等于问透）/);
  assert.match(ui.$('dims').children[0].textContent, /待问透/);
  assert(ui.htmlWrites.every(value => value === ''), '动态访谈内容不能通过 innerHTML 注入');
}

function sourceDraftTest() {
  const ui = dom(), storage = new Map();
  for (const [id, value] of Object.entries({subject: '水果市场', industry: '食品饮料', focus: '成本',
    outline: '', regionCity: '重庆市', regionMarket: '双福国际农贸城'})) ui.$(id).value = value;
  const sources = [
    {id: 'S1', title: '公开来源 <script>alert(1)</script>', url: 'https://www.example.gov.cn/market', published_at: '2026-01-01'},
    {id: 'S2', title: '另一份公开资料', url: 'http://example.org/report'},
    {id: 'X1', title: '拒绝脚本', url: 'javascript:alert(1)'},
    {id: 'X2', title: '拒绝数据URL', url: 'data:text/html,hello'},
    {id: 'X3', title: '拒绝带凭据的地址', url: 'https://username:password@example.org/private'},
  ];
  const context = vm.createContext({...ui, URL, Set, Object, JSON, console, authUser: {id: 7}, placeBrief: null,
    selectedQuestions: new Set(), lastChoiceQuestions: [], outlineOriginal: '',
    selectedInterviewee: () => '商户', outlineQuestions: () => [], renderOutlineChoices() {},
    outlineEdited() {}, confirm: () => true,
    localStorage: {getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)},
    generatedOutline: {background: '据公开资料整理的市场背景。<img src=x onerror=alert(1)>',
      background_note: '请结合资料日期和实地情况核实。', sources, research_status: 'retrieved',
      retrieved_at: '2026-10-15T12:00:00Z', questions: ['最近成本有什么变化？'],
      settings: {location: {city: '重庆市', market: '双福国际农贸城'}}},
  });
  vm.runInContext(section(main, 'const draftFields=', 'function outlineQuestions()') +
    section(main, 'function applyGeneratedOutline(', 'async function pollOutlineTask('), context);
  vm.runInContext('applyGeneratedOutline();saveInterviewDraft()', context);
  const links = ui.all.filter(el => el.tagName === 'A');
  assert.equal(links.length, 2, '来源只允许无凭据的 HTTP(S)，拒绝 javascript/data URL');
  assert(links.every(link => link.target === '_blank' && link.rel === 'noopener noreferrer'));
  assert.match(links[0].textContent, /<script>alert\(1\)<\/script>/);
  assert.match(ui.$('placeBackgroundNote').textContent, /(?:发布|资料日期)：2026-01-01/);
  assert.match(ui.$('placeBackgroundNote').textContent, /检索时间：/);
  assert.match(ui.$('placeBackground').textContent, /<img src=x onerror=alert\(1\)>/);
  assert(!ui.all.some(el => ['SCRIPT', 'IMG'].includes(el.tagName)));
  const saved = JSON.parse(storage.get('fangtan_interview_draft_7'));
  assert.equal(saved.placeBrief.sources[0].url, sources[0].url);
  assert.equal(saved.placeBrief.researchStatus, 'retrieved');
  assert.equal(saved.placeBrief.retrievedAt, '2026-10-15T12:00:00Z');
  vm.runInContext('placeBrief=null;renderPlaceBackground();restoreInterviewDraft()', context);
  assert.match(ui.$('placeBackgroundNote').textContent, /公开来源 <script>/, '草稿恢复必须保留可回查来源');
  assert(!ui.$('placeBackgroundCard').classList.contains('hidden'));
  ui.$('regionMarket').value = '另一处园区';
  vm.runInContext('renderPlaceBackground()', context);
  assert(ui.$('placeBackgroundCard').classList.contains('hidden'), '更换地点必须隐藏原地点背景及来源');
  assert.equal(ui.$('placeBackgroundNote').textContent, '');
  assert(ui.htmlWrites.every(value => value === ''), '来源和背景不可作为 HTML 注入');
}

function bundleAndLayoutTest() {
  const ui = dom();let fetched = false;
  const context = vm.createContext({...ui, currentProject: {id: '项目 A/测试'}, projectTotal: 3, encodeURIComponent,
    fetch() {fetched = true;throw Error('不得整包 fetch 进入页面内存');},
    fetchBlob() {throw Error('不得用 fetchBlob');}, URL: {createObjectURL() {throw Error('不得创建整包 Blob');}}});
  vm.runInContext(section(main, 'function downloadProjectBundle(){', 'async function renameRecordingProject('), context);
  vm.runInContext('downloadProjectBundle()', context);
  const link = ui.all.find(el => el.tagName === 'A');
  assert.equal(link.href, `api/projects/${encodeURIComponent('项目 A/测试')}/bundle.zip`);
  assert.equal(link.download, '项目资料.zip');
  assert.equal(link.clickCount, 1);
  assert.equal(link.rel, 'noopener noreferrer');
  assert.equal(fetched, false);
  assert.match(ui.$('projectBundleStatus').textContent, /浏览器将下载 ZIP/);
  for (const id of ['scrSetup', 'scrRecordPrepare', 'scrLive', 'captureHub', 'liveWorkspace',
    'placeBackgroundCard', 'covNum', 'covTitle', 'dims', 'dimsubs', 'projectBundleBtn']) {
    assert(html.includes(`id="${id}"`), `原界面或现有容器不能丢失：${id}`);
  }
  assert.match(html, /class="orb"[^>]*id="orb"|id="orb"[^>]*class="orb"/);
  assert.match(html, /class="covwrap"/);
  assert.match(html, /class="dimsubs hidden"/);
  assert(!html.includes('id="regionDistrict"') && !html.includes('id="regionTown"') && !html.includes('id="regionAddress"'));
  assert.match(html, /id="projectBundleBtn"[^>]*onclick="downloadProjectBundle\(\)"/);
}

(async () => {
  await coverageTest();sourceDraftTest();bundleAndLayoutTest();
  console.log('PASS: AI 已问透项证据可回查、时间定位、手动已问不涨比例、公开来源防注入与草稿恢复、项目直链下载及原 UI 容器保留');
})().catch(error => {console.error(error);process.exitCode = 1;});
