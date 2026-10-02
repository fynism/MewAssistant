// Runs shipped Vue reactivity and real frontend methods against an HTTP fixture.
// No browser, network service, database or npm install is required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const tick = () => new Promise(setImmediate);
const requests = [], notices = [], classes = new Set(), stored = new Map();
const location = {pathname:'/',search:'',origin:'https://platform.example'};
const knowledgeId = '11111111-1111-1111-1111-111111111111';
let app, mounted, unmount, userRole = 'user', confirmAllowed = true, responder;
const context = vm.createContext({console,setTimeout,clearTimeout,TextDecoder,TextEncoder,Headers,FormData,Blob,AbortController,DOMException,URL,URLSearchParams,crypto:require('node:crypto').webcrypto});
vm.runInContext(fs.readFileSync(path.join(root,'frontend/vendor/vue/vue.global.prod.js'),'utf8'),context);
const vue = context.Vue;
Object.assign(context, {
  Vue:{...vue,onMounted(fn) {mounted = fn;},onBeforeUnmount(fn) {unmount = fn;},createApp(definition) {return {use() {},component() {},mount() {app = vue.proxyRefs(definition.setup());}};}},
  ElementPlus:{ElMessage:{success:message=>notices.push(message),warning:message=>notices.push(message),error:message=>notices.push(message)},ElMessageBox:{confirm:async()=>{if(!confirmAllowed) throw new Error('cancel');}}},
  ElementPlusLocaleZhCn:{},
  location,history:{pushState(_,__,url) {const next = new URL(url,location.origin); location.pathname = next.pathname; location.search = next.search;}},
  window:{addEventListener() {},removeEventListener() {},scrollTo() {}},
  document:{documentElement:{classList:{contains:name=>classes.has(name),toggle:(name,on)=>on?classes.add(name):classes.delete(name)}},getElementById:()=>null},
  matchMedia:()=>({matches:false,addEventListener() {},removeEventListener() {}}),
  localStorage:{getItem:key=>stored.get(key),setItem:(key,value)=>stored.set(key,value),removeItem:key=>stored.delete(key)},
  sessionStorage:{getItem:()=>null,setItem() {}},navigator:{clipboard:{writeText:async()=>{}}},
  marked:{parse:text=>text},DOMPurify:{sanitize:text=>text},
  fetch:async(url,options={})=>{requests.push({url,method:options.method || 'GET',body:options.body,headers:options.headers});return responder(url,options);}
});
const json = (data,status=200) => ({ok:status>=200&&status<300,status,headers:new Headers(),json:async()=>data});
responder = async (url,options={}) => {
  if(url === '/platform/services/knowledge') return json({externalAvailable:true,endpoint:'https://mcp.example/mcp/knowledge'});
  if(url === '/auth/login' || url === '/auth/register') return json({access_token:'fixture-token',username:'alice',role:userRole});
  if(url === '/knowledge-settings') return json({allowed_extensions:['.pdf'],max_upload_bytes:1000000,max_retrieval_results:20});
  if(url === '/knowledges') return json(options.method === 'POST' ? {id:knowledgeId,name:'Private',has_ready_documents:true} : {knowledges:[{id:knowledgeId,name:'Private',has_ready_documents:true}]});
  if(url.endsWith('/documents') && options.method !== 'POST') return json({documents:[{id:'doc',filename:'same.pdf',status:'ready'}]});
  if(url.includes('/documents')) return json({id:'doc',status:'pending'});
  if(url === '/tools/debug/retrieve') return json({status:'no_match',results:[]});
  if(url === '/tools/debug/listKnowledges') return json({items:[],nextCursor:null});
  if(url === '/account/api-keys') return json(options.method === 'POST' ? {key:'fixture-only-key'} : {items:[{id:'key',name:'CLI',suffix:'tail',revokedAt:null}]});
  if(url.startsWith('/account/api-keys/')) return json({revokedAt:'2026-10-02'});
  if(url === '/admin/invitations') return json(options.method === 'POST' ? {invite_code:'fixture-invite'} : [{id:'invite',max_uses:1,used_count:0,revoked_at:null}]);
  if(url.startsWith('/admin/invitations/')) return json({revoked:true});
  if(url === '/admin/operations') return json({calls:2,errors:0,windowHours:24,documents:{pending:0,processing:0,failed:0}});
  if(url === '/sessions') return json({sessions:[{session_id:'existing',title:'Earlier'}]});
  if(url.startsWith('/sessions/')) return json(options.method === 'DELETE' ? {deleted:true} : {messages:[{type:'human',content:'Earlier question',knowledge_ids:[knowledgeId]}],last_knowledge_ids:[knowledgeId]});
  if(url === '/chat/stream') {
    const bytes = new TextEncoder().encode('data: {"type":"rag_step","step":{"label":"检索"}}\r\n\r\ndata: {"type":"content","content":"真实协议"}\r\n\r\ndata: {"type":"trace","rag_trace":{"retrieved_chunks":[{"filename":"same.pdf","text":"来源"}]}}\r\n\r\ndata: [DONE]\r\n\r\n');
    let cursor = 0;
    return {...json({}),body:{getReader:()=>({read:async()=>cursor<bytes.length ? {done:false,value:bytes.slice(cursor,cursor+=7)} : {done:true},releaseLock() {}})}};
  }
  return json({deleted:true});
};
vm.runInContext(fs.readFileSync(path.join(root,'frontend/app.js'),'utf8'),context);

