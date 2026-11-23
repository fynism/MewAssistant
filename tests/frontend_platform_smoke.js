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
  if(url === '/knowledge-settings') return json({allowed_extensions:['.pdf','.txt','.md','.doc','.docx','.xls','.xlsx'],max_upload_bytes:1000000,max_retrieval_results:20});
  if(url === '/knowledges') return json(options.method === 'POST' ? {id:knowledgeId,name:'Private',has_ready_documents:true} : {knowledges:[{id:knowledgeId,name:'Private',has_ready_documents:true}]});
  if(url === '/documents/search') return json({status:'ok',results:[{
    sourceId:'hit',documentId:'doc',knowledgeId,knowledgeName:'Private',filename:'same.pdf',pageNumber:7,
    text:'这是匹配词之前的一段长文本，用来验证侧栏的命中标记可见。接下来是测试检索相关内容。'
  }]});
  if(url.endsWith('/documents') && options.method !== 'POST') return json({documents:[{id:'doc',knowledge_id:knowledgeId,filename:'same.pdf',status:'ready'}]});
  if(url.includes('/preview/')) return {ok:true,headers:new Headers({'X-PDF-Page-Count':'12'}),blob:async()=>new Blob(['fixture page'])};
  if(url.endsWith('/text/content')) return {ok:true,arrayBuffer:async()=>Uint8Array.from([0xd6,0xd0,0xce,0xc4]).buffer};
  if(url.endsWith('/utf16/content')) return {ok:true,arrayBuffer:async()=>Uint8Array.from([0xfe,0xff,0x4e,0x2d,0x65,0x87]).buffer};
  if(url.endsWith('/utf32le/content')) return {ok:true,arrayBuffer:async()=>Uint8Array.from([0xff,0xfe,0,0,0x2d,0x4e,0,0,0x00,0xf6,1,0]).buffer};
  if(url.endsWith('/utf32be/content')) return {ok:true,arrayBuffer:async()=>Uint8Array.from([0,0,0xfe,0xff,0,0,0x4e,0x2d,0,1,0xf6,0x00]).buffer};
  if(url.endsWith('/markdown/content')) return {ok:true,arrayBuffer:async()=>new TextEncoder().encode('\uFEFF# 文档').buffer};
  if(url.includes('/spreadsheet-preview')) {
    const params = new URL(url,location.origin).searchParams;
    const cited = params.has('citation_page');
    return json({
      sheets:['库存 & 采购','用途 # 数据'], sheet:params.get('sheet') || (cited ? '用途 # 数据' : '库存 & 采购'),
      row_start:Number(params.get('row')) || (cited ? 41 : 1), column_start:Number(params.get('column')) || (cited ? 21 : 1),
      total_rows:80,total_columns:40,row_limit:40,column_limit:20,columns:cited ? ['U','V'] : ['A','B'],
      rows:[['42',null]],citation_range:cited ? 'U41:V41' : null
    });
  }
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
    const source = {filename:'same.pdf',knowledge_id:knowledgeId,document_id:'doc',page_number:'7'};
    const trace = {type:'trace',rag_trace:{retrieved_chunks:[
      {...source,text:'来源'}, {...source,text:'同一页的另一段'},
      {filename:'legacy.pdf',page_number:'未知',text:'旧会话片段'},
      {filename:'note.TXT',knowledge_id:knowledgeId,document_id:'text',text:'中文'},
      {filename:'guide.MD',knowledge_id:knowledgeId,document_id:'markdown',text:'文档'},
      {filename:'old.DOC',knowledge_id:knowledgeId,document_id:'old-word',text:'旧 Word 来源'},
      {filename:'new.docx',knowledge_id:knowledgeId,document_id:'new-word',page_number:3,text:'Word 新引用'},
      {filename:'old-reference.pdf',knowledge_id:knowledgeId,document_id:'old-pdf',text:'旧 PDF 来源'},
      {filename:'inventory.XLS',knowledge_id:knowledgeId,document_id:'old-sheet',page_number:4,text:'表格来源'}
    ]}};
    const bytes = new TextEncoder().encode('data: {"type":"rag_step","step":{"label":"检索"}}\r\n\r\ndata: {"type":"content","content":"真实协议"}\r\n\r\ndata: ' + JSON.stringify(trace) + '\r\n\r\ndata: [DONE]\r\n\r\n');
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
  assert.ok(requests.some(item=>item.url==='/documents'&&item.method==='GET'));
  app.searchQuery = '测试检索'; await app.runSearch();
  assert.equal(JSON.parse(requests.find(item=>item.url==='/documents/search').body).knowledgeId ?? null,null);
  const [prefix, match] = app.readingRows[0].snippet;
  assert.equal(prefix.text, '…记可见。接下来是');
  assert.equal(match.text, '测试检索');
  assert.equal(match.match, true);
  for (const [documentId, filename, expected] of [
    ['text','note.TXT','中文'], ['utf16','unicode.txt','中文'],
    ['utf32le','unicode-le.txt','中😀'], ['utf32be','unicode-be.txt','中😀'], ['markdown','guide.MD','# 文档']
  ]) {
    await app.openPreview({key:documentId,documentId,knowledgeId,filename},{detail:1}); await tick();
    assert.equal(app.preview.text,expected);
    assert.equal(app.preview.hasContent,true);
    assert.equal(app.preview.pageCount,0);
    assert.equal(requests.at(-1).headers.get('Authorization'),'Bearer fixture-token');
  }
  await app.selectKnowledge(knowledgeId); await tick();
  assert.equal(JSON.parse(requests.filter(item=>item.url==='/documents/search').at(-1).body).knowledgeId,knowledgeId);
  app.clearSearchInput();
  const file = new Blob(['test']); file.name = 'same.pdf';
  await app.uploadDocument({target:{files:[file],value:'selected'}});
  assert.ok(requests.some(item=>item.url===`/knowledges/${knowledgeId}/documents`&&item.method==='POST'));
  await app.documentAction(app.documents[0],'retry');
  await app.documentAction(app.documents[0],'delete');
  assert.ok(requests.some(item=>item.url.endsWith('/doc/retry')&&item.method==='POST'));
  const textFile = new Blob(['2025 v2.0']); textFile.name = 'new.txt';
  const uploadCount = requests.filter(item=>item.method==='POST'&&item.url.endsWith('/documents')).length;
  await app.uploadDocument({target:{files:[textFile],value:'selected'}});
  assert.equal(requests.filter(item=>item.method==='POST'&&item.url.endsWith('/documents')).length,uploadCount+1);
  const markdownFile = new Blob(['# 文档']); markdownFile.name = 'guide.MD';
  await app.uploadDocument({target:{files:[markdownFile],value:'selected'}});
  assert.equal(requests.filter(item=>item.method==='POST'&&item.url.endsWith('/documents')).length,uploadCount+2);
  await app.documentAction(app.documents[0],'reprocess');
  assert.ok(requests.some(item=>item.url.endsWith('/doc/reprocess')&&item.method==='POST'));
  await app.viewDocumentPage(app.documents[0],2);
  assert.ok(requests.some(item=>item.url.endsWith('/doc/pages/2')));
  assert.ok(app.pageReview.data);
  assert.ok(requests.some(item=>item.url.endsWith('/doc')&&item.method==='DELETE'));
  app.openTryWithKnowledge(knowledgeId); await tick();
  app.chatInput = '测试问题'; await app.sendChat();
  const chatRequest = requests.find(item=>item.url==='/chat/stream');
  assert.deepEqual(JSON.parse(chatRequest.body).knowledge_ids,[knowledgeId]);
  assert.equal(app.messages[1].text,'真实协议');
  assert.equal(app.messages[1].steps[0].label,'检索');
  assert.equal(app.messages[1].trace.retrieved_chunks[0].filename,'same.pdf');
  const previewPaths = () => requests.filter(item=>item.url.includes('/preview/'));
  const messageId = app.messages[1].id;
  assert.equal(previewPaths().length,0);
  app.chatInput = '草稿';
  await app.toggleChatSources(messageId); await tick();
  assert.equal(app.chatSources.length,8);
  assert.equal(app.chatSource.passages.join('|'),'来源|同一页的另一段');
  assert.equal(app.chatPreview.page,8);
  assert.equal(app.chatPreview.pageCount,12);
  assert.equal(previewPaths()[0].url,`/knowledges/${knowledgeId}/documents/doc/preview/8`);
  assert.equal(previewPaths()[0].headers.get('Authorization'),'Bearer fixture-token');
  assert.deepEqual(JSON.parse(previewPaths()[0].body),{query:''});
  await app.loadChatPreview(9);
  await app.closeChatSources();
  assert.equal(app.chatPreview.url,'');
  assert.equal(app.chatInput,'草稿');
  await app.toggleChatSources(messageId); await tick();
  assert.equal(app.chatPreview.page,9);
  app.selectChatSource(app.chatSources[1]); await tick();
  assert.equal(app.chatSource.pageNumber,null);
  assert.equal(app.chatSource.passages[0],'旧会话片段');
  assert.equal(app.hasChatOriginal,false);
  assert.equal(previewPaths().length,3);
  app.selectChatSource(app.chatSources[2]); await tick();
  assert.equal(app.chatPreview.text,'中文');
  app.selectChatSource(app.chatSources[3]); await tick();
  assert.equal(app.chatPreview.text,'# 文档');
  assert.equal(app.chatPreview.hasContent,true);
  app.selectChatSource(app.chatSources[4]); await tick();
  assert.equal(app.chatPreview.page,1);
  assert.equal(previewPaths().at(-1).url, `/knowledges/${knowledgeId}/documents/old-word/preview/1`);
  app.selectChatSource(app.chatSources[5]); await tick();
  assert.equal(app.chatPreview.page,4);
  assert.equal(previewPaths().at(-1).url, `/knowledges/${knowledgeId}/documents/new-word/preview/4`);
  app.selectChatSource(app.chatSources[6]); await tick();
  assert.equal(app.hasChatOriginal,true);
  assert.equal(app.chatPreview.hasContent,false);
  assert.equal(previewPaths().length,5);
  app.selectChatSource(app.chatSources[7]); await tick();
  assert.ok(requests.at(-1).url.endsWith('/old-sheet/spreadsheet-preview?citation_page=5'));
  assert.equal(app.chatPreview.workbook.row_start,41);
  assert.equal(app.chatPreview.workbook.column_start,21);
  assert.equal(app.chatPreview.citation.range,'U41:V41');
  const selectedSheet = {sheet:'用途 # 数据',row:1,column:21};
  await app.loadChatPreview(app.chatPreview.page, 0, selectedSheet);
  assert.deepEqual(Object.fromEntries(new URL(requests.at(-1).url,location.origin).searchParams),{
    sheet:selectedSheet.sheet,row:'1',column:'21'
  });
  assert.equal(app.chatPreview.workbook.sheet,selectedSheet.sheet);
  assert.equal(app.chatPreview.citation.range,'U41:V41');
  await app.closeChatSources();
  assert.equal(app.chatPreview.hasContent,false);
  await app.toggleChatSources(messageId); await tick();
  assert.equal(app.chatPreview.workbook.column_start,21);
  assert.ok(requests.at(-1).url.endsWith('row=1&column=21'));
  await app.closeChatSources();
  const chatPageRequests = requests.length;
  await app.runTool(); assert.equal(requests.length,chatPageRequests);
  app.go('/services/knowledge'); await tick();
  assert.equal(app.chatSourcesOpen,false);
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
  const wordSource = {key:'new-word',documentId:'new-word',knowledgeId,filename:'new.docx',pageNumber:3,query:'查询'};
  await app.openPreview(wordSource,{detail:1}); await tick();
  assert.equal(app.preview.page,4);
  assert.equal(app.preview.pageCount,12);
  assert.equal(previewPaths().at(-1).url, `/knowledges/${knowledgeId}/documents/new-word/preview/4`);
  assert.deepEqual(JSON.parse(previewPaths().at(-1).body),{query:'查询'});
  await app.loadPreview(5);
  assert.equal(app.preview.page,5);
  const sheetSource = {key:'new-sheet',documentId:'new-sheet',knowledgeId,filename:'inventory.xlsx'};
  await app.openPreview(sheetSource,{detail:1}); await tick();
  assert.ok(requests.at(-1).url.endsWith('/new-sheet/spreadsheet-preview'));
  assert.equal(app.preview.workbook.sheet,'库存 & 采购');
  assert.equal(app.preview.workbook.rows[0][0],'42');
  assert.equal(app.preview.citation,null);
  responder = async()=>json({detail:'expired'},401); await app.loadKnowledges();
  assert.equal(app.isAuthenticated,false); assert.equal(app.knowledges.length,0); assert.equal(app.authOpen,true);
  unmount();
  console.log('PASS: frontend API contracts, login continuity, files, scope, chunked SSE, sources, tools, history, credentials, admin, theme, stale responses, 401 cleanup.');
})().catch(error=>{unmount?.();console.error(error);process.exitCode=1;});
