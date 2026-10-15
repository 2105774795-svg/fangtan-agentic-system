const $=id=>document.getElementById(id);
const nativeFetch=window.fetch.bind(window);
window.fetch=async(input,options)=>{
  const response=await nativeFetch(input,options);
  if(authEnabled&&response.status===401&&!String(input).includes('api/auth/')&&!String(input).includes('api/memoir/')){
    authUser=null;
    if(es){es.close();es=null}
    if(recorder)stopRec();
    $('authGate').classList.remove('hidden');
  }
  return response;
};
let sid=null,recorder=null,stream=null,recordingTimer=null,uploading=false,es=null,secs=0,tick=null;
let outlineItems=[],askedSet=new Set();
const pending=[];
const failedUploads=[];
const renderedSeqs=new Set(),renderedSuggestions=new Set();
let stopWait=Promise.resolve(),segmentWait=Promise.resolve(),finishing=false,currentDetail=null,syncingState=false;
let selectedImportFile=null,importBusy=false,importPollToken=0,currentImportJob=null,importRetry=false,failedImportSession=null;
let authUser=null,authEnabled=true,authMode='login',historyTimer=null;
let currentProject=null,projectSessionItems=[],projectSelected=new Set(),projectTotal=0,projectPollToken=0,projectMerging=false,detailReturnProject=null;
let projectViewToken=0,reportArchiveSession=null,reportArchiveReady=false;
let selectedQuestions=new Set(),lastChoiceQuestions=[],generatedOutline=null,outlineJobId=null,outlinePolling=false,outlineOriginal='',wakeLock=null;
let nativeRecording=false,nativeQueueCount=0,recordClockStart=0,recordClockOffset=0,recoveringAudio=false;
let creatingSession=false,recordingPreparation=false,preparingRecording=false,placeBrief=null,leavingLive=false;
let audioOrder=0,inFlightChunkId=null;
let navigationRevision=0;
let coverageTimer=null,coverageSyncing=false;
const importAudioExt=new Set(['mp3','m4a','wav','aac','ogg','flac','wma','webm','mp4']);
const importTextExt=new Set(['txt','md','docx','srt','vtt','csv','json','html','htm']);
$('userName').value=localStorage.getItem('fangtan_user_name')||'';

