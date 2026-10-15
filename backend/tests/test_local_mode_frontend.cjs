// 运行真实页面函数，防止本地启动仍要求提交员工登录或暴露共享入口。
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {html, main, section: scriptSection} = require('./frontend_fixture.cjs');
const section = (start, end) => scriptSection(main, start, end);

function harness(payload, ok = true) {
  const elements = new Map();
  function node(id = '') {
    const classes = new Set();
    const tag = html.match(new RegExp(`<[^>]*id="${id}"[^>]*>`));
    const initial = tag?.[0].match(/class="([^"]*)"/);
    (initial?.[1] || '').split(/\s+/).filter(Boolean).forEach(x => classes.add(x));
    return {id, value: id === 'historyScope' ? 'mine' : '', textContent: '', style: {}, children: [],
      readOnly: /readonly/.test(tag?.[0] || ''),
      classList: {add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x),
        toggle(x, force) { const on = force ?? !classes.has(x); if (on) classes.add(x); else classes.delete(x); }},
      append(...items) { this.children.push(...items); }, appendChild(item) { this.children.push(item); },
      set innerHTML(value) { this.children = []; }, get innerHTML() { return ''; }};
  }
  for (const [, id] of html.matchAll(/id="([^"]+)"/g)) elements.set(id, node(id));
  const requests = [];
  const screens = [...elements.values()].filter(x => x.classList.contains('screen'));
  const context = vm.createContext({document: {getElementById: id => elements.get(id),
    querySelectorAll: () => screens, querySelector: () => ({}), createElement: () => node()},
    window: {scrollTo() {}}, localStorage: {setItem() {}, removeItem() {}}, clearInterval() {},
    fetch: async url => { requests.push(url); if (url !== 'api/auth/me') throw Error('意外请求：' + url);
      return {ok, json: async () => payload}; }, confirm: () => { throw Error('本地模式不应确认退出'); }});
  vm.runInContext(`const $=id=>document.getElementById(id);
    let authUser=null,authEnabled=true,authMode='login',currentDetail=null,es=null,recordingPreparation=false;
    let currentProject=null,projectPollToken=0,projectViewToken=0,coverageTimer=null,navigationRevision=0;const projectSelected=new Set();
    ${section('function applyAuthUser(', 'async function submitAuth(')}
    ${section('async function logoutEmployee(', 'function pickType(')}
    ${section('function showScreen(', 'function selectCaptureMode(')}
    ${section('function renderDetailActions(', 'async function renderDetailBody(')}
    ${section('async function toggleSharePanel(', 'async function renderShares(')}
    ${section('async function renderShares(', 'async function renderShareLinks(')}
    ${section('async function renderShareLinks(', 'async function loadShareLinks(')}
    ${section('async function loadProjectOverview(', 'async function openHistory(')}
    function deleteCurrentSession(){}
    function deleteCurrentRecording(){}
    function restoreInterviewDraft(){}
    function restoreOutlineTask(){}
    function restoreNativeSession(){}
    function makeButton(label){const button=document.createElement('button');button.textContent=label;return button;}
  `, context);
  return {context, elements, requests};
}

(async () => {
  const local = harness({user: {id: -1, name: '本地使用者', type: '本地用户'}, auth_enabled: false});
  assert(local.elements.get('authGate').classList.contains('hidden'), '认证探测前不应闪出员工登录页');
  await vm.runInContext('bootstrapAuth()', local.context);
  assert(local.elements.get('authGate').classList.contains('hidden'));
  assert(local.elements.get('scrSetup').classList.contains('on'));
  assert.equal(local.elements.get('userName').value, '本地使用者');
  assert.equal(local.elements.get('userName').readOnly, true);
  assert(local.elements.get('loggedInUser').classList.contains('hidden'));
  assert(local.elements.get('historyScope').classList.contains('hidden'));
  assert.equal(local.elements.get('historyScope').value, 'all');
  vm.runInContext("currentDetail={id:'local',access:'owner',status:'done',report_status:'ready',has_audio:true};renderDetailActions()", local.context);
  const labels = local.elements.get('detailActions').children.map(x => x.textContent);
  assert(!labels.includes('团队共享'));
  for (const label of ['下载纪要', '下载 Word（含逐字稿）', '区分发言人', '删除原始录音', '问逐字稿']) assert(labels.includes(label));
  await vm.runInContext('logoutEmployee()', local.context);
  await vm.runInContext("toggleSharePanel();renderShares();renderShareLinks($('detailShare'))", local.context);
  assert.deepEqual(local.requests, ['api/auth/me']);
  assert(local.elements.get('authGate').classList.contains('hidden'));
  local.context.fetch = async url => {
    assert.equal(url, 'api/projects/overview');
    return {ok: true, json: async () => ({projects: [{project_name: '项目甲', interviews: 3,
      mine: 1, shared: 2, recordings: 3, reports: 2, open_todos: 1}]})};
  };
  await vm.runInContext('loadProjectOverview()', local.context);
  assert.equal(local.elements.get('historyProjects').children[0].children[1].textContent,
    '3 场访谈 · 3 份录音 · 2 份纪要 · 1 条未完成待办');

  for (const enabled of [true, undefined, null, 0]) {
    const staff = harness({user: {id: 7, name: '员工甲', type: '员工'}, auth_enabled: enabled});
    await vm.runInContext('bootstrapAuth()', staff.context);
    assert(!staff.elements.get('loggedInUser').classList.contains('hidden'));
    assert(!staff.elements.get('historyScope').classList.contains('hidden'));
    vm.runInContext("currentDetail={id:'staff',access:'owner',status:'done',report_status:'ready'};renderDetailActions()", staff.context);
    assert(staff.elements.get('detailActions').children.some(x => x.textContent === '团队共享'));
  }
  const anonymous = harness({detail: '未登录'}, false);
  await vm.runInContext('bootstrapAuth()', anonymous.context);
  assert(!anonymous.elements.get('authGate').classList.contains('hidden'), '员工模式认证失败应显示登录页');
  console.log('PASS: local auth bootstraps without login; staff auth and record actions remain available');
})().catch(error => { console.error(error); process.exitCode = 1; });
