// Targeted regressions for network failures, stream termination and chat
// recovery. Runs the shipped Vue setup against controlled HTTP fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const tick = () => new Promise(setImmediate);
const NETWORK_ERROR = '网络连接失败，请检查网络后重试';
const knowledgeId = '11111111-1111-1111-1111-111111111111';
const vueSource = fs.readFileSync(path.join(root,'frontend/vendor/vue/vue.global.prod.js'),'utf8');
const appSource = fs.readFileSync(path.join(root,'frontend/app.js'),'utf8');

const json = (data,status=200,headers={}) => ({ok:status>=200&&status<300,status,headers:new Headers(headers),json:async()=>data});
const sseResponse = parts => {
  const queue = parts.map(part => part instanceof Error ? part : new TextEncoder().encode(part));
  return {ok:true,status:200,headers:new Headers(),body:{getReader:()=>({
    read:async()=>{
      const next = queue.shift();
      if (next instanceof Error) throw next;
      return next ? {done:false,value:next} : {done:true};
    },
    releaseLock() {}
  })}};
};
async function base(url,options={}) {
  if (url === '/auth/providers') return json({github:false});
  if (url === '/auth/me') return json({username:'alice',role:'user'});
  if (url === '/knowledge-settings') return json({allowed_extensions:[],max_upload_bytes:0,max_retrieval_results:20});
  if (url === '/knowledges') return json({knowledges:[{id:knowledgeId,name:'Private',has_ready_documents:true}]});
  if (url === '/documents/search') return json({status:'ok',results:[]});
  return json({});
}
function page(pathname, fetchFor, {token='fixture-token'}={}) {
  const parsed = new URL(pathname, 'https://platform.example');
  const requests = [], notices = [], stored = new Map();
  if (token) stored.set('accessToken', token);
  const location = {pathname:parsed.pathname,search:parsed.search,hash:parsed.hash,origin:parsed.origin};
  let app, mounted;
  const context = vm.createContext({console,setTimeout,clearTimeout,TextDecoder,TextEncoder,Headers,FormData,Blob,AbortController,DOMException,URL,URLSearchParams,crypto:require('node:crypto').webcrypto});
  vm.runInContext(vueSource,context);
  const vue = context.Vue;
  Object.assign(context, {
    Vue:{...vue,onMounted(fn) {mounted = fn;},onBeforeUnmount() {},createApp(definition) {return {use() {},component() {},mount() {app = vue.proxyRefs(definition.setup());}};}},
    ElementPlus:{ElMessage:{success:message=>notices.push(message),warning:message=>notices.push(message),error:message=>notices.push(message)},ElMessageBox:{confirm:async()=>true},locale:{}},
    location,
    history:{pushState(_,__,url) {const next = new URL(url,location.origin); location.pathname = next.pathname; location.search = next.search;},replaceState() {}},
    window:{addEventListener() {},removeEventListener() {},scrollTo() {}},
    document:{documentElement:{classList:{contains:()=>false,toggle() {}}},getElementById:()=>null,title:''},
    matchMedia:()=>({matches:false,addEventListener() {},removeEventListener() {}}),
    localStorage:{getItem:key=>stored.get(key),setItem:(key,value)=>stored.set(key,value),removeItem:key=>stored.delete(key)},
    sessionStorage:{getItem:()=>null,setItem() {}},
    navigator:{clipboard:{writeText:async()=>{}}},
    marked:require(path.join(root,'frontend/vendor/marked/marked.umd.js')),DOMPurify:{sanitize:text=>text},
    fetch:async(url,options={})=>{requests.push({url,options}); return fetchFor(url,options);}
  });
  vm.runInContext(appSource,context);
  return {get app() {return app;},mount:()=>mounted(),requests,notices};
}
const restoredNotice = page => page.notices.some(notice=>notice.includes(NETWORK_ERROR) && notice.includes('问题已恢复到输入框'));

