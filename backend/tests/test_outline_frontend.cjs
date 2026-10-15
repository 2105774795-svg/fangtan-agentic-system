const assert=require('node:assert/strict'),vm=require('node:vm');
const {main,section}=require('./frontend_fixture.cjs');
const extract=(start,end)=>section(main,start,end);
function node(){const classes=new Set();return {value:'',disabled:false,textContent:'',children:[],classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle(x,on){if(on)classes.add(x);else classes.delete(x)}},addEventListener(){},focus(){},append(...x){this.children.push(...x)},set innerHTML(x){this.children=[]}}}
const elements=new Map();const $=id=>{if(!elements.has(id))elements.set(id,node());return elements.get(id)};
const storage=new Map(),sent=[];
const settings={subject:'水果档口',interviewee_type:'商户',industry:'食品饮料',focus:'成本',location:{city:'重庆市',market:'双福国际农贸城'}};
for(const [id,value] of Object.entries({subject:settings.subject,industry:settings.industry,focus:settings.focus,regionCity:'重庆市',regionMarket:'双福国际农贸城',userName:'本地使用者'}))$(id).value=value;
let offline=true;
const context=vm.createContext({$,console,Promise,Set,Map,JSON,Date,Math,setTimeout,clearTimeout,
  document:{createElement:node,querySelectorAll:()=>[]},authUser:{id:-1},selectedInterviewee:()=> '商户',
  localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
  confirm:()=>true,alert:message=>{throw Error(message)},responseError:async()=> '接口失败',
  fetch:async(url,options)=>{sent.push({url,options});if(url==='api/outlines/jobs')return {ok:true,json:async()=>({job_id:'job-one',status:'generating'})};
    if(offline)throw Error('页面转入后台时暂时断网');return {ok:true,json:async()=>({job_id:'job-one',status:'ready',settings,background:'以水果批发为调研切入点，经营品类与园区配套情况待现场核实。',background_note:'AI参考，未经实时检索核实。',questions:['这个市场最近的客流变化多少？','这个档口的租金变化多少？']})};}
});
vm.runInContext(`let selectedQuestions=new Set(),lastChoiceQuestions=[],generatedOutline=null,outlineJobId=null,outlinePolling=false,outlineOriginal='',placeBrief=null,recordingPreparation=false,preparingRecording=false;${extract('async function generateOutline(){','function showScreen(')}`,context);
(async()=>{
  await vm.runInContext('generateOutline()',context);
  assert(storage.has('fangtan_interview_draft_-1_job'),'网络中断后必须保留后台任务 ID');
  assert.equal(vm.runInContext('outlineJobId',context),'job-one');
  assert.match($('outlineTip').textContent,/自动恢复/);
  offline=false;await vm.runInContext('pollOutlineTask()',context);
  assert.match($('outline').value,/这个市场/);
  assert.match($('placeBackground').textContent,/水果批发/);
  assert.match($('placeBackgroundNote').textContent,/未经实时检索/);
  assert(!JSON.parse(sent[0].options.body).location.town);
  assert(!Object.hasOwn(JSON.parse(sent[0].options.body).location,'district'));
  assert(!JSON.parse(sent[0].options.body).location.address);
  const savedDraft=storage.get('fangtan_interview_draft_-1');
  assert.match(JSON.parse(savedDraft).placeBrief.text,/水果批发/);
  vm.runInContext("placeBrief=null;renderPlaceBackground();restoreInterviewDraft()",context);
  assert.match($('placeBackground').textContent,/水果批发/,'刷新恢复时保留背景');
  $('regionMarket').value='另一园区';vm.runInContext('renderPlaceBackground()',context);
  assert($('placeBackgroundCard').classList.contains('hidden'),'地点变化不能继续显示旧背景');
  $('regionMarket').value='双福国际农贸城';vm.runInContext('renderPlaceBackground()',context);
  assert(!storage.has('fangtan_interview_draft_-1_job'));
  vm.runInContext("selectedQuestions.delete('这个档口的租金变化多少？')",context);
  assert.equal(vm.runInContext('selectedForRecording().length',context),1);
  assert.equal(JSON.parse(sent[0].options.body).location.market,'双福国际农贸城');
  offline=true;await vm.runInContext('generateOutline()',context);
  $('outline').value='生成期间手动编辑的现场问题';
  offline=false;await vm.runInContext('pollOutlineTask()',context);
  assert.equal($('outline').value,'生成期间手动编辑的现场问题','后台结果不能覆盖手动编辑');
  assert(storage.has('fangtan_interview_draft_-1_job'),'未载入的生成结果仍应保留恢复入口');
  vm.runInContext('applyGeneratedOutline(true)',context);
  assert.match($('outline').value,/这个市场/);
  assert(!storage.has('fangtan_interview_draft_-1_job'),'载入结果后才清理恢复入口');

  let created=0,prepared=0,resumed='';context.navigationRevision=0;
  context.startSession=async()=>{created++};context.resumeSession=async id=>{resumed=id};context.hint=()=>{};
  context.openRecordingPreparation=()=>{prepared++};
  context.fetch=async()=>({ok:true,json:async()=>({items:[{id:'old',status:'done'}]})});
  vm.runInContext(extract('async function resumeRecent(){','function enterRecording('),context);
  await vm.runInContext('resumeRecent()',context);assert.equal(prepared,1,'没有未结束会话时先进入准备页');assert.equal(created,0,'不能提前产生空访谈');
  storage.set('fangtan_recent_sid','existing-live');context.fetch=async()=>({ok:true,json:async()=>({status:'live'})});
  await vm.runInContext('resumeRecent()',context);assert.equal(resumed,'existing-live');
  console.log('PASS: 后台提纲任务恢复、城市与市场两项参数、背景持久化与切换失效、问题筛选与续访');
})().catch(error=>{console.error(error);process.exitCode=1});