function setAuthMode(mode){
  authMode=mode;
  $('authTitle').textContent=mode==='login'?'登录访谈·体察':mode==='register'?'员工注册':'找回员工密码';
  $('authSubmit').textContent=mode==='login'?'登录':mode==='register'?'注册':'修改密码';
  $('authIdCard').classList.toggle('hidden',mode==='login');$('authIdCard').required=mode!=='login';
  $('authPhone').classList.toggle('hidden',mode!=='register');$('authPhone').required=mode==='register';
  $('authConfirm').classList.toggle('hidden',mode==='login');$('authConfirm').required=mode!=='login';
  $('authPassword').autocomplete=mode==='login'?'current-password':'new-password';
  $('authMessage').textContent='';
}
function applyAuthUser(user,enabled=true){
  if(!authUser||authUser.id!==user.id){
    currentDetail=null;currentProject=null;projectSelected.clear();projectPollToken++;if(es){es.close();es=null}showScreen('scrSetup');
  }
  authUser=user;authEnabled=enabled!==false;
  $('userName').value=user.name;$('userName').readOnly=true;
  $('userNameLabel').textContent=authEnabled?'当前员工（用于内部使用量统计）':'使用人';
  $('loggedInUser').textContent=`${user.name} · 退出`;
  $('loggedInUser').classList.toggle('hidden',!authEnabled);
  $('historyScope').classList.toggle('hidden',!authEnabled);
  if(!authEnabled){$('historyScope').value='all';$('detailShare').classList.add('hidden')}
  localStorage.setItem('fangtan_user_name',user.name);$('authGate').classList.add('hidden');
  restoreInterviewDraft();restoreOutlineTask();
  restoreNativeSession();
}
async function bootstrapAuth(){
  try{const r=await fetch('api/auth/me');if(r.ok){const data=await r.json();applyAuthUser(data.user,data.auth_enabled);return}}
  catch(e){}$('authGate').classList.remove('hidden');
}
async function submitAuth(ev){
  ev.preventDefault();const btn=$('authSubmit');btn.disabled=true;$('authMessage').textContent='正在验证…';
  const body={name:$('authName').value.trim(),password:$('authPassword').value};
  if(authMode!=='login'){body.id_card=$('authIdCard').value.trim();body.confirmPassword=$('authConfirm').value}
  if(authMode==='register')body.phone=$('authPhone').value.trim();
  try{
    const route=authMode==='login'?'login':authMode==='register'?'register':'reset-password';
    const r=await fetch('api/auth/'+route,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(!r.ok)throw new Error(await responseError(r));const data=await r.json();
    if(authMode==='login'){applyAuthUser(data.user);$('authPassword').value=''}
    else{setAuthMode('login');$('authMessage').textContent=data.message||'操作成功，请登录';$('authPassword').value=''}
  }catch(e){$('authMessage').textContent=e.message}finally{btn.disabled=false}
}
async function logoutEmployee(){
  if(!authEnabled)return;
  if(!confirm('退出当前员工账号？'))return;
  if(es){es.close();es=null}await fetch('api/auth/logout',{method:'POST'});
  authUser=null;localStorage.removeItem('fangtan_recent_sid');$('userName').value='';showScreen('scrSetup');$('authGate').classList.remove('hidden');
}

function pickType(el){document.querySelectorAll('#liveForm .type').forEach(t=>t.classList.remove('sel'));el.classList.add('sel');saveInterviewDraft()}
function selectedInterviewee(){return document.querySelector('#liveForm .type.sel')?.dataset.v||'商户'}
async function responseError(response){
  try{const data=await response.json();return data.detail||JSON.stringify(data)}catch(e){return await response.text()||`HTTP ${response.status}`}
}
async function generateOutline(){
  const subject=$('subject').value.trim();
  if(!subject){$('subject').focus();alert('请先填写访谈主题');return}
  const location=interviewLocation();
  if(!location.city||!location.market){
    $('outlineTip').textContent='请填写城市和具体市场或园区';$('regionCity').focus();return;
  }
  if($('outline').value.trim()&&!confirm('重新生成会覆盖当前提纲，是否继续？'))return;
  const btn=$('outlineBtn');btn.disabled=true;btn.textContent='正在提交…';saveInterviewDraft();outlineOriginal=$('outline').value;
  try{
    const r=await fetch('api/outlines/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      subject,interviewee_type:selectedInterviewee(),industry:$('industry').value,focus:$('focus').value.trim(),location,regenerate:!!outlineOriginal.trim()
    })});
    if(!r.ok)throw new Error(await responseError(r));
    const data=await r.json();outlineJobId=data.job_id;localStorage.setItem(draftKey()+'_job',JSON.stringify({id:outlineJobId,original:outlineOriginal}));
    await pollOutlineTask();
  }catch(e){$('outlineTip').textContent='提交失败：'+e.message;btn.disabled=false;btn.textContent='重试生成提纲'}
}
const draftFields=['subject','industry','focus','outline','regionCity','regionMarket'];
function draftKey(){return 'fangtan_interview_draft_'+(authUser?.id??'local')}
function interviewLocation(){return {city:$('regionCity').value.trim(),market:$('regionMarket').value.trim()}}
function saveInterviewDraft(){try{const data={type:selectedInterviewee(),selected:[...selectedQuestions],placeBrief};draftFields.forEach(id=>data[id]=$(id).value);localStorage.setItem(draftKey(),JSON.stringify(data))}catch(e){}}
function restoreInterviewDraft(){
  placeBrief=null;
  try{const data=JSON.parse(localStorage.getItem(draftKey())||'null');if(data){draftFields.forEach(id=>{if(typeof data[id]==='string')$(id).value=data[id]});document.querySelectorAll('#liveForm .type').forEach(el=>el.classList.toggle('sel',el.dataset.v===data.type));selectedQuestions=new Set(data.selected||[]);lastChoiceQuestions=outlineQuestions();if(data.placeBrief&&typeof data.placeBrief.text==='string')placeBrief=data.placeBrief}}
  catch(e){}renderOutlineChoices();renderPlaceBackground();
}
function renderPlaceBackground(){
  const location=interviewLocation();
  const matches=placeBrief&&Object.keys(location).every(key=>(placeBrief.location?.[key]||'')===location[key]);
  $('placeBackgroundCard').classList.toggle('hidden',!matches||!placeBrief.text);
  $('placeBackground').textContent=matches?placeBrief.text:'';
  const note=$('placeBackgroundNote');note.textContent=matches?(placeBrief.note||'请结合来源日期与实地情况核实。'):'';
  if(matches&&placeBrief.retrievedAt){const stamp=document.createElement('span');stamp.style.display='block';stamp.textContent='检索时间：'+new Date(placeBrief.retrievedAt).toLocaleString('zh-CN');note.append(stamp)}
  if(matches)for(const source of (placeBrief.sources||[])){
    try{const url=new URL(source.url);if(!['https:','http:'].includes(url.protocol)||url.username||url.password)continue;
      const row=document.createElement('span'),a=document.createElement('a');row.style.display='block';a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';a.textContent=`[${source.id}] ${source.title||url.hostname}`;
      row.append(a);if(source.published_at)row.append(document.createTextNode(' · 资料日期：'+source.published_at));note.append(row);
    }catch(e){}
  }
}
function outlineQuestions(){return [...new Set($('outline').value.split('\n').map(q=>q.trim()).filter(Boolean))]}
function renderOutlineChoices(){
  const questions=outlineQuestions(),old=new Set(lastChoiceQuestions),box=$('outlineChoices');box.innerHTML='';
  questions.forEach(q=>{if(!old.has(q))selectedQuestions.add(q);const row=document.createElement('label');row.className='question-option';
    const check=document.createElement('input');check.type='checkbox';check.checked=selectedQuestions.has(q);check.onchange=()=>{check.checked?selectedQuestions.add(q):selectedQuestions.delete(q);saveInterviewDraft();updateRecordingBrief()};
    const text=document.createElement('span');text.textContent=q;row.append(check,text);box.append(row)});
  selectedQuestions=new Set(questions.filter(q=>selectedQuestions.has(q)));lastChoiceQuestions=questions;updateRecordingBrief();
}
function outlineEdited(){renderOutlineChoices();saveInterviewDraft()}
function selectedForRecording(){return outlineQuestions().filter(q=>selectedQuestions.has(q))}
function updateRecordingBrief(){
  const count=selectedForRecording().length,place=Object.values(interviewLocation()).filter(Boolean).join(' · ');
  $('recordingBrief').textContent=`${$('subject').value.trim()||'未命名访谈'}${place?' · '+place:''} · ${count} 道已勾选问题`;
  const prepare=typeof recordingPreparation!=='undefined'&&recordingPreparation;
  $('outlineRecordBtn').textContent=preparingRecording?'正在启动录音…':prepare?(count?`开始录音 · 已选 ${count} 道问题`:'直接开始录音'):`带 ${count} 道筛选问题进入录音`;
  $('outlineRecordBtn').disabled=preparingRecording||(!prepare&&count===0);
}
function applyGeneratedOutline(force=false){
  if(!generatedOutline)return;
  if(force&&$('outline').value.trim()!==outlineOriginal.trim()&&!confirm('载入生成结果将替换当前手动修改的提纲，继续吗？'))return;
  if(force&&generatedOutline.settings){
    const s=generatedOutline.settings;$('subject').value=s.subject;$('industry').value=s.industry;$('focus').value=s.focus;
    const ids={city:'regionCity',market:'regionMarket'};
    Object.entries(ids).forEach(([key,id])=>$(id).value=s.location?.[key]||'');
    document.querySelectorAll('#liveForm .type').forEach(el=>el.classList.toggle('sel',el.dataset.v===s.interviewee_type));
  }
  placeBrief=typeof generatedOutline.background==='string'&&generatedOutline.background.trim()?{text:generatedOutline.background,note:generatedOutline.background_note,sources:generatedOutline.sources||[],researchStatus:generatedOutline.research_status,retrievedAt:generatedOutline.retrieved_at,location:generatedOutline.settings?.location||interviewLocation()}:null;
  renderPlaceBackground();
  $('outline').value=generatedOutline.questions.join('\n');lastChoiceQuestions=[];selectedQuestions.clear();outlineEdited();$('outlineResultBtn').classList.add('hidden');
  localStorage.removeItem(draftKey()+'_job');
}
async function pollOutlineTask(){
  if(outlinePolling||!outlineJobId)return;outlinePolling=true;const jobId=outlineJobId,btn=$('outlineBtn');btn.disabled=true;btn.textContent='后台生成中…';
  try{
    while(jobId===outlineJobId){
      const r=await fetch(`api/outlines/jobs/${jobId}`);if(!r.ok)throw new Error(await responseError(r));const data=await r.json();
      if(data.status==='ready'){
        generatedOutline=data;
        const s=data.settings,same=s&&s.subject===$('subject').value.trim()&&s.industry===$('industry').value&&s.focus===$('focus').value.trim()&&s.interviewee_type===selectedInterviewee()&&Object.keys(interviewLocation()).every(key=>(s.location?.[key]||'')===interviewLocation()[key]);
        const applyNow=$('outline').value===outlineOriginal&&same;
        if(applyNow)applyGeneratedOutline();else $('outlineResultBtn').classList.remove('hidden');
        $('outlineTip').textContent=!applyNow?'新结果已就绪，点击“载入生成结果”查看；当前手动内容已保留':
          data.background?`地点背景与 ${data.questions.length} 道采访问题已生成，可编辑、筛选后进入录音`:'旧版提纲已恢复，重新生成可补充地点背景';
        outlineJobId=null;btn.textContent='重新生成提纲';return;
      }
      if(data.status==='failed')throw new Error(data.error||'生成失败，可重试');
      $('outlineTip').textContent='正在整理地点背景与采访问题，切换页面后也会继续生成';
      await new Promise(resolve=>setTimeout(resolve,1500));
    }
  }catch(e){$('outlineTip').textContent='暂时无法读取结果：'+e.message+'。返回前台后自动恢复，也可重试';btn.textContent='重试生成提纲'}
  finally{outlinePolling=false;btn.disabled=false}
}
function restoreOutlineTask(){try{const task=JSON.parse(localStorage.getItem(draftKey()+'_job')||'null');if(task){outlineJobId=task.id;outlineOriginal=task.original||'';pollOutlineTask()}}catch(e){}}
draftFields.filter(id=>id!=='outline').forEach(id=>$(id).addEventListener('input',()=>{saveInterviewDraft();updateRecordingBrief();renderPlaceBackground()}));
function showScreen(id){
  navigationRevision++;
  if(id!=='scrLive'){clearInterval(coverageTimer);coverageTimer=null}
  if(id!=='scrRecordPrepare'&&recordingPreparation){recordingPreparation=false;$('setupFormHost').appendChild($('liveForm'));updateRecordingBrief()}
  if(id!=='scrProject'){projectPollToken++;projectViewToken++}
  document.querySelectorAll('.screen').forEach(s=>s.classList.toggle('on',s.id===id));
  document.querySelector('meta[name=theme-color]').content='#f6f3ec';
  window.scrollTo(0,0);
}
function openInterviewWorkspace(){
  if(preparingRecording||creatingSession||leavingLive)return;
  showScreen('scrInterview');
}
async function returnToLanding(){
  if(preparingRecording)return false;
  if(creatingSession){alert('正在创建访谈，请完成后再返回首页');return false}
  if($('scrLive').classList.contains('on'))return leaveLive('scrSetup');
  saveInterviewDraft();showScreen('scrSetup');return true;
}
function selectCaptureMode(mode){
  const isProject=mode==='projects',isImport=!isProject;
  $('recordingExtras').open=mode!=='record';
  $('captureImportTab').classList.toggle('sel',isImport);
  $('captureProjectsTab').classList.toggle('sel',isProject);
  $('captureImportPanel').classList.toggle('hidden',!isImport);
  $('captureProjectsPanel').classList.toggle('hidden',!isProject);
  if(isProject)loadRecordingProjects();
}
function openRecordingPreparation(){
  if(finishing){alert('正在生成纪要，请稍候');return}
  if(sid&&(recorder||nativeRecording||uploading||pending.length||failedUploads.length||nativeQueueCount)){
    showLiveWorkspace();showScreen('scrLive');hint('当前访谈仍有录音或待上传内容，请先处理完毕',true);return;
  }
  recordingPreparation=true;$('prepareFormHost').appendChild($('liveForm'));updateRecordingBrief();renderPlaceBackground();showScreen('scrRecordPrepare');
}
async function submitInterviewSetup(){
  if(!recordingPreparation)return startSession(true);
  if(preparingRecording)return;
  preparingRecording=true;$('orb').disabled=true;$('liveFieldTab').disabled=true;$('prepareBackBtn').setAttribute('aria-disabled','true');updateRecordingBrief();
  try{if(await startSession(false))if(!recorder&&!nativeRecording)await toggleRec()}
  finally{preparingRecording=false;$('orb').disabled=false;$('liveFieldTab').disabled=false;$('prepareBackBtn').removeAttribute('aria-disabled');updateRecordingBrief()}
}
function showCaptureHub(mode='record'){
  if(preparingRecording)return;
  $('captureHub').classList.remove('hidden');$('liveWorkspace').classList.add('hidden');$('sugDock').classList.add('hidden');
  selectCaptureMode(mode);showScreen('scrLive');
  updateRecordingBrief();
}
function showLiveWorkspace(){
  $('captureHub').classList.add('hidden');$('liveWorkspace').classList.remove('hidden');$('sugDock').classList.remove('hidden');
}
function importExt(file){return (file.name.split('.').pop()||'').toLowerCase()}
function formatBytes(bytes){return bytes>=1024*1024?(bytes/1024/1024).toFixed(1)+' MB':Math.max(1,Math.round(bytes/1024))+' KB'}
function setImportStatus(text,progress=0,error=false){
  $('importStatus').textContent=text;$('importStatus').style.color=error?'var(--vermilion)':'';
  $('importProgress').style.width=Math.max(0,Math.min(100,progress))+'%';
}
function setImportFile(file){
  if(importBusy)return;
  if(!file){selectedImportFile=null;failedImportSession=null;$('importPicked').classList.add('hidden');$('importBtn').disabled=true;setImportStatus('尚未选择文件');return}
  const ext=importExt(file),audio=importAudioExt.has(ext),text=importTextExt.has(ext);
  if(!audio&&!text){alert('不支持该文件格式，请选择页面列出的音频或文字稿');return}
  const limit=audio?200*1024*1024:20*1024*1024;
  if(file.size>limit){alert(`${audio?'音频':'文字稿'}不能超过 ${audio?200:20}MB`);return}
  selectedImportFile=file;failedImportSession=null;$('importPicked').textContent=`已选择：${file.name} · ${formatBytes(file.size)}`;
  $('importPicked').classList.remove('hidden');$('importBtn').disabled=false;setImportStatus('文件已就绪，可开始分析',0);
}
// 任务在服务器独立运行；这里只提交一次并读取状态，不因网络恢复重复提交。
async function requestReport(sessionId,onProgress){
  let submitted=false;
  const pendingError=message=>{const error=new Error(message);error.reportPending=true;return error};
  const request=async(url,options)=>{
    try{return await fetch(url,options)}
    catch(e){throw pendingError(submitted?'连接中断，纪要任务已提交后台，可稍后在“历史会话”查看':'提交响应中断，请稍后在“历史会话”核对任务状态，避免重复提交')}
  };
  const response=await request(`api/sessions/${sessionId}/finish`,{method:'POST',headers:{'X-Background-Task':'1'}});
  if(!response.ok)throw new Error(await responseError(response));
  submitted=response.status===202;
  const data=await response.json().catch(()=>{throw pendingError(submitted?'纪要任务已提交后台，但响应读取中断，可稍后在“历史会话”查看':'纪要响应读取中断，请稍后在“历史会话”核对状态，避免重复提交')});
  if(response.status!==202&&typeof data.report==='string')return data.report;
  if(response.status!==202)throw new Error('纪要接口返回异常，请稍后在“历史会话”查看');
  onProgress?.({...data,stage:'已提交后台生成，可离开页面，稍后在“历史会话”查看',progress:0});
  const deadline=Date.now()+15*60*1000;
  while(Date.now()<deadline){
    const stateResponse=await request(`api/sessions/${sessionId}/report-job`);
    if(!stateResponse.ok)throw pendingError('暂时无法读取纪要状态：'+await responseError(stateResponse)+'；可稍后在“历史会话”查看');
    const state=await stateResponse.json().catch(()=>{throw pendingError('纪要状态读取中断，任务仍在后台处理，可稍后在“历史会话”查看')});onProgress?.(state);
    if(state.report_status==='failed')throw new Error(state.error||'后台纪要生成失败，请稍后重试');
    if(state.report_status==='ready'){
      const reportResponse=await request(`api/sessions/${sessionId}/report.md`);
      if(!reportResponse.ok)throw pendingError('纪要已经生成，但暂时无法读取；可稍后在“历史会话”查看');
      return reportResponse.text().catch(()=>{throw pendingError('纪要已经生成，但内容读取中断；可稍后在“历史会话”查看')});
    }
    await new Promise(resolve=>setTimeout(resolve,1200));
  }
  throw pendingError('纪要仍在后台处理中，可离开页面，稍后在“历史会话”查看，无需重复提交');
}
async function importMaterial(){
  if(!selectedImportFile||importBusy)return;
  const userName=$('userName').value.trim();
  if(!userName){alert('请先返回“实战访谈”填写使用人姓名');return}
  localStorage.setItem('fangtan_user_name',userName);
  importBusy=true;importRetry=false;currentImportJob=null;const token=++importPollToken;$('importBtn').disabled=true;$('importBtn').textContent=failedImportSession?'正在重试纪要…':'正在提交…';
  try{
    if(failedImportSession){
      const existing=failedImportSession;
      const report=await requestReport(existing,state=>setImportStatus(state.stage||'后台生成纪要，可稍后在“历史会话”查看',state.progress||0));
      if(await showImportedReport(existing,report,token))failedImportSession=null;return;
    }
    const form=new FormData();form.append('material',selectedImportFile);form.append('subject',$('subject').value.trim());
    form.append('user_name',userName);form.append('interviewee_type',selectedInterviewee());form.append('industry',$('industry').value);form.append('focus',$('focus').value.trim());
    form.append('location',JSON.stringify(interviewLocation()));
    setImportStatus('正在安全上传文件…',3);
    const response=await fetch('api/imports/analyze',{method:'POST',body:form});
    if(!response.ok)throw new Error(await responseError(response));
    let job=await response.json();currentImportJob=job.job_id;$('importCancelBtn').classList.remove('hidden');
    while(token===importPollToken){
      setImportStatus(job.stage||'正在处理…',job.progress||0);
      if(job.status==='ready'){
        failedImportSession=job.session_id;
        if(await showImportedReport(job.session_id,null,token))failedImportSession=null;return;
      }
      if(job.status==='failed'){
        failedImportSession=job.session_id||null;
        const retained=job.session_id?'；已提取的内容保留在“历史会话”，可稍后重试生成纪要':'';
        throw new Error((job.error||'处理失败')+retained);
      }
      if(job.status==='cancelled'){setImportStatus('已取消，本次临时文件已清理',0);return}
      await new Promise(resolve=>setTimeout(resolve,1200));
      const poll=await fetch(`api/imports/${job.job_id}`);if(!poll.ok)throw new Error(await responseError(poll));job=await poll.json();
    }
  }catch(e){if(token===importPollToken){importRetry=true;setImportStatus((e.reportPending?'':'处理失败：')+e.message,0,!e.reportPending);alert((e.reportPending?'':'材料分析失败：')+e.message)}
  }finally{if(token===importPollToken){importBusy=false;currentImportJob=null;$('importCancelBtn').classList.add('hidden');$('importCancelBtn').disabled=false;$('importBtn').disabled=!selectedImportFile;$('importBtn').textContent=failedImportSession?'重试生成纪要':importRetry?'重新提交分析':'分析并生成纪要'}}
}
async function showImportedReport(sessionId,report=null,token=importPollToken){
  if(token!==importPollToken)return false;
  if(report===null){
    const reportResponse=await fetch(`api/sessions/${sessionId}/report.md`);
    if(!reportResponse.ok)throw new Error('纪要已经生成，但暂时无法读取');
    report=await reportResponse.text();
  }
  if(token!==importPollToken)return false;
  sid=sessionId;localStorage.setItem('fangtan_recent_sid',sid);
  document.querySelector('#scrReport .kicker').textContent='AI MEETING NOTES';
  document.querySelector('#scrReport h1').textContent='AI 访谈纪要';
  document.querySelector('#scrReport .desc').textContent='由已有材料整理 · 关键结论、待办与原话请结合原始材料核实';
  $('md').innerHTML=md2html(report);$('dlMd').href=`api/sessions/${sid}/report.md`;$('dlMd').style.display='';
  showScreen('scrReport');
  prepareReportArchive(sessionId);
  return true;
}
async function cancelImportMaterial(){
  if(!currentImportJob)return;
  const button=$('importCancelBtn');button.disabled=true;button.textContent='正在取消…';
  try{const r=await fetch(`api/imports/${currentImportJob}/cancel`,{method:'POST'});
    if(!r.ok)throw new Error(await responseError(r));setImportStatus('已请求取消，等待当前步骤结束…',0)
  }catch(e){alert('取消失败：'+e.message);button.disabled=false}finally{button.textContent='取消本次处理'}
}