(async () => {
  // Registration: an unreachable server must not leak the browser's English
  // TypeError, while a real HTTP detail must still reach the form.
  const register = page('/',async (url,options)=>{
    if (url === '/auth/register') throw new TypeError('Failed to fetch');
    return base(url,options);
  },{token:''});
  await register.mount();
  register.app.authFormRef = {validate:async()=>true,clearValidate() {}};
  register.app.authMode = 'register';
  Object.assign(register.app.authForm,{username:'bob',password:'secret',invite:'invite'});
  await register.app.submitAuth();
  assert.equal(register.app.authError,NETWORK_ERROR);
  assert.equal(register.app.authLoading,false);
  assert.equal(register.requests.filter(item=>item.url==='/auth/register').length,1);

  // The connection can also drop after the response headers, while its JSON
  // body is read.
  const bodyDropped = page('/',async (url,options)=>url === '/auth/register'
    ? {ok:true,status:200,headers:new Headers(),json:async()=>{throw new TypeError('Failed to fetch');}}
    : base(url,options),{token:''});
  await bodyDropped.mount();
  bodyDropped.app.authFormRef = {validate:async()=>true,clearValidate() {}};
  bodyDropped.app.authMode = 'register';
  Object.assign(bodyDropped.app.authForm,{username:'bob',password:'secret',invite:'invite'});
  await bodyDropped.app.submitAuth();
  assert.equal(bodyDropped.app.authError,NETWORK_ERROR);

  const rejected = page('/',async (url,options)=>url === '/auth/register' ? json({detail:'邀请码无效'},400) : base(url,options),{token:''});
  await rejected.mount();
  rejected.app.authFormRef = {validate:async()=>true,clearValidate() {}};
  rejected.app.authMode = 'register';
  Object.assign(rejected.app.authForm,{username:'bob',password:'secret',invite:'bad'});
  await rejected.app.submitAuth();
  assert.equal(rejected.app.authError,'邀请码无效');

  // Search: the same normalization feeds the inline error state.
  const search = page('/workspace/knowledges',async (url,options)=>{
    if (url === '/documents/search') throw new TypeError('Load failed');
    return base(url,options);
  });
  await search.mount();
  search.app.searchQuery = '测试检索';
  await search.app.runSearch();
  assert.equal(search.app.searchError,NETWORK_ERROR);
  assert.equal(search.app.searchLoading,false);

  // Aborting a search stays silent instead of becoming a network error.
  const aborted = page('/workspace/knowledges',(url,options)=>url === '/documents/search'
    ? new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true}))
    : base(url,options));
  await aborted.mount();
  aborted.app.searchQuery = '测试检索';
  const pendingSearch = aborted.app.runSearch();
  await tick();
  aborted.app.clearSearchInput();
  await pendingSearch;
  assert.equal(aborted.app.searchError,'');
  assert.equal(aborted.app.searchLoading,false);

  // A request that never got a response removes the failed exchange and hands
  // the question back. Resending after the edit produces one new exchange.
  let retryCalls = 0;
  const retry = page('/try',async (url,options)=>{
    if (url === '/chat/stream') {
      retryCalls++;
      if (retryCalls === 1) throw new TypeError('Failed to fetch');
      return sseResponse(['data: {"type":"content","content":"第二次成功"}\r\n\r\n','data: [DONE]\r\n\r\n']);
    }
    return base(url,options);
  });
  await retry.mount();
  retry.app.chatInput = '原问题';
  await retry.app.sendChat();
  assert.equal(retry.app.messages.length,0);
  assert.equal(retry.app.chatInput,'原问题');
  assert.equal(retry.app.chatLoading,false);
  assert.ok(restoredNotice(retry));
  assert.equal(retryCalls,1);
  await retry.app.sendChat();
  assert.equal(retry.app.messages.filter(message=>message.author === 'user').length,1);
  assert.equal(retry.app.messages[1].text,'第二次成功');
  assert.equal(retryCalls,2);

  // A draft typed while the answer was pending wins over the failed question.
  let rejectDraft;
  const draft = page('/try',(url,options)=>url === '/chat/stream'
    ? new Promise((_,reject)=>{rejectDraft = reject;})
    : base(url,options));
  await draft.mount();
  draft.app.chatInput = '原问题';
  const pendingDraft = draft.app.sendChat();
  await tick();
  assert.equal(draft.app.messages.length,2);
  draft.app.chatInput = '等待期间的新草稿';
  rejectDraft(new TypeError('Failed to fetch'));
  await pendingDraft;
  assert.equal(draft.app.chatInput,'等待期间的新草稿');
  assert.equal(draft.app.messages.length,2);
  assert.equal(draft.app.messages[1].error,NETWORK_ERROR);
  assert.ok(!draft.notices.some(notice=>notice.includes('问题已恢复到输入框')));

  // HTTP business errors on the stream endpoint keep their detail and retry hint.
  const limited = page('/try',async (url,options)=>url === '/chat/stream' ? json({detail:'请求过于频繁'},429,{'Retry-After':'12'}) : base(url,options));
  await limited.mount();
  limited.app.chatInput = '原问题';
  await limited.app.sendChat();
  assert.equal(limited.app.messages.length,0);
  assert.equal(limited.app.chatInput,'原问题');
  assert.ok(limited.notices.some(notice=>notice.includes('请求过于频繁，请12 秒后重试') && notice.includes('问题已恢复到输入框')));

  // A dropped connection after partial text keeps the partial answer and marks
  // it as incomplete instead of discarding it or calling it a user stop.
  const partial = page('/try',async (url,options)=>url === '/chat/stream'
    ? sseResponse(['data: {"type":"content","content":"半句回答"}\r\n\r\n',new TypeError('Failed to fetch')])
    : base(url,options));
  await partial.mount();
  partial.app.chatInput = '原问题';
  await partial.app.sendChat();
  assert.equal(partial.app.messages.length,2);
  assert.equal(partial.app.messages[1].text,'半句回答');
  assert.equal(partial.app.messages[1].error,NETWORK_ERROR);
  assert.equal(partial.app.chatInput,'');
  assert.ok(!partial.notices.some(notice=>notice.includes('问题已恢复到输入框')));

  // EOF without [DONE] is a truncated stream, not a successful completion.
  const cutoff = page('/try',async (url,options)=>url === '/chat/stream'
    ? sseResponse(['data: {"type":"content","content":"半句回答"}\r\n\r\n'])
    : base(url,options));
  await cutoff.mount();
  cutoff.app.chatInput = '原问题';
  await cutoff.app.sendChat();
  assert.equal(cutoff.app.messages[1].text,'半句回答');
  assert.equal(cutoff.app.messages[1].error,'连接提前结束，内容可能不完整');

  // The same missing completion marker with no answer recovers instead of
  // leaving an empty bubble behind.
  const emptyCutoff = page('/try',async (url,options)=>url === '/chat/stream'
    ? sseResponse([])
    : base(url,options));
  await emptyCutoff.mount();
  emptyCutoff.app.chatInput = '原问题';
  await emptyCutoff.app.sendChat();
  assert.equal(emptyCutoff.app.messages.length,0);
  assert.equal(emptyCutoff.app.chatInput,'原问题');
  assert.ok(emptyCutoff.notices.some(notice=>notice.includes('连接提前结束，请重试') && notice.includes('问题已恢复到输入框')));

  // A transport error after [DONE] must not mark a completed answer as broken.
  const doneThenDrop = page('/try',async (url,options)=>url === '/chat/stream'
    ? sseResponse(['data: {"type":"content","content":"完整回答"}\r\n\r\n','data: [DONE]\r\n\r\n',new TypeError('Failed to fetch')])
    : base(url,options));
  await doneThenDrop.mount();
  doneThenDrop.app.chatInput = '原问题';
  await doneThenDrop.app.sendChat();
  assert.equal(doneThenDrop.app.messages.length,2);
  assert.equal(doneThenDrop.app.messages[1].text,'完整回答');
  assert.equal(doneThenDrop.app.messages[1].error,'');

  // A server error event with no answer also recovers the question.
  const serverError = page('/try',async (url,options)=>url === '/chat/stream'
    ? sseResponse(['data: {"type":"error","content":"对话服务暂时不可用"}\r\n\r\n','data: [DONE]\r\n\r\n'])
    : base(url,options));
  await serverError.mount();
  serverError.app.chatInput = '原问题';
  await serverError.app.sendChat();
  assert.equal(serverError.app.messages.length,0);
  assert.equal(serverError.app.chatInput,'原问题');
  assert.ok(serverError.notices.some(notice=>notice.includes('对话服务暂时不可用') && notice.includes('问题已恢复到输入框')));

  // The stop button is an intentional abort: keep the exchange, never restore
  // the question or turn it into a network error.
  const stopped = page('/try',(url,options)=>url === '/chat/stream'
    ? {ok:true,status:200,headers:new Headers(),body:{getReader:()=>({
      read:()=>new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true})),
      releaseLock() {}
    })}}
    : base(url,options));
  await stopped.mount();
  stopped.app.chatInput = '原问题';
  const pendingStop = stopped.app.sendChat();
  await tick();
  stopped.app.stopChat();
  await pendingStop;
  assert.equal(stopped.app.messages.length,2);
  assert.equal(stopped.app.messages[1].error,'已停止回答');
  assert.equal(stopped.app.chatInput,'');
  assert.ok(!stopped.notices.some(notice=>notice.includes('问题已恢复到输入框')));

  // Switching sessions or accounts owns the transcript; a late failure must
  // not restore a question into the new context.
  let rejectSession;
  const switched = page('/try',(url,options)=>url === '/chat/stream'
    ? new Promise((_,reject)=>{rejectSession = reject;})
    : base(url,options));
  await switched.mount();
  switched.app.chatInput = '原问题';
  const pendingSession = switched.app.sendChat();
  await tick();
  switched.app.newChat();
  rejectSession(new TypeError('Failed to fetch'));
  await pendingSession;
  assert.equal(switched.app.messages.length,0);
  assert.equal(switched.app.chatInput,'');
  assert.ok(!switched.notices.some(notice=>notice.includes('问题已恢复到输入框')));

  let rejectAccount;
  const switchedAccount = page('/try',(url,options)=>url === '/chat/stream'
    ? new Promise((_,reject)=>{rejectAccount = reject;})
    : base(url,options));
  await switchedAccount.mount();
  switchedAccount.app.chatInput = '原问题';
  const pendingAccount = switchedAccount.app.sendChat();
  await tick();
  switchedAccount.app.logout(false);
  rejectAccount(new TypeError('Failed to fetch'));
  await pendingAccount;
  assert.equal(switchedAccount.app.messages.length,0);
  assert.equal(switchedAccount.app.chatInput,'');
  assert.ok(!switchedAccount.notices.some(notice=>notice.includes('问题已恢复到输入框')));

  // Enter still submits, Shift+Enter and composing do not, and a successful
  // answer keeps both the transcript and a draft typed while waiting.
  const success = page('/try',async (url,options)=>url === '/chat/stream'
    ? sseResponse(['data: {"type":"content","content":"完整回答"}\r\n\r\n','data: [DONE]\r\n\r\n'])
    : base(url,options));
  await success.mount();
  success.app.chatInput = '键盘问题';
  let prevented = false;
  success.app.handleChatKey({key:'Enter',shiftKey:false,isComposing:false,preventDefault() {prevented = true;}});
  await tick();
  for (let index = 0; index < 20 && success.app.chatLoading; index++) await tick();
  assert.equal(prevented,true);
  assert.equal(success.app.messages[1].text,'完整回答');
  assert.equal(success.app.messages[1].error,'');
  assert.equal(success.app.chatInput,'');
  success.app.handleChatKey({key:'Enter',shiftKey:true,isComposing:false,preventDefault() {throw new Error('Shift+Enter must not submit');}});
  success.app.handleChatKey({key:'Enter',shiftKey:false,isComposing:true,preventDefault() {throw new Error('composition must not submit');}});
  assert.equal(success.app.messages.length,2);

  console.log('PASS: network errors, HTTP details, aborts, chat recovery, drafts, stream interruption, stop, session/account guards, keyboard submit.');
})().catch(error=>{console.error(error);process.exitCode=1;});
