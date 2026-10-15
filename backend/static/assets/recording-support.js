/* Durable browser audio outbox and HarmonyOS native bridge. No audio is cached by a service worker. */
(() => {
  let database;
  const open = () => database ||= new Promise((resolve, reject) => {
    const request = indexedDB.open('fangtan-recording-outbox', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('chunks', {keyPath:'id'});
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => {database=null;reject(request.error)};
  });
  async function transaction(mode, action){
    const db=await open();
    return new Promise((resolve,reject)=>{
      const tx=db.transaction('chunks',mode), request=action(tx.objectStore('chunks'));
      tx.oncomplete=()=>resolve(request.result);tx.onabort=tx.onerror=()=>reject(tx.error||request.error);
    });
  }
  window.RecordingStore={
    put:job=>transaction('readwrite',store=>store.put({id:job.id,sid:job.sid,blob:job.blob,createdAt:job.createdAt||Date.now(),order:job.order||job.createdAt||Date.now(),attempts:0})),
    remove:id=>transaction('readwrite',store=>store.delete(id)),
    list:async sid=>(await transaction('readonly',store=>store.getAll())).filter(job=>job.sid===sid).sort((a,b)=>(a.order||a.createdAt)-(b.order||b.createdAt))
  };
  const calls=new Map();
  window.fangtanNativeResult=message=>{
    const result=typeof message==='string'?JSON.parse(message):message;
    const waiting=calls.get(result.requestId);
    if(waiting){clearTimeout(waiting.timer);calls.delete(result.requestId);result.failed?waiting.reject(new Error(result.error)):waiting.resolve(result)}
    window.dispatchEvent(new CustomEvent('fangtan-native-status',{detail:result}));
  };
  window.FangtanNative={
    available:()=>typeof window.FangtanNativeBridge?.call==='function',
    call:(action,params={})=>new Promise((resolve,reject)=>{
      const requestId=crypto.randomUUID();
      const timer=setTimeout(()=>{calls.delete(requestId);reject(new Error('原生录音没有及时响应，请回到 App 检查录音状态'))},30000);
      calls.set(requestId,{resolve,reject,timer});
      try{const accepted=window.FangtanNativeBridge.call(JSON.stringify({requestId,action,...params}));if(accepted!=='accepted')throw Error(accepted)}
      catch(error){clearTimeout(timer);calls.delete(requestId);reject(error)}
    })
  };
})();