const importInput=$('importFile'),importDrop=$('importDrop');
importInput.addEventListener('change',()=>setImportFile(importInput.files[0]));
['dragenter','dragover'].forEach(name=>importDrop.addEventListener(name,event=>{event.preventDefault();if(!importBusy)importDrop.classList.add('drag')}));
['dragleave','drop'].forEach(name=>importDrop.addEventListener(name,event=>{event.preventDefault();importDrop.classList.remove('drag')}));
importDrop.addEventListener('drop',event=>setImportFile(event.dataTransfer.files[0]));
function fmtDate(ts){return new Date(ts*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})}
function makeButton(text,cls,handler){const b=document.createElement('button');b.textContent=text;if(cls)b.className=cls;b.onclick=handler;return b}

async function projectRequest(url,options){
  const r=await fetch(url,options);if(!r.ok)throw new Error(await responseError(r));return r.json();
}
function jsonPost(body,method='POST'){return {method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}}
async function loadRecordingProjects(){
  const box=$('recordingProjectsList'),status=$('recordingProjectsStatus');status.textContent='正在读取项目…';box.innerHTML='';
  try{
    const {items}=await projectRequest('api/projects');
    status.textContent=items.length?`${items.length} 个项目文件夹`:'';
    if(!items.length){const empty=document.createElement('p');empty.className='desc';empty.textContent='还没有项目，可先新建，也可在录音结束后归档时创建。';box.append(empty);return}
    for(const item of items){
      const card=document.createElement('article');card.className='session-card project-folder';card.tabIndex=0;
      const title=document.createElement('div');title.className='session-title serif';title.textContent=item.name;
      const meta=document.createElement('div');meta.className='session-meta';meta.textContent=`${item.interviews} 场访谈 · ${item.recordings} 份录音 · ${item.reports} 份纪要${item.access==='shared'?' · 同事共享（仅显示有权查看的访谈）':''}`;
      const open=()=>openRecordingProject(item.id);card.onclick=open;card.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open()}};
      card.append(title,meta);box.append(card);
    }
  }catch(e){status.textContent='读取失败：'+e.message}
}
async function createRecordingProject(event){
  event.preventDefault();const name=$('newProjectName').value.trim();if(!name)return;
  const button=$('createProjectBtn');button.disabled=true;
  try{const p=await projectRequest('api/projects',jsonPost({name}));$('newProjectName').value='';await openRecordingProject(p.id)}
  catch(e){$('recordingProjectsStatus').textContent='创建失败：'+e.message}finally{button.disabled=false}
}
async function loadOwnedProjectNames(){
  try{const {items}=await projectRequest('api/projects');const list=$('ownedProjectNames');list.innerHTML='';
    items.filter(p=>p.access==='owner').forEach(p=>{const o=document.createElement('option');o.value=p.name;list.append(o)})
  }catch(e){}
}
async function prepareReportArchive(sessionId){
  reportArchiveSession=sessionId;reportArchiveReady=false;$('reportArchiveStatus').textContent='正在读取归档项目…';
  $('reportArchiveBtn').disabled=true;$('reportNewProjectBtn').disabled=true;$('reportNewProject').value='';
  try{
    const [{items},session]=await Promise.all([projectRequest('api/projects'),projectRequest(`api/sessions/${sessionId}`)]);
    if(reportArchiveSession!==sessionId)return;
    const select=$('reportProject');select.innerHTML='<option value="">暂不归档</option>';
    for(const p of items.filter(p=>p.access==='owner')){const o=document.createElement('option');o.value=p.id;o.textContent=p.name;select.append(o)}
    select.value=session.project_id||'';reportArchiveReady=true;
    $('reportArchiveStatus').textContent=session.project_name?`已归档到「${session.project_name}」`:'本次访谈已保存，可选择项目归档。';
    $('reportArchiveBtn').disabled=false;$('reportNewProjectBtn').disabled=false;
  }catch(e){if(reportArchiveSession===sessionId)$('reportArchiveStatus').textContent='项目读取失败：'+e.message+'，可到历史详情归档。'}
}
async function saveReportProject(){
  if(!reportArchiveReady||!reportArchiveSession)return;
  const sessionId=reportArchiveSession,button=$('reportArchiveBtn');button.disabled=true;
  try{
    const p=await projectRequest(`api/sessions/${sessionId}/project`,jsonPost({project_id:$('reportProject').value},'PATCH'));
    if(reportArchiveSession===sessionId)$('reportArchiveStatus').textContent=p.project_name?`已保存到「${p.project_name}」`:'已保存为未归档访谈';
  }catch(e){$('reportArchiveStatus').textContent='归档失败：'+e.message}finally{button.disabled=false}
}
async function createReportProject(){
  const name=$('reportNewProject').value.trim();if(!reportArchiveReady||!reportArchiveSession)return;
  if(!name){$('reportArchiveStatus').textContent='请先填写新项目名称';$('reportNewProject').focus();return}
  const sessionId=reportArchiveSession,button=$('reportNewProjectBtn');button.disabled=true;
  try{
    const p=await projectRequest('api/projects',jsonPost({name}));
    await projectRequest(`api/sessions/${sessionId}/project`,jsonPost({project_id:p.id},'PATCH'));
    await prepareReportArchive(sessionId);
  }catch(e){$('reportArchiveStatus').textContent='新建并归档失败：'+e.message}finally{button.disabled=false}
}
function returnFromDetail(){if(detailReturnProject)openRecordingProject(detailReturnProject);else openHistory()}
async function openRecordingProject(projectId){
  projectPollToken++;showScreen('scrProject');const token=++projectViewToken;
  currentProject=null;projectSessionItems=[];projectSelected.clear();projectTotal=0;projectMerging=false;
  $('projectTitle').textContent='正在读取项目…';$('projectMeta').textContent='';$('projectSessions').innerHTML='';
  $('projectRecords').innerHTML='';$('projectRecordView').classList.add('hidden');$('projectLoadMore').classList.add('hidden');
  $('projectMergeStatus').textContent='';$('projectMergeBtn').disabled=true;
  $('projectBundleBtn').disabled=true;$('projectBundleStatus').textContent='';
  try{
    const data=await projectRequest(`api/projects/${projectId}`);if(token!==projectViewToken)return;
    currentProject=data.project;projectSessionItems=data.items;projectTotal=data.total;
    $('projectBundleBtn').disabled=!data.total;
    $('projectTitle').textContent=currentProject.name;$('projectRename').value=currentProject.name;
    $('projectRecordTitle').value=currentProject.name+'·总访谈记录';
    const owner=currentProject.access==='owner';
    ['projectRenameRow','projectMergePanel','projectSelectRow'].forEach(id=>$(id).classList.toggle('hidden',!owner));
    $('projectMeta').textContent=`共 ${data.total} 场访谈${owner?'':' · 同事共享，仅可查看获分享的访谈'}`;
    renderProjectSessions();renderProjectRecords(data.records);
    const active=data.records.find(r=>r.status==='generating');if(active)openProjectRecord(active.id);
  }catch(e){if(token===projectViewToken)$('projectTitle').textContent='项目读取失败：'+e.message}
}
function renderProjectSessions(){
  const box=$('projectSessions');box.innerHTML='';const owner=currentProject?.access==='owner';
  if(!projectSessionItems.length){const empty=document.createElement('div');empty.className='empty';empty.textContent='项目里还没有访谈。录音结束后或在历史详情中选择此项目即可归档。';box.append(empty)}
  for(const item of projectSessionItems){
    const card=document.createElement('article');card.className='session-card merge-choice';
    const check=document.createElement('input');check.type='checkbox';check.id='merge_'+item.id;
    check.checked=projectSelected.has(item.id);check.disabled=!owner||!item.merge_eligible;check.setAttribute('aria-label','选择访谈：'+item.display_subject);
    if(!owner)check.classList.add('hidden');
    check.onchange=()=>{if(check.checked)projectSelected.add(item.id);else projectSelected.delete(item.id);updateMergeSelection()};
    const label=document.createElement('label');label.htmlFor=check.id;
    const title=document.createElement('div');title.className='session-title serif';title.textContent=item.display_subject;
    const meta=document.createElement('div');meta.className='session-meta';
    const state=item.status==='live'?'录音尚未结束':!item.utterance_count?'无逐字稿，无法合并':item.report_status==='ready'?'逐字稿与纪要均已就绪':'有逐字稿，单场纪要尚未生成';
    meta.textContent=`${fmtDate(item.created_at)} · ${item.utterance_count} 段逐字稿 · ${state}`;
    label.append(title,meta);const view=makeButton('查看','',()=>openSessionDetail(item.id,currentProject.id));card.append(check,label,view);box.append(card);
  }
  $('projectLoadMore').classList.toggle('hidden',projectSessionItems.length>=projectTotal);updateMergeSelection();
}
function updateMergeSelection(){
  const count=projectSelected.size;const button=$('projectMergeBtn');button.textContent=`合并选中访谈（${count}）`;
  button.disabled=projectMerging||count<2||count>50||currentProject?.access!=='owner';
  const eligible=projectSessionItems.filter(s=>s.merge_eligible);const selected=eligible.filter(s=>projectSelected.has(s.id)).length;
  $('projectSelectAll').checked=eligible.length>0&&selected===eligible.length;$('projectSelectAll').indeterminate=selected>0&&selected<eligible.length;
  if(count>50)$('projectMergeStatus').textContent='单次最多合并 50 场，请减少勾选数量。';
}
function selectAllProjectSessions(checked){
  for(const item of projectSessionItems.filter(s=>s.merge_eligible)){if(checked)projectSelected.add(item.id);else projectSelected.delete(item.id)}
  renderProjectSessions();
}
async function loadMoreProjectSessions(){
  if(!currentProject)return;const projectId=currentProject.id,button=$('projectLoadMore');button.disabled=true;
  try{const data=await projectRequest(`api/projects/${projectId}?offset=${projectSessionItems.length}`);
    if(currentProject?.id!==projectId)return;projectSessionItems.push(...data.items);projectTotal=data.total;renderProjectSessions();
  }catch(e){$('projectMergeStatus').textContent='加载失败：'+e.message}finally{button.disabled=false}
}
function downloadProjectBundle(){
  if(!currentProject||!projectTotal)return;
  const a=document.createElement('a');a.href=`api/projects/${encodeURIComponent(currentProject.id)}/bundle.zip`;
  a.download='项目资料.zip';a.target='_blank';a.rel='noopener noreferrer';a.click();
  $('projectBundleStatus').textContent='正在打包，浏览器将下载 ZIP；大项目请稍候，错误会在下载页说明。';
}
async function renameRecordingProject(){
  if(!currentProject)return;const name=$('projectRename').value.trim(),projectId=currentProject.id,button=$('projectRenameBtn');button.disabled=true;
  try{await projectRequest(`api/projects/${projectId}`,jsonPost({name},'PATCH'));await openRecordingProject(projectId)}
  catch(e){$('projectMergeStatus').textContent='改名失败：'+e.message}finally{button.disabled=false}
}
function renderProjectRecords(records){
  const box=$('projectRecords');box.innerHTML='';
  if(!records.length){const empty=document.createElement('p');empty.className='desc';empty.textContent=currentProject?.access==='owner'?'尚未生成总访谈记录':'总访谈记录由项目创建人管理。';box.append(empty);return}
  for(const r of records){const card=document.createElement('article');card.className='session-card';card.tabIndex=0;
    const title=document.createElement('div');title.className='session-title serif';title.textContent=r.title;
    const meta=document.createElement('div');meta.className='session-meta';meta.textContent=`${r.sources.length} 场访谈 · ${r.status==='ready'?'已生成':r.status==='failed'?'生成失败，可重新选择生成':r.stage||'生成中'} · ${fmtDate(r.created_at)}`;
    const open=()=>openProjectRecord(r.id);card.onclick=open;card.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open()}};card.append(title,meta);box.append(card);
  }
}
async function refreshProjectRecords(){
  if(!currentProject)return;const projectId=currentProject.id;
  const data=await projectRequest(`api/projects/${projectId}?limit=1`);if(currentProject?.id===projectId)renderProjectRecords(data.records);
}
async function generateProjectRecord(){
  if(!currentProject||projectMerging||projectSelected.size<2||projectSelected.size>50)return;
  const projectId=currentProject.id;projectMerging=true;updateMergeSelection();$('projectMergeStatus').textContent='正在提交所选访谈…';
  try{
    const data=await projectRequest(`api/projects/${projectId}/records`,jsonPost({title:$('projectRecordTitle').value.trim(),session_ids:[...projectSelected]}));
    if(currentProject?.id!==projectId)return;$('projectMergeStatus').textContent='已提交。可离开页面，生成完成后在本项目查看。';
    await refreshProjectRecords();await openProjectRecord(data.record_id);
  }catch(e){if(currentProject?.id===projectId)$('projectMergeStatus').textContent='合并失败：'+e.message}
  finally{if(currentProject?.id===projectId){projectMerging=false;updateMergeSelection()}}
}
async function openProjectRecord(recordId){
  const token=++projectPollToken,projectId=currentProject?.id;
  $('projectRecordView').classList.remove('hidden');$('projectRecordBody').innerHTML='';$('projectRecordExports').innerHTML='';$('projectRecordSources').innerHTML='';
  $('projectRecordStatus').textContent='正在读取总访谈记录…';
  try{
    while(token===projectPollToken&&currentProject?.id===projectId){
      const record=await projectRequest(`api/project-records/${recordId}`);if(token!==projectPollToken)return;
      $('projectRecordViewTitle').textContent=record.title;
      $('projectRecordStatus').textContent=record.status==='ready'?'已综合全部所选逐字稿与已有纪要，请按来源核对。':record.error||record.stage;
      if(record.status!=='generating'){
        const sources=$('projectRecordSources');sources.innerHTML='';
        for(const source of record.sources)sources.append(makeButton(`${source.reference} · ${source.subject}`,'',()=>openSessionDetail(source.id,projectId)));
        if(record.status==='ready'){
          $('projectRecordBody').innerHTML=md2html(record.markdown);
          for(const [format,label] of [['docx','导出 Word'],['md','导出 Markdown']]){
            const a=document.createElement('a');a.className='backlink';a.textContent=label;a.href=`api/project-records/${recordId}/export?format=${format}`;$('projectRecordExports').append(a);
          }
        }else{
          $('projectRecordExports').append(makeButton('重新选择来源访谈','',()=>{
            projectSelected=new Set(record.sources.filter(s=>projectSessionItems.some(item=>item.id===s.id&&item.merge_eligible)).map(s=>s.id));
            $('projectRecordTitle').value=record.title;renderProjectSessions();$('projectMergePanel').scrollIntoView({behavior:'smooth'});
          }));
        }
        await refreshProjectRecords();return;
      }
      await new Promise(resolve=>setTimeout(resolve,1800));
    }
  }catch(e){if(token===projectPollToken)$('projectRecordStatus').textContent='读取失败：'+e.message+'，可重新进入项目查看。'}
}

