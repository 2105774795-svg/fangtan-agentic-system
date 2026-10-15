/* 时叙录首版：独立身份、私密档案、半双工讲述、带来源的记忆与章节。
 * 故事、身份、任务进度只存在内存，不进入访谈草稿或浏览器持久存储。
 */
window.Memoir = (() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const prefix = 'api/memoir';
  let active = false, epoch = 0, identity = null, profile = null, snapshot = null;
  let session = null, turns = [], currentQuestion = '', busy = false;
  let media = null, microphone = null, chunks = [], recorded = null, recordingTimer = null;
  let previewURL = null, stopPromise = null, recordingEpoch = 0, requestId = null;
  let pendingPayload = null, tab = 'story', taskTimer = null;
  const requests = new Set();
  const watchedTasks = new Map();
  const privateFields = ['memoirLoginName', 'memoirLoginPassword', 'memoirProfileName',
    'memoirProfileDescription', 'memoirText', 'memoirChapterTitle'];
  const privateNodes = ['memoirIdentity', 'memoirProfiles', 'memoirProfileTitle', 'memoirProfileAbout',
    'memoirSessions', 'memoirSessionTitle', 'memoirQuestion', 'memoirTurns', 'memoirMemories', 'memoirChapters'];

  class ExpiredView extends Error {}
  const isCurrent = token => active && token === epoch;
  function status(text) { $('memoirStatus').textContent = text || ''; }
  function show(id, visible) { $(id).classList.toggle('hidden', !visible); }
  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function action(text, handler, className) {
    const button = node('button', text, className);button.type = 'button';
    button.onclick = async () => {try {await handler();} catch (error) {report(error);} };
    return button;
  }
  function report(error) {
    if (active && !(error instanceof ExpiredView) && error?.name !== 'AbortError') {
      status(error?.message || '暂时未完成，请稍后重试。');
    }
  }
  function endpoint(path) { return prefix + path; }
  async function request(path, options = {}, token = epoch) {
    const controller = new AbortController();requests.add(controller);
    let timedOut = false;
    const timeout = setTimeout(() => {timedOut = true;controller.abort();}, 120000);
    try {
      const response = await fetch(endpoint(path), {credentials: 'same-origin', cache: 'no-store',
        ...options, signal: controller.signal});
      if (!isCurrent(token)) throw new ExpiredView();
      if (response.status === 401) {
        privateReset();identity = null;showLogin();
        status('私密空间验证已失效，请重新登录。人物资料已从当前页面清除。');
        throw new ExpiredView();
      }
      if (!response.ok) {
        let message = `请求未完成（${response.status}）`;
        try {const data = await response.json();if (typeof data.detail === 'string') message = data.detail;} catch (_) {}
        throw new Error(message);
      }
      const data = await response.json();
      if (!isCurrent(token)) throw new ExpiredView();
      return data;
    } catch (error) {
      if (timedOut && isCurrent(token)) throw new Error('网络等待时间较长，请重试读取或提交；已经送达服务器的原稿不会被撤销。');
      throw error;
    } finally {clearTimeout(timeout);requests.delete(controller);}
  }
  function json(method, body) {
    return {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)};
  }
  function profilePath(suffix = '') {
    if (!profile) throw new Error('请先选择人物档案。');
    return '/profiles/' + encodeURIComponent(profile.id) + suffix;
  }
  function sessionPath(suffix = '') {
    if (!session) throw new Error('请先开始或打开一次讲述。');
    return profilePath('/sessions/' + encodeURIComponent(session.id) + suffix);
  }
  function uuid() {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    const bytes = new Uint8Array(16);
    if (!window.crypto?.getRandomValues) throw new Error('浏览器缺少安全请求标识，请更新浏览器后再提交。');
    window.crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 15) | 64;bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes].map(value => value.toString(16).padStart(2, '0')).join('');
    return hex.slice(0, 8) + '-' + hex.slice(8, 12) + '-' + hex.slice(12, 16) + '-' + hex.slice(16, 20) + '-' + hex.slice(20);
  }
  function timestamp(value) {
    if (!value) return '';
    const date = new Date(typeof value === 'number' ? value * 1000 : value);
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN');
  }
  function safeAudio(value) {
    if (typeof value !== 'string') return '';
    try {
      const url = new URL(value, window.location.href);
      const base = new URL(endpoint('/'), window.location.href);
      return url.origin === base.origin && url.pathname.startsWith(base.pathname + 'profiles/') &&
        !url.username && !url.password ? url.href : '';
    } catch (_) {return '';}
  }
  function audioFor(turnId, url) {
    const source = safeAudio(url || endpoint(profilePath('/audio/' + encodeURIComponent(turnId))));
    if (!source) return null;
    const element = node('audio');element.controls = true;element.preload = 'none';
    element.src = source;return element;
  }
  function stopSpeaking() {
    if (window.speechSynthesis) window.speechSynthesis.cancel();
  }
  function localVoice() {
    if (!window.speechSynthesis) return null;
    return window.speechSynthesis.getVoices().find(voice => /^zh(?:-|_)/i.test(voice.lang) && voice.localService === true) || null;
  }
  function voiceAvailable() {
    const available = !!localVoice();
    $('memoirSpeak').disabled = !available || busy || !!media;
    if (!available && active) $('memoirVoiceNote').textContent = '当前设备没有可用的本地中文音色，请阅读问题文字；可在设备设置中安装中文语音。本模块不会将追问文字发给未知在线语音服务。';
    else if (active) $('memoirVoiceNote').textContent = '使用设备本地中文语音。半双工：先听问题，再录一段；每段最长 110 秒。切到后台会停止采音并保留未提交片段。';
    return available;
  }
  function speak() {
    if (!active || !currentQuestion || media || busy) return;
    const voice = localVoice();
    if (!voice || !window.SpeechSynthesisUtterance) {voiceAvailable();return;}
    $('scrMemoir').querySelectorAll('audio').forEach(audio => audio.pause());
    stopSpeaking();
    const utterance = new window.SpeechSynthesisUtterance(currentQuestion);
    utterance.lang = voice.lang;utterance.voice = voice;utterance.rate = 0.92;
    utterance.onerror = () => {if (active) status('设备语音暂不可用，可直接阅读问题文字继续讲述。');};
    window.speechSynthesis.speak(utterance);
  }
  function question(value, auto = false) {
    currentQuestion = typeof value === 'string' ? value : (value?.text || '');
    $('memoirQuestion').textContent = currentQuestion || '可以先讲一段你最想留下的故事。';
    if (value?.degraded) status('采访服务暂时繁忙，已提供备用问题；原始讲述仍已保存。');
    if (value?.paused) status('可以先停一停，不必回答不愿谈及的问题。');
    if (auto && $('memoirAutoSpeak').checked) speak();
  }
  function stopTracks() {
    if (microphone) microphone.getTracks().forEach(track => track.stop());
    microphone = null;
  }
  function clearPreview() {
    const audio = $('memoirPreview');audio.pause();audio.removeAttribute('src');audio.load();show('memoirPreview', false);
    if (previewURL) URL.revokeObjectURL(previewURL);previewURL = null;
  }
  function setPreview(blob) {
    clearPreview();
    if (!blob) return;
    previewURL = URL.createObjectURL(blob);$('memoirPreview').src = previewURL;show('memoirPreview', true);
  }
  function clearDraft() {
    recorded = null;pendingPayload = null;requestId = null;$('memoirText').value = '';clearPreview();
  }
  function hasDraft() {return !!recorded || !!$('memoirText').value.trim() || !!media || !!pendingPayload;}
  function setBusy(value) {
    busy = value;
    ['memoirStartSession', 'memoirSend', 'memoirSkip', 'memoirFinish', 'memoirPause', 'memoirDiscard',
      'memoirGenerateChapter', 'memoirDeleteProfile', 'memoirProfilesBack'].forEach(id => {$(id).disabled = value;});
    $('memoirRecord').disabled = value;
    $('memoirText').disabled = value || !!pendingPayload || !!recorded || !!media;
    if (session && session.status !== 'live') {$('memoirFinish').disabled = true;$('memoirSkip').disabled = true;}
    voiceAvailable();
  }
  async function stopRecording(keep = true) {
    if (!media) {stopTracks();return stopPromise;}
    const recorder = media, collected = chunks, capturedStream = microphone;
    media = null;chunks = [];clearTimeout(recordingTimer);recordingTimer = null;
    const token = recordingEpoch;
    stopPromise = new Promise(resolve => {
      recorder.onstop = () => {
        capturedStream?.getTracks().forEach(track => track.stop());
        if (keep && isCurrent(token) && collected.length) {
          const blob = new Blob(collected, {type: recorder.mimeType || collected[0].type || 'audio/webm'});
          if (blob.size && blob.size <= 10 * 1024 * 1024) {recorded = blob;requestId = requestId || uuid();setPreview(blob);}
          else status('录音为空或超过 10MB，请缩短片段重新录制。');
        }
        if (isCurrent(token)) {$('memoirRecord').textContent = '录一段';$('memoirRecord').setAttribute('aria-pressed', 'false');setBusy(busy);}
        resolve();
      };
      try {recorder.stop();} catch (_) {stopTracks();chunks = [];resolve();}
    });
    // 麦克风立即释放，不等上传或网络往返。
    stopTracks();return stopPromise;
  }
  async function toggleRecord() {
    if (busy) return;
    if (media) {await stopRecording(true);await sendTurn();return;}
    if (document.hidden) {status('请回到前台页面后再开始录音。');return;}
    if (hasDraft()) {status('请先发送或清除当前未提交内容，再录下一段。');return;}
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      status('当前浏览器不支持录音，请用文字讲述，或在支持录音的 HTTPS 浏览器打开。');return;
    }
    stopSpeaking();
    $('scrMemoir').querySelectorAll('audio').forEach(audio => audio.pause());
    const token = epoch;setBusy(true);
    try {
      const acquired = await navigator.mediaDevices.getUserMedia({audio: true});
      if (!isCurrent(token) || document.hidden) {acquired.getTracks().forEach(track => track.stop());if (isCurrent(token)) status('页面已进入后台，没有启动录音；请返回前台后重试。');return;}
      microphone = acquired;const bucket = [];chunks = bucket;recordingEpoch = token;
      const mimeType = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm'].find(type => window.MediaRecorder.isTypeSupported?.(type));
      media = mimeType ? new window.MediaRecorder(acquired, {mimeType}) : new window.MediaRecorder(acquired);
      media.ondataavailable = event => {if (event.data?.size && isCurrent(token)) bucket.push(event.data);};
      media.onerror = () => {
        stopRecording(true).then(() => {if (isCurrent(token)) status('设备录音被中断，已保留可用片段，请检查后重试发送。');});
      };
      media.start();$('memoirRecord').textContent = '说完了';$('memoirRecord').setAttribute('aria-pressed', 'true');
      status('正在录音，点击“说完了”后停止麦克风并提交。');
      recordingTimer = setTimeout(async () => {
        if (isCurrent(token) && media) {await stopRecording(true);if (isCurrent(token)) {status('已到单段时长上限，正在提交这一段。');await sendTurn().catch(report);}}
      }, 110000);
    } catch (error) {stopTracks();media = null;throw new Error('无法打开麦克风，请检查浏览器权限，或使用文字讲述。');}
    finally {if (isCurrent(token)) setBusy(false);}
  }
  function privateReset() {
    epoch++;requests.forEach(controller => controller.abort());requests.clear();
    clearTimeout(taskTimer);taskTimer = null;watchedTasks.clear();
    stopSpeaking();stopRecording(false);stopTracks();chunks = [];
    $('scrMemoir').querySelectorAll('audio').forEach(audio => {audio.pause();audio.removeAttribute('src');audio.load();});
    clearDraft();identity = null;profile = null;snapshot = null;session = null;turns = [];currentQuestion = '';
    privateFields.forEach(id => {$(id).value = '';});privateNodes.forEach(id => {$(id).textContent = '';});
    ['memoirRecordingConsent', 'memoirCloudConsent', 'memoirAutoSpeak'].forEach(id => {$(id).checked = false;});
    $('memoirCreateDetails').open = false;$('memoirRecord').textContent = '录一段';$('memoirRecord').setAttribute('aria-pressed', 'false');
    $('memoirLoginSubmit').disabled = false;$('memoirCreateSubmit').disabled = false;
    show('memoirConversation', false);setBusy(false);status('');
  }
  function showLogin() {
    show('memoirLogin', true);show('memoirLibrary', false);show('memoirProfile', false);show('memoirLogout', false);
  }
  async function open() {
    if ((typeof creatingSession !== 'undefined' && creatingSession)
        || (typeof preparingRecording !== 'undefined' && preparingRecording)
        || (typeof leavingLive !== 'undefined' && leavingLive)) {
      window.alert('访谈正在创建或保存，请完成后再进入时叙录。');return;
    }
    // 不允许把原访谈的正在采音/上传内容悄悄带入另一个模式。
    if ((typeof recorder !== 'undefined' && recorder) || (typeof nativeRecording !== 'undefined' && nativeRecording)) {
      window.alert('请先暂停原访谈录音，再进入时叙录。');return;
    }
    if (active) return;
    privateReset();active = true;
    if (typeof showScreen === 'function') showScreen('scrMemoir');
    else document.querySelectorAll('.screen').forEach(element => element.classList.toggle('on', element.id === 'scrMemoir'));
    showLogin();status('正在验证私密空间…');const token = epoch;
    try {const data = await request('/auth/me', {}, token);identity = data.user;await loadLibrary(token);}
    catch (error) {report(error);}
  }
  function leave(force = false) {
    if (!force && hasDraft() && !window.confirm('有未提交或尚在确认的文字、录音。离开会清除本设备片段；已经到达服务器的讲述和后台任务仍可能保存。确定离开吗？')) return false;
    active = false;privateReset();showLogin();
    if (typeof showScreen === 'function') showScreen('scrSetup');
    return true;
  }
  async function login(event) {
    event.preventDefault();if (busy || $('memoirLoginSubmit').disabled) return;
    const token = epoch;$('memoirLoginSubmit').disabled = true;
    status('正在单独验证员工账号…');
    try {
      const data = await request('/auth/login', json('POST', {name: $('memoirLoginName').value.trim(), password: $('memoirLoginPassword').value}), token);
      identity = data.user;$('memoirLoginPassword').value = '';await loadLibrary(token);
    } finally {if (isCurrent(token)) $('memoirLoginSubmit').disabled = false;}
  }
  async function logout() {
    if (hasDraft() && !window.confirm('退出会清除当前设备上的未提交文字和录音。已提交资料保留，是否继续？')) return;
    privateReset();showLogin();show('memoirLogout', true);status('本页资料已清空，正在确认服务器退出…');
    let confirmed = false;
    try {await request('/auth/logout', {method: 'POST'});confirmed = true;}
    catch (error) {if (error instanceof ExpiredView) confirmed = true;}
    finally {
      privateReset();showLogin();show('memoirLogout', !confirmed);
      status(confirmed ? '已退出私密空间，当前页面的故事、录音预览和身份已清除。' : '当前页面资料已清空，但服务器退出尚未确认，请联网后再次点击“退出私密空间”。');
      $('memoirLoginSubmit').disabled = false;
    }
  }
  async function loadLibrary(token = epoch) {
    const data = await request('/profiles', {}, token);
    clearTimeout(taskTimer);taskTimer = null;watchedTasks.clear();
    profile = null;snapshot = null;session = null;turns = [];question('');clearDraft();
    show('memoirLogin', false);show('memoirLibrary', true);show('memoirProfile', false);show('memoirLogout', true);
    $('memoirIdentity').textContent = (identity?.name || '当前员工') + ' 的私密人物档案 · 不进入团队共享列表';
    const container = $('memoirProfiles');container.textContent = '';
    for (const item of data.profiles || []) {
      const card = node('section', undefined, 'memoir-card');
      card.append(node('h3', item.name, 'serif'), node('p', item.description || '慢慢讲，把故事留下来。'));
      card.append(node('p', '最近整理：' + timestamp(item.updated_at), 'memoir-note'));
      const actions = node('div', undefined, 'memoir-actions');actions.append(action('打开人物档案', () => openProfile(item.id), 'primary'));card.append(actions);container.append(card);
    }
    if (!container.children.length) container.append(node('p', '还没有人物档案。先建立一份私密档案，再开始讲故事。', 'empty'));
    status('');
  }
  async function createProfile(event) {
    event.preventDefault();if (busy || $('memoirCreateSubmit').disabled) return;
    if (!$('memoirRecordingConsent').checked || !$('memoirCloudConsent').checked) {status('请分别取得录音和云端处理的明确同意，不能默认代为同意。');return;}
    const token = epoch;$('memoirCreateSubmit').disabled = true;
    try {
      const data = await request('/profiles', json('POST', {name: $('memoirProfileName').value.trim(), description: $('memoirProfileDescription').value.trim(),
        recording_consent: true, cloud_consent: true}), token);
      $('memoirProfileName').value = '';$('memoirProfileDescription').value = '';
      $('memoirRecordingConsent').checked = false;$('memoirCloudConsent').checked = false;
      await openProfile(data.profile.id);
    } finally {if (isCurrent(token)) $('memoirCreateSubmit').disabled = false;}
  }
  async function openProfile(id, keepTab = false) {
    const token = epoch;const data = await request('/profiles/' + encodeURIComponent(id), {}, token);
    if (profile?.id !== id) {clearTimeout(taskTimer);taskTimer = null;watchedTasks.clear();}
    profile = data.profile;snapshot = data;session = null;turns = [];question('');
    show('memoirLibrary', false);show('memoirProfile', true);show('memoirConversation', false);show('memoirSessionsPanel', true);
    $('memoirProfileTitle').textContent = profile.name;
    $('memoirProfileAbout').textContent = profile.description || '只记录有来源的故事，允许模糊、停顿和不同说法。';
    renderProfile();selectTab(keepTab ? tab : 'story');status('');watchKnownTasks(data.tasks || []);
  }
  function selectTab(value) {
    if (media) {status('请先停止录音，再切换时间线或草稿。');return;}
    stopSpeaking();tab = value;
    for (const name of ['story', 'timeline', 'book']) {
      const title = name[0].toUpperCase() + name.slice(1);
      $('memoir' + title + 'Tab').setAttribute('aria-selected', String(name === value));
      show('memoir' + title + 'Panel', name === value);
    }
  }
  function renderProfile() {
    renderSessions();renderMemories();renderChapters();
  }
  function renderSessions() {
    const container = $('memoirSessions');container.textContent = '';
    for (const item of snapshot?.sessions || []) {
      const card = node('div', undefined, 'memoir-card');
      card.append(node('h3', item.title || '一次讲述'), node('p', timestamp(item.created_at) + ' · ' + (item.status === 'live' ? '可继续讲述' : '已结束'), 'memoir-note'));
      const actions = node('div', undefined, 'memoir-actions');actions.append(action(item.status === 'live' ? '继续这一场讲述' : '查看原话与回听', () => openSession(item.id)));card.append(actions);container.append(card);
    }
    if (!container.children.length) container.append(node('p', '还没有讲述，从一件记得最清楚的小事开始。', 'memoir-note'));
  }
  function renderEvidence(container, evidence) {
    for (const item of evidence || []) {
      const row = node('div', undefined, 'memoir-evidence');
      row.append(node('p', '原话依据：' + (item.quote || '')));
      const actions = node('div', undefined, 'memoir-actions');
      actions.append(action('查看原始讲述', () => showEvidence(item.turn_id)));
      row.append(actions);container.append(row);
    }
  }
  async function showEvidence(turnId) {
    const token = epoch;const data = await request(profilePath(), {}, token);
    // 片段标识不包含任何本地路径；只在授权档案的场次内寻找原文。
    for (const item of data.sessions || []) {
      const source = await request(profilePath('/sessions/' + encodeURIComponent(item.id)), {}, token);
      const match = (source.turns || []).find(turn => turn.id === turnId);
      if (match) {
        if (hasDraft() && !window.confirm('打开原话将清除当前未提交片段，是否继续？')) return;
        clearDraft();await openSession(item.id);selectTab('story');
        const target = $('memoirTurn-' + turnId);if (target) target.scrollIntoView({block: 'center', behavior: 'smooth'});
        status('已定位到本档案的原始讲述；有录音的片段可直接回听。');return;
      }
    }
    status('原始片段已不存在或无权访问，请重新整理资料。');
  }
  function renderMemories() {
    const container = $('memoirMemories');container.textContent = '';
    for (const item of snapshot?.memories || []) {
      const card = node('section', undefined, 'memoir-card');
      card.append(node('h3', item.title || '一段往事', 'serif'));
      card.append(node('p', (item.date_text || '时间待确认') + ' · ' + (item.status === 'confirmed' ? '已人工确认' : '候选线索，待核实'), 'memoir-note'));
      card.append(node('p', item.description || '', 'memoir-text'));renderEvidence(card, item.evidence);
      if (item.status !== 'confirmed') {
        const actions = node('div', undefined, 'memoir-actions');
        actions.append(action('核对原话后确认', async () => {
          await request(profilePath('/events/' + encodeURIComponent(item.id) + '/confirm'), {method: 'POST'});
          await refreshProfile();status('已记录人工确认；原始讲述没有被覆盖。');
        }));card.append(actions);
      }
      container.append(card);
    }
    if (!container.children.length) container.append(node('p', '还没有时间线候选。结束一场讲述并完成后台整理后，会在这里出现。', 'empty'));
  }
  function renderChapters() {
    const container = $('memoirChapters');container.textContent = '';
    for (const item of snapshot?.chapters || []) {
      const card = node('section', undefined, 'memoir-card');
      const task = (snapshot?.tasks || []).find(task => (task.id || task.task_id) === item.task_id);
      card.append(node('h3', item.title || '时叙录章节', 'serif'));
      card.append(node('p', item.source_stale ? '原始讲述已更新，本章需生成新版本后再确认。' :
        task?.status === 'failed' ? '本次整理暂未完成，原始讲述仍保留，可重新生成草稿。' :
        task?.status === 'cancelled' ? '本次整理已取消。' : item.status === 'generating' ? '正在后台整理，尚未形成可确认的草稿。' :
        item.status === 'confirmed' ? '已人工确认的版本' : 'AI 草稿 · 请核对原话', 'memoir-note'));
      for (const paragraph of item.paragraphs || []) {
        card.append(node('p', paragraph.text, 'memoir-text'));renderEvidence(card, paragraph.evidence);
      }
      const actions = node('div', undefined, 'memoir-actions');
      if (!item.source_stale && item.status === 'draft') actions.append(action('核对后确认本版本', async () => {
        await request(profilePath('/chapters/' + encodeURIComponent(item.id) + '/confirm'), {method: 'POST'});
        await refreshProfile();status('已确认此章节版本，原话与其他版本仍保留。');
      }));
      if (item.status === 'confirmed' && !item.source_stale) {
        for (const [format, label] of [['md', '导出 Markdown'], ['docx', '导出 Word']]) {
          const link = node('a', label);link.href = endpoint(profilePath('/chapters/' + encodeURIComponent(item.id) + '/export?format=' + format));
          link.download = '';link.rel = 'noopener noreferrer';link.target = '_blank';actions.append(link);
        }
      }
      card.append(actions);container.append(card);
    }
    if (!container.children.length) container.append(node('p', '还没有章节草稿。先讲一些故事，再按主题整理成书。', 'empty'));
  }
  async function refreshProfile() {
    if (!profile) return;
    const id = profile.id, data = await request(profilePath());
    if (!profile || profile.id !== id) throw new ExpiredView();
    profile = data.profile;snapshot = data;renderProfile();watchKnownTasks(data.tasks || []);
  }
  async function startSession() {
    if (busy || !profile) return;
    const token = epoch;setBusy(true);status('正在准备一个适合承接往事的问题…');
    try {
      const data = await request(profilePath('/sessions'), json('POST', {}), token);
      session = data.session;turns = data.question?.turn ? [data.question.turn] : [];
      showConversation();question(data.question);status(data.question?.degraded ? '已提供备用问题，可以继续讲述。' : '准备好了，先听问题或直接开始讲述。');
    } finally {if (isCurrent(token)) setBusy(false);}
  }
  async function openSession(id) {
    if (busy) return;
    if (hasDraft() && !window.confirm('打开另一场讲述会清除未提交的片段。是否继续？')) return;
    clearDraft();stopSpeaking();
    const data = await request(profilePath('/sessions/' + encodeURIComponent(id)));
    session = data.session;turns = data.turns || [];showConversation();
    const last = [...turns].reverse().find(turn => turn.role === 'assistant');question(last?.text || '可以接着讲上次没有说完的故事。');
    status(session.status === 'live' ? '已恢复原始讲述，不会自动打开麦克风。' : '这一场已结束，可以回看原话或回听录音。');
  }
  function showConversation() {
    show('memoirSessionsPanel', false);show('memoirConversation', true);
    $('memoirSessionTitle').textContent = session?.title || '这一场讲述';
    const live = session?.status === 'live';
    show('memoirRecordControls', live);$('memoirFinish').disabled = !live;$('memoirSkip').disabled = !live;
    renderTurns();voiceAvailable();
  }
  function renderTurns() {
    const container = $('memoirTurns');container.textContent = '';
    for (const item of turns) {
      const row = node('div', undefined, 'memoir-turn');row.id = 'memoirTurn-' + item.id;
      row.append(node('b', item.role === 'human' ? '讲述者 · 原始讲述' : 'AI · 采访提问（不作为人生事实）'));
      row.append(node('span', item.text || ''));
      if (item.role === 'human' && item.audio_url) {const audio = audioFor(item.id, item.audio_url);if (audio) row.append(audio);}
      container.append(row);
    }
  }
  async function sendTurn(skip = false) {
    if (busy || !session || session.status !== 'live') return;
    if (media) {status('请先点击“说完了”停止录音。');return;}
    if (!skip && !pendingPayload && !recorded && !$('memoirText').value.trim()) {status('请先录一段或输入文字。');return;}
    if (!pendingPayload && recorded && $('memoirText').value.trim()) {status('请分别提交录音或文字，不能用手填文字替代原始录音转写。');return;}
    stopSpeaking();const token = epoch;
    if (!pendingPayload) {
      requestId = requestId || uuid();
      pendingPayload = {text: skip ? '这个问题我暂时不想回答，请换一个问题。' : $('memoirText').value.trim(), audio: skip ? null : recorded, id: requestId};
    }
    const pending = pendingPayload, form = new FormData();
    form.append('client_turn_id', pending.id);
    if (pending.text) form.append('text', pending.text);
    if (pending.audio) {
      const extension = /mp4|m4a/.test(pending.audio.type) ? 'm4a' : /ogg/.test(pending.audio.type) ? 'ogg' : 'webm';
      form.append('audio', pending.audio, 'story.' + extension);
    }
    setBusy(true);status('录音已停止，正在保存原始讲述并准备下一个问题…');
    try {
      const data = await request(sessionPath('/turn'), {method: 'POST', body: form}, token);
      if (data.human_turn && !turns.some(turn => turn.id === data.human_turn.id)) turns.push(data.human_turn);
      if (data.question?.turn && !turns.some(turn => turn.id === data.question.turn.id)) turns.push(data.question.turn);
      clearDraft();renderTurns();question(data.question);
      status(data.question?.paused ? '可以停一停，已提交的原始讲述已保存。' : data.question?.degraded ? '原始讲述已保存；采访服务繁忙，暂用备用问题。' : '这一段已保存，可以接着慢慢讲。');
    } catch (error) {
      if (isCurrent(token) && !(error instanceof ExpiredView)) status('提交暂未确认：' + error.message + '。片段仍保留，点击“发送这段讲述”会用相同标识重试，不会重复建稿。');
      throw error;
    } finally {
      if (isCurrent(token)) {setBusy(false);if (!pendingPayload && $('memoirAutoSpeak').checked) speak();}
    }
  }
  async function pauseSession() {
    if (media) await stopRecording(true);
    stopSpeaking();
    if (hasDraft()) {status('已暂停录音。未提交片段仍在本页，请先发送或明确清除，避免丢失。');return;}
    session = null;turns = [];currentQuestion = '';$('memoirQuestion').textContent = '';$('memoirTurns').textContent = '';
    show('memoirConversation', false);show('memoirSessionsPanel', true);await refreshProfile();status('已暂停，稍后可从“继续这一场讲述”接着讲。');
  }
  async function finishSession() {
    if (busy || !session || session.status !== 'live') return;
    if (media) await stopRecording(true);
    if (hasDraft()) {status('还有未提交的讲述，请先发送或清除，再结束这一场。');return;}
    if (!turns.some(turn => turn.role === 'human')) {status('这场还没有保存讲述，可以先讲一段，或暂停后下次继续。');return;}
    stopSpeaking();const token = epoch;setBusy(true);
    try {
      const data = await request(sessionPath('/finish'), {method: 'POST'}, token);
      session = data.session;showConversation();await refreshProfile();
      if (data.task_id) watchTask(data.task_id, '记忆整理');
      status('讲述已保存。记忆整理在后台继续，离开页面不会取消；重新打开本档案可查看结果。');
    } finally {if (isCurrent(token)) {setBusy(false);if (session?.status !== 'live') {$('memoirFinish').disabled = true;$('memoirSkip').disabled = true;}}}
  }
  async function generateChapter() {
    if (busy || !profile) return;
    const token = epoch;setBusy(true);status('正在提交章节整理任务…');
    try {
      const title = $('memoirChapterTitle').value.trim();
      const data = await request(profilePath('/chapters'), json('POST', title ? {title} : {}), token);
      if (data.task_id) watchTask(data.task_id, '章节草稿');
      status('已提交章节整理，后台会依据完整原话生成草稿。离开页面不会取消任务。');await refreshProfile();
    } finally {if (isCurrent(token)) setBusy(false);}
  }
  function watchKnownTasks(tasks) {
    for (const task of tasks) if (['queued', 'running', 'retry'].includes(task.status)) watchTask(task.task_id || task.id, task.kind === 'memoir_memory' ? '记忆整理' : '章节草稿');
  }
  function watchTask(id, label) {
    if (!id || watchedTasks.has(id)) return;
    watchedTasks.set(id, label);
    if (!taskTimer) taskTimer = setTimeout(pollTasks, 1000);
  }
  async function pollTasks() {
    taskTimer = null;
    if (!active || !profile || !watchedTasks.size) return;
    const token = epoch;
    try {
      for (const [id, label] of [...watchedTasks]) {
        const data = await request('/tasks/' + encodeURIComponent(id), {}, token);
        const task = data.task || data;
        if (['done', 'succeeded', 'ready', 'completed'].includes(task.status)) {
          watchedTasks.delete(id);await refreshProfile();status(label + '已完成，请核对原文依据后确认。');
        } else if (['failed', 'cancelled'].includes(task.status)) {
          watchedTasks.delete(id);status(label + (task.status === 'cancelled' ? '已取消。' : '未完成：' + (task.error || '可稍后重试。')));
        } else status(label + '正在后台处理' + (task.stage ? ' · ' + task.stage : '') + '。离开页面不会取消任务。');
      }
    } catch (error) {report(error);}
    if (isCurrent(token) && watchedTasks.size) taskTimer = setTimeout(pollTasks, 2500);
  }
  async function deleteProfile() {
    if (!profile || busy) return;
    if (!window.confirm('永久删除“' + profile.name + '”及其原始录音、逐字稿、记忆、章节？后台整理任务也会取消，此操作不可撤销。')) return;
    await stopRecording(false);stopSpeaking();clearDraft();
    const token = epoch;const data = await request(profilePath(), {method: 'DELETE'}, token);
    privateReset();identity = null;
    // 删除后重新验证身份，不保留任何已删档案正文。
    const me = await request('/auth/me');identity = me.user;await loadLibrary();
    status(data.audio_cleanup_pending ? '档案及文字资料已删除，但部分录音文件清理未完成，请联系管理员处理。' : '已永久删除该人物档案、原始录音与衍生资料，无法恢复。');
  }
  function bind(id, handler) {$(id).addEventListener('click', () => Promise.resolve().then(handler).catch(report));}
  $('memoirLoginForm').addEventListener('submit', event => login(event).catch(report));
  $('memoirCreateForm').addEventListener('submit', event => createProfile(event).catch(report));
  bind('memoirExit', () => leave());bind('memoirLogout', logout);bind('memoirProfilesBack', async () => {
    if (hasDraft() && !window.confirm('返回档案列表会清除未提交内容，确定返回吗？')) return;
    stopSpeaking();await stopRecording(false);clearDraft();await loadLibrary();
  });
  bind('memoirDeleteProfile', deleteProfile);bind('memoirStartSession', startSession);
  bind('memoirStoryTab', () => selectTab('story'));bind('memoirTimelineTab', () => selectTab('timeline'));bind('memoirBookTab', () => selectTab('book'));
  bind('memoirRecord', toggleRecord);bind('memoirSend', () => sendTurn());bind('memoirSkip', () => {
    if (hasDraft()) {status('请先发送或清除当前片段，再换一个问题。');return;}return sendTurn(true);
  });
  bind('memoirSpeak', speak);bind('memoirStopSpeak', stopSpeaking);bind('memoirPause', pauseSession);bind('memoirFinish', finishSession);bind('memoirGenerateChapter', generateChapter);
  bind('memoirDiscard', async () => {
    if (busy || !hasDraft() || !window.confirm('清除尚未提交的文字与录音？已提交故事不会受影响。')) return;
    await stopRecording(false);clearDraft();$('memoirText').disabled = false;status('已清除未提交片段。');
  });
  if (window.speechSynthesis?.addEventListener) window.speechSynthesis.addEventListener('voiceschanged', voiceAvailable);
  document.addEventListener('visibilitychange', () => {
    if (active && document.hidden) {stopSpeaking();if (media) stopRecording(true).then(() => {if (active) status('页面进入后台，已停止麦克风并保留未提交片段。回到前台后可检查、发送。');});}
  });
  window.addEventListener('beforeunload', event => {if (active && hasDraft()) {event.preventDefault();event.returnValue = '';}});
  window.addEventListener('pagehide', () => {if (active) {active = false;privateReset();showLogin();}});
  window.addEventListener('pageshow', event => {
    // BFCache 恢复时不把旧的私密界面当作仍已授权，也不留下 active=false 的死页面。
    if (event.persisted && !active && $('scrMemoir').classList.contains('on') && typeof showScreen === 'function') showScreen('scrSetup');
  });
  if (window.MutationObserver) {
    const observer = new window.MutationObserver(() => {
      if (!active) return;
      const gate = $('authGate');
      if (!$('scrMemoir').classList.contains('on') || (gate && !gate.classList.contains('hidden'))) {
        active = false;privateReset();showLogin();
      }
    });
    observer.observe($('scrMemoir'), {attributes: true, attributeFilter: ['class']});
    if ($('authGate')) observer.observe($('authGate'), {attributes: true, attributeFilter: ['class']});
  }
  return Object.freeze({open, leave});
})();
