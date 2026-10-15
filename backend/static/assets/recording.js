async function toggleRec(){
  if(recorder||nativeRecording){await stopRec();return}
  if(typeof FangtanNative!=='undefined'&&FangtanNative.available()){
    try{const result=await FangtanNative.call('start',{sessionId:sid,offsetMs:secs*1000});applyNativeStatus(result);hint('鸿蒙原生录音中，可熄屏或切到后台')}
    catch(e){hint('原生录音启动失败：'+e.message,true)}return;
  }
  await stopWait;
  try{stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true}})}
  catch(e){alert('无法访问麦克风：'+e.message);return}
  const mime=MediaRecorder.isTypeSupported('audio/webm;codecs=opus')?'audio/webm;codecs=opus':'';
  recorder=new MediaRecorder(stream,mime?{mimeType:mime}:undefined);
  const active=recorder;
  // timeslice 后续片段不保证包含容器头；每次 stop/start 产出完整的独立音频。
  active.ondataavailable=e=>{if(e.data.size)queueAudio(e.data)};
  let segmentStopped=()=>{};
  const startSegment=()=>{
    segmentWait=new Promise(resolve=>{segmentStopped=resolve});
    active.start();
    recordingTimer=setTimeout(()=>{if(recorder===active&&active.state==='recording')active.stop()},10000);
  };
  active.onstop=()=>{
    segmentStopped();
    if(recorder===active){if(stream?.active!==false&&stream?.getTracks().some(track=>track.readyState==='ended')!==true)startSegment();else{stopRec();hint('麦克风被系统中断，已保存可获取的录音，返回后点“录”继续',true)}}
  };
  active.onerror=()=>{stopRec();hint('录音被浏览器中断，已保存的分片会继续上传，请重新点击“录”',true)};
  stream.getTracks().forEach(track=>track.addEventListener?.('ended',()=>{if(recorder===active){stopRec();hint('系统已停止麦克风，录音已中断，请返回后继续',true)}}));
  startSegment();
  $('orb').classList.add('rec');$('orb').textContent='停';
  $('livebar').classList.add('rec');$('stTxt').textContent='录音中';
  hint('边说边转写，建议自动推送');
  acquireWakeLock();recordClockStart=Date.now();recordClockOffset=secs;
  clearInterval(tick);tick=setInterval(()=>{secs=recordClockOffset+Math.floor((Date.now()-recordClockStart)/1000);$('timer').textContent=fmtMs(secs*1000)},1000);
}
function stopRec(){
  if(nativeRecording){return FangtanNative.call('stop').then(applyNativeStatus)}
  if(!recorder)return stopWait;
  const active=recorder;
  recorder=null;clearTimeout(recordingTimer);recordingTimer=null;
  stopWait=segmentWait.then(()=>{pump()});
  // 分段 stop 已排队时仍等待最后的 dataavailable，但不重复调用 stop。
  if(active.state!=='inactive')active.stop();
  if(stream)stream.getTracks().forEach(t=>t.stop());stream=null;
  clearInterval(tick);
  releaseWakeLock();
  $('orb').classList.remove('rec');$('orb').textContent='录';
  $('livebar').classList.remove('rec');$('stTxt').textContent='已停止';
  hint('剩余片段转写中…');
  return stopWait;
}
async function acquireWakeLock(){try{if(navigator.wakeLock&&document.visibilityState==='visible')wakeLock=await navigator.wakeLock.request('screen')}catch(e){}}
function releaseWakeLock(){if(wakeLock){wakeLock.release().catch(()=>{});wakeLock=null}}
function queueAudio(blob){
  const id=typeof crypto!=='undefined'&&crypto.randomUUID?crypto.randomUUID():Date.now().toString(36)+'_'+Math.random().toString(36).slice(2);
  audioOrder=Math.max(audioOrder+1,Date.now()*1000);
  const job={id,sid,blob,attempts:0,createdAt:Date.now(),order:audioOrder};
  job.persisted=typeof RecordingStore==='undefined'?Promise.resolve():RecordingStore.put(job).catch(()=>hint('浏览器存储空间不足，当前分片仍会上传，请保持页面打开',true));
  pending.push(job);pump();
}
async function recoverAudioOutbox(){
  if(recoveringAudio||!sid||typeof RecordingStore==='undefined')return;recoveringAudio=true;
  try{const jobs=await RecordingStore.list(sid);const known=new Set([...pending,...failedUploads].map(j=>j.id));
    if(inFlightChunkId)known.add(inFlightChunkId);audioOrder=Math.max(audioOrder,...jobs.map(job=>job.order||job.createdAt||0));
    for(const job of jobs){if(!known.has(job.id))pending.push(job)}if(pending.length){hint(`正在恢复 ${pending.length} 个未上传分片`);pump()}
  }catch(e){hint('本机录音缓存暂时不可读，请保持页面打开',true)}finally{recoveringAudio=false}
}
function applyNativeStatus(result){
  const wasRecording=nativeRecording;
  nativeRecording=result.recording===true;nativeQueueCount=result.pendingUploads||0;
  if(result.sessionId&&sid&&result.sessionId!==sid){hint('App 中仍有另一场录音，请先返回该访谈',true);return}
  $('orb').classList.toggle('rec',nativeRecording);$('orb').textContent=nativeRecording?'停':'录';
  $('livebar').classList.toggle('rec',nativeRecording);$('stTxt').textContent=nativeRecording?'原生后台录音中':'已停止';
  if(result.elapsedMs!==undefined){secs=Math.floor(result.elapsedMs/1000);$('timer').textContent=fmtMs(result.elapsedMs)}
  if(nativeRecording&&!wasRecording){recordClockStart=Date.now();recordClockOffset=secs;clearInterval(tick);tick=setInterval(()=>{$('timer').textContent=fmtMs((recordClockOffset+Math.floor((Date.now()-recordClockStart)/1000))*1000)},1000)}
  if(!nativeRecording&&wasRecording)clearInterval(tick);
  if(result.error)hint(result.error,true);
}
async function pump(){
  if(uploading)return;
  const job=pending.shift();if(!job)return;
  inFlightChunkId=job.id||null;
  uploading=true;hint('转写中…');
  const type=job.blob.type||'';
  const ext=type.includes('wav')?'wav':type.includes('mp4')?'mp4':type.includes('ogg')?'ogg':'webm';
  const fd=new FormData();fd.append('audio',job.blob,`chunk.${ext}`);
  if(job.id)fd.append('chunk_id',job.id);
  let retryDelay=0,failed=false;
  try{
    if(job.persisted)await job.persisted;
    const r=await fetch(`api/sessions/${job.sid||sid}/audio`,{method:'POST',body:fd});
    if(!r.ok)throw new Error(await responseError(r));
    if(job.id&&typeof RecordingStore!=='undefined'){try{await RecordingStore.remove(job.id)}catch(e){hint('已上传，本机缓存暂未清理；后续重试会自动去重',true)}}
  }catch(e){
    failed=true;job.attempts++;
    if(job.attempts<3){pending.unshift(job);retryDelay=job.attempts*1000;hint(`上传失败，正在第 ${job.attempts+1} 次重试…`,true)}
    else{job.error=e.message;failedUploads.push(job);hint('录音片段处理失败：'+e.message+'；结束时会再次尝试',true)}
  }
  uploading=false;inFlightChunkId=null;
  if(pending.length)setTimeout(pump,retryDelay);
  else if(!failed)hint(recorder?'录音中 · 每10秒自动转写':'已停止 · 可结束生成纪要');
}
async function waitForUploads(timeoutMs=240000){
  const deadline=Date.now()+timeoutMs;
  if(typeof FangtanNative!=='undefined'&&FangtanNative.available()){
    while(Date.now()<deadline){const status=await FangtanNative.call('status');applyNativeStatus(status);if(!status.pendingUploads)break;
      if(status.error)throw new Error(status.error);await FangtanNative.call('retry');await new Promise(resolve=>setTimeout(resolve,1000));}
    if(nativeQueueCount)throw new Error('App 仍有未上传录音，已保存在手机上，请恢复网络后重试');
  }
  while((uploading||pending.length)&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,200));
  if(uploading||pending.length)throw new Error('等待音频上传超时，请检查网络后重试');
}
function hint(msg,err){$('hint').textContent=msg;$('hint').style.color=err?'var(--vermilion)':''}
async function restoreNativeSession(){
  if(typeof FangtanNative==='undefined'||!FangtanNative.available())return;
  try{const state=await FangtanNative.call('status');
    if(state.sessionId&&(state.recording||state.pendingUploads)){await resumeSession(state.sessionId);applyNativeStatus(state);await FangtanNative.call('retry')}
    $('recordingCapability').textContent='当前为鸿蒙原生录音，支持系统允许的熄屏及后台录音；来电或系统终止时会提示并保留已记录内容。';
  }catch(e){$('recordingCapability').textContent='原生录音暂时不可用：'+e.message}
}