function scheduleHistorySearch(){clearTimeout(historyTimer);historyTimer=setTimeout(openHistory,280)}
async function loadHistoryProjects(){
  const r=await fetch('api/projects');if(!r.ok)return;
  const chosen=$('historyProject').value,select=$('historyProject');
  select.innerHTML='<option value="">全部项目</option>';
  for(const name of (await r.json()).projects){const o=document.createElement('option');o.value=name;o.textContent=name;select.appendChild(o)}
  select.value=chosen;
}
async function toggleProjectOverview(){
  const box=$('historyProjects');box.classList.toggle('hidden');
  if(!box.classList.contains('hidden'))await loadProjectOverview();
}
async function loadProjectOverview(){
  const box=$('historyProjects');box.textContent='正在读取项目…';
  const r=await fetch('api/projects/overview');
  if(!r.ok){box.textContent='项目读取失败：'+await responseError(r);return}
  const projects=(await r.json()).projects;box.innerHTML='';
  if(!projects.length){box.innerHTML='<div class="empty">暂无已归类项目，可在访谈详情中填写项目名称</div>';return}
  projects.forEach(item=>{const card=document.createElement('article');card.className='session-card';card.tabIndex=0;
    const title=document.createElement('div');title.className='session-title serif';title.textContent=item.project_name;
    const ownership=authEnabled?` · 我的 ${item.mine} 场 · 同事共享 ${item.shared} 场`:'';
    const meta=document.createElement('div');meta.className='session-meta';meta.textContent=`${item.interviews} 场访谈${ownership} · ${item.recordings} 份录音 · ${item.reports} 份纪要 · ${item.open_todos} 条未完成待办`;
    const select=()=>openRecordingProject(item.id);
    card.onclick=select;card.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();select()}};
    card.append(title,meta);box.appendChild(card)});
}

async function openHistory(){
  if(es){es.close();es=null}
  showScreen('scrHistory');
  const box=$('historyList');box.textContent='正在读取会话…';
  try{
    await loadHistoryProjects();
    if(!$('historyProjects').classList.contains('hidden'))await loadProjectOverview();
    const query=new URLSearchParams({search:$('historySearch').value.trim(),project:$('historyProject').value,scope:authEnabled?$('historyScope').value:'all'});
    const r=await fetch('api/sessions/?'+query.toString());
    if(!r.ok)throw new Error(await r.text());
    const page=await r.json();box.innerHTML='';$('historyCount').textContent=`当前筛选共 ${page.total} 场访谈`;
    const overview=await fetch('api/management/overview');
    if(overview.ok){const s=(await overview.json()).stats;
      $('historyOverview').textContent=`${authEnabled?'我的累计':'累计'}：${s.interviews} 场访谈 · ${s.recordings} 份录音 · ${s.reports} 份纪要 · 约 ${s.duration_minutes} 分钟`}
    if(!page.items.length){const e=document.createElement('div');e.className='empty';e.textContent='还没有访谈记录，开工一场吧';box.appendChild(e);return}
    page.items.forEach(item=>{
      const card=document.createElement('article');card.className='session-card';card.onclick=()=>openSessionDetail(item.id);
      const title=document.createElement('div');title.className='session-title serif';title.textContent=item.display_subject;
      const meta=document.createElement('div');meta.className='session-meta';
      const state=document.createElement('span');state.className='badge';state.textContent=item.status==='live'?'进行中':'已结束';
      const report=item.report_status==='ready'?'纪要已生成':item.report_status==='generating'?'纪要后台生成中':item.report_status==='failed'?'纪要生成失败':'暂无纪要';
      const industry=item.industry&&item.industry!=='综合/不限'?` · ${item.industry}`:'';
      const project=item.project_name?` · 项目：${item.project_name}`:'';
      const team=item.access==='shared'?` · ${item.user_name} 分享`:'';
      meta.append(state,document.createTextNode(`${item.interviewee_type}${industry}${project}${team} · ${item.utterance_count} 条转写 · ${report} · ${fmtDate(item.created_at)}`));
      card.append(title,meta);box.appendChild(card);
    });
  }catch(e){box.innerHTML='';const d=document.createElement('div');d.className='empty';d.textContent='读取失败：'+e.message;box.appendChild(d)}
}

