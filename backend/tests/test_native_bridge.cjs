const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/assets/recording-support.js'),'utf8');
let sequence=0,lastRequest;const timers=new Set(),events=[];
const window={FangtanNativeBridge:{call:message=>{lastRequest=JSON.parse(message);return 'accepted'}},dispatchEvent:event=>events.push(event)};
const context=vm.createContext({window,crypto:{randomUUID:()=> 'request-'+(++sequence)},CustomEvent:class{constructor(name,data){this.name=name;this.detail=data.detail}},
  setTimeout:fn=>{timers.add(fn);return fn},clearTimeout:fn=>timers.delete(fn)});
vm.runInContext(source,context);
(async()=>{
  const status=window.FangtanNative.call('status');window.fangtanNativeResult({requestId:lastRequest.requestId,recording:false,pendingUploads:2,error:'来电中断，录音已保存'});
  assert.equal((await status).pendingUploads,2,'状态提示不应被当作原生调用失败');
  const start=window.FangtanNative.call('start',{sessionId:'live-session'});assert.equal(lastRequest.sessionId,'live-session');
  window.fangtanNativeResult({requestId:lastRequest.requestId,failed:true,error:'麦克风未授权'});
  await assert.rejects(start,/麦克风未授权/);assert.equal(timers.size,0);assert.equal(events.length,2);
  console.log('PASS: 原生录音异步回调、明确失败与状态恢复协议');
})().catch(error=>{console.error(error);process.exitCode=1});