(async () => {
  await mounted(); await tick();
  assert.equal(app.connectionEndpoint,'https://fyism.cn/mcp/knowledge');
  assert.ok(!requests.some(item=>item.url==='/platform/services/knowledge'));
  assert.ok(app.connectionExamples[0].source.includes('/mcp/knowledge'));
  assert.equal(JSON.parse(app.connectionExamples[1].source).mcpServers.meowconnect_knowledge.headers.Authorization,'Bearer ${MCP_API_KEY}');
  for(const example of app.connectionExamples) assert.equal(example.tokens.map(token=>token.text).join(''),example.source);
  app.go('/workspace/knowledges'); await tick();
  assert.equal(app.isAuthenticated,false);
  app.authFormRef = {validate:async()=>true,clearValidate() {}};
  Object.assign(app.authForm,{username:'alice',password:'password'});
  await app.submitAuth(); await tick();
  assert.equal(app.page,'/workspace/knowledges');
  assert.equal(app.documents[0].filename,'same.pdf');
  const file = new Blob(['test']); file.name = 'same.pdf';
  await app.uploadDocument({target:{files:[file],value:'selected'}});
  assert.ok(requests.some(item=>item.url===`/knowledges/${knowledgeId}/documents`&&item.method==='POST'));
  await app.documentAction({id:'doc',filename:'same.pdf'},'retry');
  await app.documentAction({id:'doc',filename:'same.pdf'},'delete');
  assert.ok(requests.some(item=>item.url.endsWith('/doc/retry')&&item.method==='POST'));
  assert.ok(requests.some(item=>item.url.endsWith('/doc')&&item.method==='DELETE'));
  assert.ok(!requests.some(item=>/^\/documents(?:\/|$)/.test(item.url)));
  app.openTryWithKnowledge(knowledgeId); await tick();
  app.chatInput = '测试问题'; await app.sendChat();
  const chatRequest = requests.find(item=>item.url==='/chat/stream');
  assert.deepEqual(JSON.parse(chatRequest.body).knowledge_ids,[knowledgeId]);
  assert.equal(app.messages[1].text,'真实协议');
  assert.equal(app.messages[1].steps[0].label,'检索');
  assert.equal(app.messages[1].trace.retrieved_chunks[0].filename,'same.pdf');
  const chatPageRequests = requests.length;
  await app.runTool(); assert.equal(requests.length,chatPageRequests);
  app.go('/services/knowledge'); await tick();
  assert.equal(app.knowledges[0].id,knowledgeId);
  app.toolQuery = '测试检索'; await app.runTool();
  assert.equal(app.toolResult.status,'no_match');
  assert.deepEqual(JSON.parse(requests.find(item=>item.url==='/tools/debug/retrieve').body).knowledgeIds,[knowledgeId]);
  app.selectedKnowledgeIds = []; await tick();
  const before = requests.length; await app.runTool(); assert.equal(requests.length,before);
  assert.ok(app.toolError.includes('选择至少一个'));
  app.toolName = 'listKnowledges'; await tick(); await app.runTool();
  assert.ok(Array.isArray(app.toolResult.items));
  app.go('/try'); await tick(); assert.equal(app.toolResult,null);
  await app.loadSessions(); await app.loadSession({session_id:'existing'});
  assert.equal(app.messages[0].text,'Earlier question'); assert.equal(app.selectedKnowledgeIds[0],knowledgeId);
  app.go('/account'); await tick(); app.keyName = 'CLI'; await app.createKey();
  assert.equal(app.newKey,'fixture-only-key'); assert.equal(app.keys[0].suffix,'tail');
  confirmAllowed = false; const revokeBefore = requests.length; await app.revokeKey(app.keys[0]); assert.equal(requests.length,revokeBefore);
  confirmAllowed = true; await app.revokeKey(app.keys[0]); assert.equal(app.newKey,'');
  app.keyName = 'CLI'; await app.createKey(); app.go('/'); await tick(); assert.equal(app.newKey,'');
  app.logout(); userRole = 'admin'; await app.submitAuth(); app.go('/admin'); await tick();
  await app.createInvite(); assert.equal(app.newInvite,'fixture-invite'); assert.equal(app.operations.calls,2);
  await app.revokeInvite(app.invites[0]); assert.equal(app.newInvite,'');
  app.toggleTheme(); assert.ok(classes.has('dark'));
  app.go('/account'); await tick();
  // A creation response arriving after leaving the page must not reveal its key.
  const normalResponder = responder; let finish;
  responder = async(url,options)=>url==='/account/api-keys'&&options.method==='POST' ? new Promise(resolve=>{finish=resolve;}) : normalResponder(url,options);
  app.keyName = 'Slow'; const pending = app.createKey(); await tick(); app.go('/'); await tick(); finish(json({key:'late-secret'})); await pending; assert.equal(app.newKey,'');
  responder = normalResponder;
  app.go('/workspace/knowledges'); await tick();
  responder = async()=>json({detail:'expired'},401); await app.loadKnowledges();
  assert.equal(app.isAuthenticated,false); assert.equal(app.knowledges.length,0); assert.equal(app.authOpen,true);
  unmount();
  console.log('PASS: frontend API contracts, login continuity, files, scope, chunked SSE, sources, tools, history, credentials, admin, theme, stale responses, 401 cleanup.');
})().catch(error=>{unmount?.();console.error(error);process.exitCode=1;});