async function openTodos(){
  showScreen('scrTodos');const box=$('allTodos');box.textContent='正在读取待办…';
  const r=await fetch('api/management/overview');
  if(!r.ok){box.textContent='待办读取失败：'+await responseError(r);return}
  const todos=(await r.json()).todos;box.innerHTML='';
  if(!todos.length){box.innerHTML='<div class="empty">暂无待办</div>';return}
  todos.forEach(item=>{
    const card=document.createElement('article');card.className='session-card';
    const title=document.createElement('div');title.className='session-title serif';title.textContent=item.subject;
    const row=document.createElement('label');row.className='todo-item'+(item.done?' done':'');
    const check=document.createElement('input');check.type='checkbox';check.checked=!!item.done;check.disabled=item.access!=='owner';
    check.onchange=async()=>{const x=await fetch(`api/sessions/${item.session_id}/todos/${item.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({done:check.checked})});
      if(!x.ok){alert(await responseError(x));check.checked=!check.checked;return}openTodos()};
    const text=document.createElement('span');text.textContent=item.text;row.append(check,text);
    const meta=document.createElement('div');meta.className='session-meta';
    const link=document.createElement('a');link.className='backlink';link.href='javascript:void(0)';link.textContent=`查看访谈${item.project_name?' · '+item.project_name:''}${item.access==='shared'?' · 团队共享':''}`;
    link.onclick=()=>openSessionDetail(item.session_id);meta.appendChild(link);card.append(title,row,meta);box.appendChild(card);
  });
}

async function openSessionDetail(sessionId,fromProject=null){
  detailReturnProject=fromProject;$('detailBack').textContent=fromProject?'← 返回项目':'← 返回历史';
  showScreen('scrDetail');$('detailBody').textContent='正在读取详情…';$('detailActions').innerHTML='';
  try{
    const r=await fetch(`api/sessions/${sessionId}`);if(!r.ok)throw new Error(await r.text());
    currentDetail=await r.json();
    $('detailShare').classList.add('hidden');
    $('detailTitle').value=currentDetail.subject;
    const owner=currentDetail.access==='owner';$('detailTitle').readOnly=!owner;
    $('detailTitle').nextElementSibling.classList.toggle('hidden',!owner);
    $('detailProject').value=currentDetail.project_name||'';
    loadOwnedProjectNames();
    $('detailProject').readOnly=!owner;$('detailProjectRow').querySelector('button').classList.toggle('hidden',!owner);
    const industry=currentDetail.industry&&currentDetail.industry!=='综合/不限'?` · ${currentDetail.industry}`:'';
    $('detailMeta').textContent=`${currentDetail.interviewee_type}${industry} · ${currentDetail.status==='live'?'进行中':'已结束'} · ${currentDetail.utterance_count} 条转写 · ${fmtDate(currentDetail.created_at)}${owner?'':` · 由 ${currentDetail.user_name} 分享`}`;
    $('detailFocus').textContent=currentDetail.focus?`采访重点：${currentDetail.focus}`:'';
    renderDetailActions();await renderDetailBody();
  }catch(e){$('detailBody').textContent='读取失败：'+e.message}
}
function renderDetailActions(){
  const box=$('detailActions');box.innerHTML='';if(!currentDetail)return;
  const owner=currentDetail.access==='owner';
  if(owner&&currentDetail.status==='live')box.appendChild(makeButton('继续访谈','primary',()=>resumeSession(currentDetail.id)));
  if(owner&&currentDetail.status!=='live'&&currentDetail.report_status!=='ready')box.appendChild(makeButton(currentDetail.report_status==='generating'?'查看生成进度':'生成纪要','primary',generateDetailReport));
  if(currentDetail.report_status==='ready'){
    box.appendChild(makeButton('查看纪要','primary',()=>renderDetailBody('report')));
    if(owner)box.appendChild(makeButton('修订纪要','',()=>renderDetailBody('edit')));
    const a=document.createElement('a');a.href=`api/sessions/${currentDetail.id}/report.md`;a.download='访谈纪要素材包.md';a.textContent='下载纪要';a.className='backlink';a.style.margin='9px 4px 0';box.appendChild(a);
    const word=document.createElement('a');word.href=`api/sessions/${currentDetail.id}/minutes.docx`;word.textContent='下载 Word（含逐字稿）';word.className='backlink';word.style.margin='9px 4px 0';box.appendChild(word);
  }
  box.appendChild(makeButton('逐字稿 / 回听','',()=>renderDetailBody('transcript')));
  box.appendChild(makeButton('问透评估','',()=>renderDetailBody('coverage')));
  if(currentDetail.has_audio&&currentDetail.status==='done')box.appendChild(makeButton('区分发言人','',()=>renderDetailBody('speakers')));
  if(owner&&currentDetail.has_audio&&currentDetail.status==='done')box.appendChild(makeButton('删除原始录音','danger',deleteCurrentRecording));
  box.appendChild(makeButton('待办事项','',()=>renderDetailBody('todos')));
  box.appendChild(makeButton('问逐字稿','',()=>renderDetailBody('ask')));
  if(owner&&authEnabled)box.appendChild(makeButton('团队共享','',toggleSharePanel));
  if(owner)box.appendChild(makeButton('删除会话','danger',deleteCurrentSession));
}
async function renderDetailBody(mode='auto'){
  const box=$('detailBody');box.innerHTML='';box.dataset.mode=mode;
  if(mode==='coverage'){await renderDetailCoverage(box);return}
  if(mode==='todos'){renderTodos(box);return}
  if(mode==='ask'){renderQuestions(box);return}
  if(mode==='speakers'){await renderSpeakerPanel(box);return}
  if(mode==='edit'){await renderReportEditor(box);return}
  if(mode!=='transcript'&&currentDetail.report_status==='ready'){
    const r=await fetch(`api/sessions/${currentDetail.id}/report.md`);
    if(r.ok){box.innerHTML=md2html(await r.text());return}
  }
  if(currentDetail.has_audio){
    const player=document.createElement('div');player.className='detail-panel';
    const title=document.createElement('h3');title.textContent='录音回听';
    const audio=document.createElement('audio');audio.controls=true;audio.preload='metadata';audio.src=`api/sessions/${currentDetail.id}/recording`;
    const link=document.createElement('a');link.href=audio.src+'?download=1';link.textContent='下载原始录音';link.className='backlink';
    player.append(title,audio,link);box.appendChild(player);
  }
  const download=document.createElement('a');download.href=`api/sessions/${currentDetail.id}/transcript.txt`;download.textContent='下载逐字稿 TXT';download.className='backlink';box.appendChild(download);
  if(!currentDetail.utterances.length){const e=document.createElement('div');e.className='empty';e.textContent='暂无转写内容';box.appendChild(e);return}
  const search=document.createElement('input');search.type='search';search.placeholder='在本场逐字稿中筛选内容';
  search.style.cssText='width:100%;border:1.5px solid #c9c2b2;border-radius:10px;background:#fbf9f4;padding:10px 12px;margin:10px 0;font:inherit';
  box.appendChild(search);
  const list=document.createElement('div');list.className='detail-transcript';
  currentDetail.utterances.forEach(u=>{
    const row=document.createElement('div');row.className='detail-utt';
    const t=document.createElement('time');t.textContent=fmtMs(u.start_ms);t.title='点击跳转到对应录音位置';
    t.onclick=()=>{const player=box.querySelector('audio');if(player){player.currentTime=Math.max(0,u.start_ms/1000);player.play().catch(()=>{})}};
    row.appendChild(t);
    if(u.speaker){const speaker=document.createElement('span');speaker.className='speaker';speaker.textContent=u.speaker;row.appendChild(speaker)}
    const x=document.createElement('span');x.textContent=u.speaker?u.text.replace(new RegExp('^'+u.speaker.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'[：:]\\s*'), ''):u.text;row.appendChild(x);
    if(currentDetail.access==='owner'){
      const edit=document.createElement('button');edit.textContent='标记发言人';edit.onclick=()=>renameSpeaker(u);row.appendChild(edit);
      if(currentDetail.can_delete_segments){const remove=document.createElement('button');remove.textContent='删除这段录音';remove.className='danger';remove.onclick=()=>deleteRecordingSegment(u);row.appendChild(remove)}
    }
    list.appendChild(row)
  });
  search.oninput=()=>{const q=search.value.trim().toLowerCase();
    [...list.children].forEach((row,index)=>row.classList.toggle('hidden',!!q&&!currentDetail.utterances[index].text.toLowerCase().includes(q)&&!(currentDetail.utterances[index].speaker||'').toLowerCase().includes(q)))};
  box.appendChild(list);
}
async function renderDetailCoverage(box){
  const sessionId=currentDetail?.id;if(!sessionId)return;
  const valid=()=>currentDetail?.id===sessionId&&box.dataset.mode==='coverage'&&$('scrDetail').classList.contains('on');
  const r=await fetch(`api/sessions/${sessionId}/coverage`);if(!valid())return;
  if(!r.ok){box.textContent='读取失败：'+await responseError(r);return}
  const cov=await r.json();box.textContent='';
  const summary=document.createElement('p');summary.textContent=`AI 判断已问透 ${cov.covered.length}/${cov.dims.length} 项；根据逐字稿证据辅助判断，不代表独立事实核验。`;box.append(summary);
  if(currentDetail.access==='owner'){
    const button=makeButton('根据逐字稿分析 / 重评','',async()=>{button.disabled=true;button.textContent='正在提交…';try{const result=await fetch(`api/sessions/${sessionId}/coverage/analyze`,{method:'POST'});if(!result.ok)throw new Error(await responseError(result));if(valid())await renderDetailCoverage(box)}catch(e){button.textContent='提交失败：'+e.message;button.disabled=false}});box.append(button);
  }
  for(const d of cov.dims){const panel=document.createElement('div');panel.className='detail-panel';
    const title=document.createElement('h3');title.textContent=d.name+' · '+(d.status_label||'待分析');
    const reason=document.createElement('p');reason.textContent=d.reason;panel.append(title,reason);
    for(const e of d.evidence||[]){const line=document.createElement('p');line.textContent=`[${fmtMs(e.start_ms||0)} · 段落${Number(e.seq)+1}] “${e.quote}”`;panel.append(line)}
    if(d.follow_up){const next=document.createElement('p');next.textContent='建议追问：'+d.follow_up;panel.append(next)}box.append(panel);
  }
  if(cov.analysis_status==='generating'){summary.textContent+=' 正在后台分析…';setTimeout(()=>{if(valid())renderDetailCoverage(box)},2500)}
  else if(cov.analysis_status==='failed')summary.textContent+=' 分析暂时失败，可以重试。';
  else if(cov.analysis_status==='empty')summary.textContent+=' 目前没有可分析的逐字稿。';
  else if(cov.analysis_status==='not_analyzed')summary.textContent+=' 尚未分析；旧资料仅在你点击后交给 AI。';
}
async function renderSpeakerPanel(box){
  const sessionId=currentDetail.id;
  const r=await fetch(`api/sessions/${sessionId}/speakers`);
  if(!r.ok){box.textContent='读取发言人分析失败：'+await responseError(r);return}
  const data=await r.json();if(!currentDetail||currentDetail.id!==sessionId||box!==$('detailBody'))return;
  const panel=document.createElement('div');panel.className='detail-panel';
  const title=document.createElement('h3');title.textContent='本次录音的发言人分离稿';
  const intro=document.createElement('p');intro.textContent='自动标记“发言人 A/B…”，不识别真实身份；原逐字稿和纪要不会被覆盖，也不建立跨访谈声纹库。结果可能有误，请回听核对。';
  panel.append(title,intro);
  if(data.status==='processing'){
    const waiting=document.createElement('p');waiting.textContent='正在区分发言人，可离开此页，稍后回来查看。';panel.appendChild(waiting);
    setTimeout(()=>{if(currentDetail&&currentDetail.id===sessionId&&$('detailBody').contains(panel))renderDetailBody('speakers')},2500);
  }
  if(data.status==='failed'){const error=document.createElement('p');error.textContent='分析失败：'+(data.error||'请稍后重试');panel.appendChild(error)}
  if(data.stale){const notice=document.createElement('p');notice.textContent='以下是上一次成功分析的结果，并非本轮处理结果；请重新核对后使用。';notice.style.color='var(--vermilion)';panel.appendChild(notice)}
  if(data.segments.length){
    const audio=document.createElement('audio');audio.controls=true;audio.preload='metadata';audio.src=`api/sessions/${sessionId}/recording`;panel.appendChild(audio);
    const download=document.createElement('a');download.className='backlink';download.href=`api/sessions/${sessionId}/speakers.txt`;download.textContent='下载发言人分离稿 TXT';panel.appendChild(download);
    data.segments.forEach(segment=>{const row=document.createElement('div');row.className='detail-utt';
      const time=document.createElement('time');time.textContent=fmtMs(segment.start_ms);time.onclick=()=>{audio.currentTime=segment.start_ms/1000;audio.play().catch(()=>{})};
      const label=document.createElement('span');label.className='speaker';label.textContent='发言人 '+(segment.speaker_id<26?String.fromCharCode(65+segment.speaker_id):segment.speaker_id+1);
      const text=document.createElement('span');text.textContent=segment.text;row.append(time,label,text);panel.appendChild(row)});
  }
  if(currentDetail.access==='owner'){
    const actions=document.createElement('div');actions.className='detail-actions';
    if(data.available===false){
      const unavailable=document.createElement('p');
      unavailable.textContent=data.unavailable_reason||'自动区分发言人暂不可用，请联系管理员检查配置。';
      panel.appendChild(unavailable);
    }
    if(data.status!=='processing'&&data.available!==false){
      const consent=document.createElement('label');consent.className='todo-item';
      const checkbox=document.createElement('input');checkbox.type='checkbox';
      const note=document.createElement('span');note.textContent='我已取得本次录音中所有发言人对云端发言人分离的单独同意。';consent.append(checkbox,note);panel.appendChild(consent);
      const start=makeButton(data.segments.length?'重新分析':'开始区分','',async()=>{
        if(!checkbox.checked){alert('请先确认已取得所有发言人的单独同意');return}
        start.disabled=true;const response=await fetch(`api/sessions/${sessionId}/speakers/analyze`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({consent:true})});
        if(!response.ok){alert('无法开始：'+await responseError(response));start.disabled=false;return}renderDetailBody('speakers')});actions.appendChild(start);
    }
    if(data.consented_at||data.segments.length){const clear=makeButton('删除分离结果与同意记录','danger',async()=>{
      if(!confirm('将清除本系统保存的发言人分离结果与同意记录。已提交云端的处理请求可能无法撤回。是否继续？'))return;
      const response=await fetch(`api/sessions/${sessionId}/speakers`,{method:'DELETE'});
      if(!response.ok){alert(await responseError(response));return}renderDetailBody('speakers')});actions.appendChild(clear)}
    panel.appendChild(actions);
  }
  box.appendChild(panel);
}
async function renderReportEditor(box){
  const r=await fetch(`api/sessions/${currentDetail.id}/report.md`);
  if(!r.ok){box.textContent='纪要读取失败';return}
  const hint=document.createElement('p');hint.textContent='人工修订将覆盖当前纪要；已生成的待办不会自动重写。';
  const editor=document.createElement('textarea');editor.className='report-editor';editor.value=await r.text();
  const actions=document.createElement('div');actions.className='detail-actions';
  const save=makeButton('保存修订','primary',async()=>{
    if(!editor.value.trim())return;save.disabled=true;
    const response=await fetch(`api/sessions/${currentDetail.id}/report`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({markdown:editor.value})});
    if(!response.ok){alert('保存失败：'+await responseError(response));save.disabled=false;return}renderDetailBody('report');
  });
  actions.append(save,makeButton('取消','',()=>renderDetailBody('report')));box.append(hint,editor,actions);
}
async function saveDetailProject(){
  if(!currentDetail||currentDetail.access!=='owner')return;
  const project_name=$('detailProject').value.trim();
  const r=await fetch(`api/sessions/${currentDetail.id}/project`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_name})});
  if(!r.ok){alert('项目保存失败：'+await responseError(r));return}
  currentDetail.project_name=project_name;await loadHistoryProjects();
}
async function renameSpeaker(u){
  const speaker=prompt('给这段发言标记姓名或角色（留空为取消标记）',u.speaker||'');if(speaker===null)return;
  const r=await fetch(`api/sessions/${currentDetail.id}/utterances/${u.seq}/speaker`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({speaker:speaker.trim()})});
  if(!r.ok){alert('标记失败：'+await responseError(r));return}u.speaker=speaker.trim();renderDetailBody('transcript');
}
function renderTodos(box){
  const panel=document.createElement('div');panel.className='detail-panel';
  const heading=document.createElement('h3');heading.textContent='行动项与待办';
  const hint=document.createElement('p');hint.textContent='从纪要中提取的待办可在这里勾选；负责人和时间请以原文为准。';panel.append(heading,hint);
  const todos=currentDetail.todos||[];
  if(!todos.length){const empty=document.createElement('p');empty.textContent='暂无待办，可手动补充。';panel.appendChild(empty)}
  todos.forEach(item=>{
    const row=document.createElement('label');row.className='todo-item'+(item.done?' done':'');
    const check=document.createElement('input');check.type='checkbox';check.checked=!!item.done;check.disabled=currentDetail.access!=='owner';
    check.onchange=()=>updateTodo(item.id,check.checked);
    const text=document.createElement('span');text.textContent=item.text;row.append(check,text);
    if(currentDetail.access==='owner'){const del=document.createElement('button');del.type='button';del.textContent='删除';del.onclick=e=>{e.preventDefault();removeTodo(item.id)};row.appendChild(del)}
    panel.appendChild(row);
  });
  if(currentDetail.access==='owner'){
    const compose=document.createElement('div');compose.className='detail-compose';
    const input=document.createElement('input');input.placeholder='补充一条待办';input.maxLength=500;
    const add=document.createElement('button');add.textContent='添加';add.onclick=()=>createTodo(input.value);compose.append(input,add);panel.appendChild(compose);
  }
  box.appendChild(panel);
}
async function createTodo(text){
  if(!text.trim())return;
  const r=await fetch(`api/sessions/${currentDetail.id}/todos`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:text.trim()})});
  if(!r.ok){alert(await responseError(r));return}currentDetail.todos=(await r.json()).todos;renderDetailBody('todos');
}
async function updateTodo(id,done){
  const r=await fetch(`api/sessions/${currentDetail.id}/todos/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({done})});
  if(!r.ok){alert(await responseError(r));return}currentDetail.todos=(await r.json()).todos;renderDetailBody('todos');
}
async function removeTodo(id){
  if(!confirm('删除这条待办？'))return;
  const r=await fetch(`api/sessions/${currentDetail.id}/todos/${id}`,{method:'DELETE'});
  if(!r.ok){alert(await responseError(r));return}currentDetail.todos=(await r.json()).todos;renderDetailBody('todos');
}
function renderQuestions(box){
  const panel=document.createElement('div');panel.className='detail-panel';
  const heading=document.createElement('h3');heading.textContent='问逐字稿';
  const hint=document.createElement('p');hint.textContent='只按已记录的原文回答，并附对应时间点；原文没有的内容会明确说明。';panel.append(heading,hint);
  (currentDetail.questions||[]).forEach(item=>{
    const q=document.createElement('div');q.className='detail-panel';
    const title=document.createElement('strong');title.textContent='问：'+item.question;
    const answer=document.createElement('div');answer.textContent=item.answer;answer.style.whiteSpace='pre-wrap';q.append(title,answer);panel.appendChild(q);
  });
  const compose=document.createElement('div');compose.className='detail-compose';
  const input=document.createElement('textarea');input.placeholder='例如：对方提到的成本变化有哪些？';input.maxLength=500;
  const send=document.createElement('button');send.textContent='提问';send.onclick=async()=>{
    const question=input.value.trim();if(!question)return;send.disabled=true;send.textContent='查找中…';
    try{const r=await fetch(`api/sessions/${currentDetail.id}/ask`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question})});
      if(!r.ok)throw new Error(await responseError(r));const data=await r.json();
      currentDetail.questions=currentDetail.access==='owner'?data.questions:[...(currentDetail.questions||[]),{question,answer:data.answer}];renderDetailBody('ask');
    }catch(e){alert('问答失败：'+e.message);send.disabled=false;send.textContent='提问'}
  };compose.append(input,send);panel.appendChild(compose);box.appendChild(panel);
}
async function toggleSharePanel(){
  if(!authEnabled)return;
  const box=$('detailShare');if(!box.classList.contains('hidden')){box.classList.add('hidden');return}
  box.classList.remove('hidden');await renderShares();
}
async function renderShares(){
  if(!authEnabled)return;
  const box=$('detailShare');box.innerHTML='';const heading=document.createElement('h3');heading.textContent='团队共享';
  const intro=document.createElement('p');intro.textContent='只有被添加的员工能查看这场访谈、逐字稿和录音；修改与删除仍由创建人负责。';box.append(heading,intro);
  const r=await fetch(`api/sessions/${currentDetail.id}/shares`);
  if(!r.ok){box.append(document.createTextNode('共享名单读取失败'));return}
  (await r.json()).shares.forEach(person=>{
    const row=document.createElement('div');row.className='share-person';const name=document.createElement('span');name.textContent=person.user_name;
    const del=document.createElement('button');del.textContent='移除';del.onclick=async()=>{
      const x=await fetch(`api/sessions/${currentDetail.id}/shares/${person.user_id}`,{method:'DELETE'});
      if(!x.ok){alert(await responseError(x));return}renderShares()};row.append(name,del);box.appendChild(row);
  });
  const compose=document.createElement('div');compose.className='detail-compose';
  const search=document.createElement('input');search.placeholder='搜索员工姓名';search.maxLength=50;
  const find=document.createElement('button');find.textContent='查找';
  const results=document.createElement('div');
  find.onclick=async()=>{
    results.textContent='正在查找…';const x=await fetch('api/auth/people?search='+encodeURIComponent(search.value.trim()));
    if(!x.ok){results.textContent=await responseError(x);return}results.innerHTML='';
    const people=(await x.json()).people.filter(p=>!p.expired&&p.id!==authUser.id);
    if(!people.length){results.textContent='没有找到可共享的员工';return}
    people.forEach(person=>{const row=document.createElement('div');row.className='share-result';
      const label=document.createElement('span');label.textContent=`${person.name} · ${person.type}`;
      const add=document.createElement('button');add.textContent='共享';add.onclick=async()=>{
        const y=await fetch(`api/sessions/${currentDetail.id}/shares`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:person.id,user_name:person.name})});
        if(!y.ok){alert(await responseError(y));return}renderShares()};row.append(label,add);results.appendChild(row)});
  };compose.append(search,find);box.append(compose,results);
  await renderShareLinks(box);
}
async function renderShareLinks(box){
  if(!authEnabled)return;
  const panel=document.createElement('div');panel.className='detail-panel';
  const heading=document.createElement('h3');heading.textContent='限时分享链接';
  const hint=document.createElement('p');hint.textContent='链接7天后失效，可提前撤销。持有链接的人无需登录即可查看所选内容，请只发给可信的人。';
  const compose=document.createElement('div');compose.className='detail-compose';
  const kind=document.createElement('select');kind.setAttribute('aria-label','分享内容');
  [['report','纪要 Markdown'],['word','纪要 Word 与逐字稿'],['transcript','逐字稿 TXT'],['audio','原始录音']].forEach(([value,label])=>{
    const option=document.createElement('option');option.value=value;option.textContent=label;kind.appendChild(option)});
  const create=document.createElement('button');create.textContent='生成链接';
  const result=document.createElement('div');result.className='detail-compose';
  create.onclick=async()=>{
    create.disabled=true;
    try{
      const r=await fetch(`api/sessions/${currentDetail.id}/share-links`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:kind.value})});
      if(!r.ok)throw new Error(await responseError(r));
      const data=await r.json();const url=new URL(data.path,location.href).href;
      result.innerHTML='';const input=document.createElement('input');input.value=url;input.readOnly=true;input.setAttribute('aria-label','新生成的分享链接');
      const copy=document.createElement('button');copy.textContent='复制';copy.onclick=async()=>{try{await navigator.clipboard.writeText(url);copy.textContent='已复制'}catch(_){input.select();document.execCommand('copy')}};
      result.append(input,copy);await loadShareLinks(panel);
    }catch(e){alert('生成链接失败：'+e.message)}finally{create.disabled=false}
  };
  compose.append(kind,create);panel.append(heading,hint,compose,result);
  const list=document.createElement('div');list.className='share-link-list';panel.appendChild(list);box.appendChild(panel);
  await loadShareLinks(panel);
}
async function loadShareLinks(panel){
  const list=panel.querySelector('.share-link-list');list.textContent='正在读取链接…';
  const r=await fetch(`api/sessions/${currentDetail.id}/share-links`);
  if(!r.ok){list.textContent='链接读取失败';return}
  list.innerHTML='';const labels={report:'纪要 Markdown',word:'纪要 Word',transcript:'逐字稿 TXT',audio:'原始录音'};
  const links=(await r.json()).links.filter(item=>!item.revoked_at&&item.expires_at>Date.now()/1000);
  if(!links.length){list.textContent='暂无有效链接';return}
  links.forEach(item=>{const row=document.createElement('div');row.className='share-person';
    const name=document.createElement('span');name.textContent=`${labels[item.kind]||item.kind} · ${new Date(item.expires_at*1000).toLocaleString('zh-CN')} 失效`;
    const revoke=document.createElement('button');revoke.textContent='撤销';revoke.onclick=async()=>{
      if(!confirm('撤销后链接将立即失效，是否继续？'))return;
      const x=await fetch(`api/sessions/${currentDetail.id}/share-links/${item.id}`,{method:'DELETE'});
      if(!x.ok){alert(await responseError(x));return}loadShareLinks(panel)};row.append(name,revoke);list.appendChild(row)});
}
async function renameCurrentSession(){
  if(!currentDetail)return;const subject=$('detailTitle').value.trim();if(!subject){alert('标题不能为空');return}
  const r=await fetch(`api/sessions/${currentDetail.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({subject})});
  if(!r.ok){alert('保存失败：'+await r.text());return}currentDetail.subject=subject;currentDetail.display_subject=subject;
}
async function deleteCurrentRecording(){
  if(!currentDetail||currentDetail.access!=='owner'||!currentDetail.has_audio)return;
  if(!confirm('删除后将无法回听或下载原始录音，但逐字稿和纪要会保留。是否继续？'))return;
  const response=await fetch(`api/sessions/${currentDetail.id}/recording`,{method:'DELETE'});
  if(!response.ok){alert('删除录音失败：'+await responseError(response));return}
  await openSessionDetail(currentDetail.id,detailReturnProject);
}
async function deleteRecordingSegment(utterance){
  if(!currentDetail||currentDetail.access!=='owner'||!currentDetail.can_delete_segments)return;
  if(!confirm('删除后将移除这段录音和对应逐字稿，后续时间轴会前移，已有纪要需要重新生成。是否继续？'))return;
  const response=await fetch(`api/sessions/${currentDetail.id}/utterances/${utterance.seq}`,{method:'DELETE'});
  if(!response.ok){alert('删除录音片段失败：'+await responseError(response));return}
  await openSessionDetail(currentDetail.id,detailReturnProject);
}
async function deleteCurrentSession(){
  if(!currentDetail)return;const warning=currentDetail.status==='live'?'删除后无法继续录音，也无法恢复。':'删除后无法恢复。';
  if(!confirm(`确定删除“${currentDetail.display_subject}”吗？${warning}`))return;
  const r=await fetch(`api/sessions/${currentDetail.id}`,{method:'DELETE'});if(!r.ok){alert('删除失败：'+await r.text());return}currentDetail=null;await openHistory();
}
async function generateDetailReport(ev){
  if(!currentDetail)return;const sessionId=currentDetail.id,fromProject=detailReturnProject,btn=ev?.currentTarget;
  if(btn){btn.disabled=true;btn.textContent='后台生成中，可稍后回来…'}
  try{
    await requestReport(sessionId,state=>{if(btn)btn.textContent=state.stage||'后台生成中，可稍后回来…'});
    if(currentDetail?.id===sessionId&&$('scrDetail').classList.contains('on'))await openSessionDetail(sessionId,fromProject);
  }catch(e){if(btn){btn.disabled=false;btn.textContent=e.reportPending?'查看生成进度':'重新生成'}alert((e.reportPending?'':'生成失败：')+e.message)}
}
async function resumeRecent(){
  const revision=navigationRevision;
  const recent=localStorage.getItem('fangtan_recent_sid');
  if(recent){try{const r=await fetch(`api/sessions/${recent}`);if(revision!==navigationRevision)return;if(r.ok){const s=await r.json();if(revision!==navigationRevision)return;if(s.status==='live'){await resumeSession(recent,revision);return}}}catch(e){}}
  if(revision!==navigationRevision)return;
  try{const r=await fetch('api/sessions/?limit=50&scope=mine');if(revision!==navigationRevision)return;if(!r.ok)throw new Error(await responseError(r));const p=await r.json();if(revision!==navigationRevision)return;const live=p.items.find(x=>x.status==='live');if(live){await resumeSession(live.id,revision);return}
    openRecordingPreparation();
  }catch(e){if(revision===navigationRevision)alert('续访失败：'+e.message)}
}

function enterRecording(){showCaptureHub('record')}

async function startSession(requireOutline=false){
  if(creatingSession||recorder||nativeRecording||uploading||pending.length||failedUploads.length||nativeQueueCount){alert('当前访谈仍在录音或上传，请先完成保存再新建录音');return false}
  if(requireOutline&&!selectedForRecording().length){$('outlineTip').textContent='请至少勾选一道问题';return}
  const userName=$('userName').value.trim();
  if(!userName){alert('请先填写使用人姓名');$('userName').focus();return}
  localStorage.setItem('fangtan_user_name',userName);
  const subject=$('subject').value.trim()||'未命名访谈';
  const itype=selectedInterviewee();
  const industry=$('industry').value,focus=$('focus').value.trim();
  const chosen=selectedForRecording();creatingSession=true;
  try{
    const r=await fetch('api/sessions/',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_name:userName,subject,interviewee_type:itype,industry,focus,outline:chosen,location:interviewLocation()})});
    if(!r.ok)throw new Error(await r.text());
    sid=(await r.json()).session_id;localStorage.setItem('fangtan_recent_sid',sid);
    resetLiveView(subject);
    renderOutline(chosen,[]);
    showLiveWorkspace();showScreen('scrLive');connectEvents();
    const st=await fetchLiveState();if(st)applyLiveState(st,false);
    return true;
  }catch(e){alert('创建会话失败：'+e.message);return false}finally{creatingSession=false}
}

function resetLiveView(subject='未命名访谈'){
  if(es){es.close();es=null}renderedSeqs.clear();renderedSuggestions.clear();askedSet.clear();
  pending.splice(0);failedUploads.splice(0);uploading=false;finishing=false;
  $('transcripts').innerHTML='';$('chips').innerHTML='';$('sugDock').innerHTML='';$('dimsubs').classList.add('hidden');
  $('liveTitle').textContent=subject;$('timer').textContent='00:00';secs=0;
  $('finishLink').textContent='结束访谈 · 生成纪要素材包';$('finishLink').style.pointerEvents='';
}
function parsePayload(row){try{return typeof row.payload==='string'?JSON.parse(row.payload):row.payload||{}}catch(e){return {}}}
function renderOutline(items,suggestions=[]){
  outlineItems=items||[];$('chips').innerHTML='';askedSet=new Set(suggestions.filter(s=>s.kind==='outline'&&(s.status==='asked'||parsePayload(s).asked)).map(s=>parsePayload(s).item));
  outlineItems.forEach(item=>{
    const c=document.createElement('span');c.className='chip'+(askedSet.has(item)?' done':'');
    const ck=document.createElement('span');ck.className='ck';const tx=document.createElement('span');tx.textContent=item;c.append(ck,tx);
    c.onclick=async()=>{if(c.classList.contains('done'))return;c.classList.add('done');askedSet.add(item);
      const r=await fetch(`api/sessions/${sid}/outline_asked`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({item})});
      if(!r.ok){c.classList.remove('done');askedSet.delete(item);hint('提纲状态保存失败',true)}};
    $('chips').appendChild(c);
  });
}
async function fetchLiveState(sessionId=sid){
  try{const r=await fetch(`api/sessions/${sessionId}/state`);if(!r.ok)throw new Error(await r.text());return await r.json()}
  catch(e){hint('状态恢复失败：'+e.message,true);return null}
}
function applyLiveState(st,restoreAll=true){
  $('liveTitle').textContent=st.subject||st.display_subject||'未命名访谈';
  if(restoreAll){
    (st.utterances||[]).forEach(addUtt);renderOutline(st.outline||[],st.suggestions||[]);
    const latest=(st.suggestions||[]).filter(s=>s.status==='new'&&!['outline','dim','auto_naming'].includes(s.kind)).at(-1);
    if(latest)showSug({type:'suggestion',kind:latest.kind,id:latest.id,...parsePayload(latest)});
    const last=(st.utterances||[]).at(-1);if(last&&!recorder&&!nativeRecording){secs=Math.ceil(last.end_ms/1000);$('timer').textContent=fmtMs(secs*1000)}
  }
  if(st.coverage)renderCoverage(st.coverage);
}
async function syncLiveState(){
  if(syncingState||!sid)return;syncingState=true;try{const st=await fetchLiveState();if(st)applyLiveState(st,true)}finally{syncingState=false}
}
function connectEvents(){
  clearInterval(coverageTimer);coverageTimer=setInterval(refreshCoverage,5000);refreshCoverage();
  if(es)es.close();es=new EventSource(`api/sessions/${sid}/events`);
  es.onopen=()=>{hint(recorder?'录音中 · 连接已恢复':'实时连接已就绪')};
  es.onerror=()=>hint('实时连接已断开，正在自动重连…',true);
  es.onmessage=e=>{
    let ev;try{ev=JSON.parse(e.data)}catch(err){return}
    if(ev.type==='transcript')addUtt(ev);
    else if(ev.type==='signal')attachSignal(ev);
    else if(ev.type==='suggestion')showSug(ev);
    else if(ev.type==='coverage')renderCoverage(ev.coverage);
    else if(ev.type==='naming_started')hint('正在生成会话名称…');
    else if(ev.type==='renamed'){$('liveTitle').textContent=ev.subject;document.title=ev.subject+' · 访谈体察'}
    else if(ev.type==='ready')syncLiveState();
    else if(ev.type==='error')hint(ev.message,true);
  };
}
async function refreshCoverage(){
  if(coverageSyncing||!sid||document.visibilityState==='hidden'||!$('scrLive').classList.contains('on'))return;
  coverageSyncing=true;const sessionId=sid;
  try{const r=await fetch(`api/sessions/${sessionId}/coverage`);if(r.ok&&sid===sessionId){const data=await r.json();const cov=data.coverage||data;if(Array.isArray(cov.dims))renderCoverage(cov)}}catch(e){}
  finally{coverageSyncing=false}
}
async function resumeSession(sessionId,revision=navigationRevision){
  const st=await fetchLiveState(sessionId);if(revision!==navigationRevision)return false;
  if(!st||st.status!=='live'){alert('该会话已结束，无法继续录音');return false}
  sid=sessionId;localStorage.setItem('fangtan_recent_sid',sid);resetLiveView();
  applyLiveState(st,true);showLiveWorkspace();showScreen('scrLive');connectEvents();await recoverAudioOutbox();hint('历史内容已恢复，可继续录音');
  return true;
}

async function leaveLive(target='scrInterview'){
  if(preparingRecording||leavingLive)return false;
  const destination=target==='scrSetup'?'scrSetup':'scrInterview';
  if(!$('captureHub').classList.contains('hidden')){
    if(importBusy&&!confirm('材料仍在后台分析。返回后不会中断处理，完成结果可从“历史会话”查看。是否返回？'))return false;
    if(importBusy){importPollToken++;importBusy=false;$('importBtn').disabled=!selectedImportFile;$('importBtn').textContent='分析并生成纪要';setImportStatus('任务继续在后台处理，可稍后到历史会话查看',0)}
    showScreen(destination);return true;
  }
  if(finishing){alert('正在生成纪要，请稍候');return false}
  const hasProgress=Boolean(recorder||nativeRecording||uploading||pending.length||failedUploads.length||renderedSeqs.size||secs);
  const label=destination==='scrSetup'?'首页':'实战访谈';
  if(hasProgress&&!confirm(`返回${label}将停止当前录音，但不会结束或删除本次访谈。内容会保留，可从“历史会话”继续。是否返回？`))return false;
  leavingLive=true;$('liveHomeBtn').setAttribute('aria-disabled','true');
  const recordDisabled=$('orb').disabled;$('orb').disabled=true;
  const btn=$('liveFieldTab');btn.disabled=true;btn.textContent='保存中…';
  try{
    if(recorder||nativeRecording)await stopRec();else await stopWait;
    if(failedUploads.length){pending.push(...failedUploads.splice(0).map(j=>({...j,attempts:0})));pump()}
    if(uploading||pending.length){hint('正在保存剩余录音…');await waitForUploads()}
    if(failedUploads.length)throw new Error('仍有录音片段上传失败，请检查网络后重试');
    if(es){es.close();es=null}
    clearInterval(tick);$('sugDock').innerHTML='';document.title='访谈·体察';showScreen(destination);return true;
  }catch(e){hint('返回失败：'+e.message,true);alert('暂时无法返回：'+e.message);return false;
  }finally{leavingLive=false;$('orb').disabled=recordDisabled;$('liveHomeBtn').removeAttribute('aria-disabled');btn.disabled=false;btn.textContent='实战访谈'}
}

let lastCoverage=null;
function renderCoverage(cov){
  lastCoverage=cov;
  const pct=Math.round(cov.ratio*100);
  $('covNum').textContent=pct+'%';
  const ai=cov.engine==='ai'||cov.analysis_status!==undefined;
  $('covTitle').textContent=ai?'AI 问透进度':cov.mode==='outline'?'筛选提纲摸排进度':'摸排进度';
  $('chips').classList.toggle('hidden',cov.mode==='outline');
  $('covArc').style.strokeDashoffset=194.8*(1-cov.ratio);
  $('covHint').textContent=cov.missing.length?
    (cov.mode==='outline'?`已标记 ${cov.covered.length}/${cov.dims.length} 道问题，点击待问问题查看并标记`:'还没聊到：'+cov.missing.map(i=>cov.dims.find(d=>d.id===i).name).join('、')):(cov.mode==='outline'?'已筛选问题全部摸排完毕 ✓':'七块全部摸排到位 ✓');
  if(ai){const pending=['queued','running','retry','generating','pending','processing'].includes(cov.analysis_status);
    $('covHint').textContent=`AI 判断已问透 ${cov.covered.length}/${cov.dims.length} 项；点选查看原文依据与待追问内容。`+
      (cov.source_stale?` 当前仅依据已分析的 ${cov.analyzed_utterances}/${cov.transcript_utterances} 段，新增内容待判断。`:'')+
      (pending?' 正在分析逐字稿…':cov.analysis_status==='failed'?' 分析暂时失败，可点选问题重新分析。':' 人工“已问”不计入问透比例。');
  }
  const box=$('dims');box.innerHTML='';
  cov.dims.forEach(d=>{
    const el=document.createElement('span');
    el.className='dim'+(d.covered?' hit':'');
    const icon=document.createElement('span');icon.className='ic';icon.textContent=d.icon;
    el.append(icon,document.createTextNode(d.name));
    if(ai){const labels={not_discussed:'未聊到',mentioned:'提到未答',partial:'待问透',sufficient:'已问透'};el.append(document.createTextNode(' · '+(labels[d.status]||'待分析')))}
    el.onclick=()=>{
      if(d.covered&&!ai)return;
      document.querySelectorAll('.dim').forEach(x=>x.classList.remove('open'));
      el.classList.add('open');
      const p=$('dimsubs');p.classList.remove('hidden');p.innerHTML='';
      if(ai){const reason=document.createElement('div');reason.className='why';reason.textContent=d.reason||'尚无足够逐字稿证据。';p.append(reason);
        for(const evidence of (d.evidence||[])){const row=document.createElement('div');row.className='q';row.textContent=`[${fmtMs(evidence.start_ms||0)} · 段落${Number(evidence.seq)+1}] “${evidence.quote}”`;p.append(row)}
      }
      d.subs.forEach(q=>{
        const row=document.createElement('div');row.className='q';row.textContent='“'+q+'”';
        row.onclick=()=>{row.style.opacity=.4};
        p.appendChild(row);
      });
      const m=document.createElement('div');m.className='mark';
      m.textContent=ai?(d.manual_asked?'已人工标记为问过（不等于问透）':'标记问过（不等于问透）'):cov.mode==='outline'?'点这里标记这道问题已摸排 →':'点这里标记「'+d.name+'」已摸排 →';
      m.onclick=async()=>{const r=await fetch(`api/sessions/${sid}/${cov.mode==='outline'?'outline_asked':'dim_asked'}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cov.mode==='outline'?{item:d.item}:{dim:d.id})});
        if(!r.ok){hint('进度保存失败：'+await responseError(r),true);return}p.classList.add('hidden');await syncLiveState()};
      p.appendChild(m);
      if(ai){const retry=document.createElement('div');retry.className='mark';retry.textContent='根据最新逐字稿重新分析 →';retry.onclick=async()=>{retry.textContent='正在提交…';try{const r=await fetch(`api/sessions/${sid}/coverage/analyze`,{method:'POST'});if(!r.ok)throw new Error(await responseError(r));await refreshCoverage()}catch(e){retry.textContent='提交失败：'+e.message}};p.append(retry)}
    };
    box.appendChild(el);
  });
}

