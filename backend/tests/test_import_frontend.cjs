// 模拟导入失败后再次点击按钮：已有会话只重试纪要，不重复上传建档。
const assert = require('node:assert/strict');
const vm = require('node:vm');

const {main, section} = require('./frontend_fixture.cjs');
const reportHelper = section(main, 'async function requestReport(', 'async function importMaterial(){');
const source = section(main, 'async function importMaterial(){', 'const importInput=');
const elements = new Map();
const requests = [];
const notices = [];
function element(id) {
  if (!elements.has(id)) elements.set(id, {value: '', style: {}, classList: {add() {}, remove() {}}, disabled: false});
  return elements.get(id);
}
element('userName').value = '甲';
element('subject').value = '成本变化';
element('industry').value = '制造业';
element('focus').value = '成本';
const response = (payload, ok = true) => ({ok, json: async () => payload, text: async () => String(payload)});
const context = vm.createContext({
  FormData, URL, Promise, document: {querySelector: () => ({textContent: ''})},
  localStorage: {setItem() {}},
  $(id) { return element(id); },
  selectedInterviewee: () => '商户',
  setImportStatus(message) { notices.push(message); },
  responseError: async () => '处理失败',
  md2html: value => value,
  showScreen() {},
  prepareReportArchive() {},
  interviewLocation: () => ({}),
  alert(message) { notices.push(message); },
  fetch: async (url) => {
    requests.push(url);
    if (url === 'api/imports/analyze') return response({
      job_id: 'job1', status: 'failed', session_id: 'saved123', error: '纪要生成失败',
    });
    if (url === 'api/sessions/saved123/finish') return response({report: '# 纪要'});
    if (url === 'api/sessions/saved123/report.md') return {...response({}), text: async () => '# 纪要'};
    throw new Error(`意外请求 ${url}`);
  },
});
vm.runInContext(`let selectedImportFile={name:'material.txt',size:10},importBusy=false,importPollToken=0;
  let currentImportJob=null,importRetry=false,failedImportSession=null,sid=null;
  ${reportHelper}${source}`, context);

(async () => {
  await vm.runInContext('importMaterial()', context);
  await vm.runInContext('importMaterial()', context);
  assert.deepEqual(requests, ['api/imports/analyze', 'api/sessions/saved123/finish']);
  assert.equal(element('md').innerHTML, '# 纪要');
  requests.length = 0;
  let reportReads = 0;
  context.fetch = async url => {
    requests.push(url);
    if (url === 'api/imports/analyze') return response({job_id: 'job2', status: 'ready', session_id: 'saved456'});
    if (url === 'api/sessions/saved456/finish') return response({report: '# 纪要'});
    if (url === 'api/sessions/saved456/report.md') {
      reportReads += 1;
      return reportReads === 1 ? response({detail: '临时读取失败'}, false)
        : {...response({}), text: async () => '# 纪要'};
    }
    throw new Error(`意外请求 ${url}`);
  };
  await vm.runInContext('importMaterial()', context);
  await vm.runInContext('importMaterial()', context);
  assert.deepEqual(requests, ['api/imports/analyze', 'api/sessions/saved456/report.md',
                              'api/sessions/saved456/finish']);
  assert.equal(element('md').innerHTML, '# 纪要');
  console.log('PASS: retry report on existing imported session without duplicate upload');
})().catch(error => { console.error(error); process.exitCode = 1; });