function addUtt(ev){
  const key=String(ev.seq??ev.id);if(renderedSeqs.has(key))return;renderedSeqs.add(key);
  const div=document.createElement('div');div.className='utt';div.dataset.seq=ev.seq??ev.id;
  const tm=document.createElement('span');tm.className='tm';tm.textContent=fmtMs(ev.start_ms);
  const tx=document.createElement('span');
  div.appendChild(tm);div.appendChild(tx);tx.textContent=ev.text;
  $('transcripts').appendChild(div);
  if(document.querySelectorAll('.utt').length>2)div.scrollIntoView({block:'end',behavior:'smooth'});
}
function attachSignal(ev){
  const div=document.querySelector(`.utt[data-seq="${ev.seq}"]`);if(!div)return;
  ev.signals.slice(0,2).forEach(s=>{
    const sp=document.createElement('span');sp.className='tag';sp.textContent=s.type;div.appendChild(sp);
  });
}
function fmtMs(ms){const m=Math.floor(ms/60000),s=Math.floor(ms%60000/1000);return String(m).padStart(2,'0')+':'+String(s).padStart(2,'0')}

function showSug(ev){
  if(ev.id!=null){const key=String(ev.id);if(renderedSuggestions.has(key))return;renderedSuggestions.add(key)}
  $('sugDock').innerHTML='';
  if(ev.kind==='playbook')return showPlaybook(ev);
  if(ev.kind==='coverage')return showGap(ev);
  if(!ev.follow_up)return;
  const div=document.createElement('div');div.className='sug';
  const lbl=document.createElement('div');lbl.className='lbl';lbl.textContent='追问建议';
  const q=document.createElement('div');q.className='q serif';q.textContent='“'+ev.follow_up+'”';
  div.appendChild(lbl);div.appendChild(q);
  if(ev.reason){const w=document.createElement('div');w.className='why';w.textContent='为什么这么问 · '+ev.reason;div.appendChild(w)}
  if(ev.alternative){const a=document.createElement('div');a.className='alt';a.textContent='备选 · '+ev.alternative;div.appendChild(a)}
  const row=document.createElement('div');row.className='row';
  const b1=document.createElement('button');b1.className='asked';b1.textContent='已问 ✓';
  b1.onclick=async()=>{await fetch(`api/suggestions/${ev.id}/asked`,{method:'POST'});div.style.transition='opacity .3s';div.style.opacity=.3;b1.disabled=true};
  const b2=document.createElement('button');b2.className='dis';b2.textContent='忽略';
  b2.onclick=async()=>{await fetch(`api/suggestions/${ev.id}/dismissed`,{method:'POST'});$('sugDock').innerHTML=''};
  row.appendChild(b1);row.appendChild(b2);div.appendChild(row);
  $('sugDock').appendChild(div);
  if(navigator.vibrate)navigator.vibrate([80,60,80]);
}

/* 作战开场卡：话术库 */
function showPlaybook(ev){
  const div=document.createElement('div');div.className='sug';
  const lbl=document.createElement('div');lbl.className='lbl';lbl.textContent='老师傅的话 · 开场';
  div.appendChild(lbl);
  (ev.scripts||[]).forEach(s=>{
    const q=document.createElement('div');q.className='q serif';q.style.fontSize='18px';
    q.textContent='“'+s.text+'”';div.appendChild(q);
    const w=document.createElement('div');w.className='why';w.textContent='为什么这么教 · '+s.why;
    div.appendChild(w);
  });
  (ev.consent||[]).forEach(s=>{
    const c=document.createElement('div');c.className='alt';
    c.textContent='🎙 录音授权 · '+s.text;div.appendChild(c);
  });
  if(ev.why){const w=document.createElement('div');w.className='why';w.textContent=ev.why;div.appendChild(w)}
  const row=document.createElement('div');row.className='row';
  const b=document.createElement('button');b.className='asked';b.textContent='记住了，开始聊';
  b.onclick=async()=>{await fetch(`api/suggestions/${ev.id}/asked`,{method:'POST'});$('sugDock').innerHTML=''};
  row.appendChild(b);div.appendChild(row);
  $('sugDock').appendChild(div);
  if(navigator.vibrate)navigator.vibrate([60,40,60]);
}

/* 摸排提醒卡：覆盖度缺口 */
function showGap(ev){
  if(!ev.question)return;
  const div=document.createElement('div');div.className='sug';
  const lbl=document.createElement('div');lbl.className='lbl';lbl.textContent='摸排提醒 · '+ev.dim_name;
  const q=document.createElement('div');q.className='q serif';q.style.fontSize='19px';
  q.textContent='“'+ev.question+'”';div.appendChild(lbl);div.appendChild(q);
  if(ev.why){const w=document.createElement('div');w.className='why';w.textContent=ev.why;div.appendChild(w)}
  const row=document.createElement('div');row.className='row';
  const b1=document.createElement('button');b1.className='asked';b1.textContent='已摸排 ✓';
  b1.onclick=async()=>{await fetch(`api/suggestions/${ev.id}/asked`,{method:'POST'});
    await fetch(`api/sessions/${sid}/${ev.outline_item?'outline_asked':'dim_asked'}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(ev.outline_item?{item:ev.outline_item}:{dim:ev.dim})});
    $('sugDock').innerHTML=''};
  const b2=document.createElement('button');b2.className='dis';b2.textContent='再等等';
  b2.onclick=async()=>{await fetch(`api/suggestions/${ev.id}/dismissed`,{method:'POST'});$('sugDock').innerHTML=''};
  row.appendChild(b1);row.appendChild(b2);div.appendChild(row);
  $('sugDock').appendChild(div);
  if(navigator.vibrate)navigator.vibrate([80,60,80]);
}

async function finishSession(){
  if(finishing)return;finishing=true;$('finishLink').style.pointerEvents='none';$('finishLink').textContent='正在收尾…';
  const sessionId=sid;
  try{
    if(recorder||nativeRecording)await stopRec();else await stopWait;
    if(failedUploads.length){pending.push(...failedUploads.splice(0).map(j=>({...j,attempts:0})));pump()}
    hint('等待剩余录音转写…');await waitForUploads();
    if(failedUploads.length)throw new Error('仍有录音片段上传失败，请检查网络后再次结束');
    hint('正在提交后台纪要任务…');$('finishLink').textContent='后台生成纪要中…';
    const report=await requestReport(sessionId,state=>hint(state.stage||'后台生成纪要，可稍后在“历史会话”查看'));
    if(es){es.close();es=null}
    showScreen('scrReport');$('md').innerHTML=md2html(report);$('dlMd').href=`api/sessions/${sessionId}/report.md`;
    prepareReportArchive(sessionId);
  }catch(e){hint((e.reportPending?'':'生成失败：')+e.message,!e.reportPending);finishing=false;$('finishLink').style.pointerEvents='';$('finishLink').textContent=e.reportPending?'查看纪要生成进度':'重试结束访谈'}
}

/* 迷你 Markdown 渲染：标题/粗体/列表/表格/引用 */
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function inline(s){return esc(s).replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>')}
function md2html(md){
  const lines=md.split('\n');let html='',inTable=false,inList=false;
  const closeList=()=>{if(inList){html+='</ul>';inList=false}};
  for(const line of lines){
    const t=line.trim();
    if(/^\|.*\|$/.test(t)){
      closeList();
      const cells=t.slice(1,-1).split('|').map(c=>`<td>${inline(c)}</td>`).join('');
      if(!inTable){html+='<table>';inTable=true}
      html+=`<tr>${cells}</tr>`;continue;
    }
    if(inTable){html+='</table>';inTable=false}
    if(/^### /.test(t)){closeList();html+=`<h3>${inline(t.slice(4))}</h3>`}
    else if(/^## /.test(t)){closeList();html+=`<h2>${inline(t.slice(3))}</h2>`}
    else if(/^# /.test(t)){closeList();html+=`<h1>${inline(t.slice(2))}</h1>`}
    else if(t.startsWith('> ')){closeList();html+=`<blockquote>${inline(t.slice(2))}</blockquote>`}
    else if(/^[-*] /.test(t)||/^\d+\. /.test(t)){
      if(!inList){html+='<ul style="list-style:none;padding-left:0">';inList=true}
      html+=`<li>${inline(t.replace(/^[-*] /,'').replace(/^\d+\. /,''))}</li>`;
    }
    else if(t===''){closeList()}
    else{closeList();html+=`<p>${inline(t)}</p>`}
  }
  closeList();if(inTable)html+='</table>';
  return html;
}
document.addEventListener('visibilitychange',async()=>{
  if(document.visibilityState!=='visible')return;
  if(outlineJobId&&!outlinePolling)pollOutlineTask();
  if(recorder)acquireWakeLock();
  if(sid){await recoverAudioOutbox();if($('scrLive').classList.contains('on')&&$('captureHub').classList.contains('hidden'))await syncLiveState()}
  if(typeof FangtanNative!=='undefined'&&FangtanNative.available()){
    try{const state=await FangtanNative.call('status');if(state.recording&&state.sessionId!==sid)await resumeSession(state.sessionId);applyNativeStatus(state);await FangtanNative.call('retry')}catch(e){hint(e.message,true)}
  }
});
window.addEventListener('online',()=>{if(outlineJobId&&!outlinePolling)pollOutlineTask();pending.push(...failedUploads.splice(0).map(job=>({...job,attempts:0})));recoverAudioOutbox();pump()});
window.addEventListener('beforeunload',event=>{if(preparingRecording||recorder||uploading||pending.length){event.preventDefault();event.returnValue=''}});
window.addEventListener('fangtan-native-status',event=>{if(event.detail.sessionId===sid)applyNativeStatus(event.detail)});
bootstrapAuth();
