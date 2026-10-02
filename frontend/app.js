/* Vue 3 + Element Plus. Business data comes exclusively from the existing API. */
(() => {
  'use strict';
  const {createApp, ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick} = Vue;
  const {ElMessage, ElMessageBox} = ElementPlus;
  const routes = ['/', '/services/knowledge', '/workspace/knowledges', '/try', '/account', '/admin'];
  const titles = {'/':'MCP 服务广场', '/services/knowledge':'知识库检索 MCP', '/workspace/knowledges':'我的知识库', '/try':'试一下', '/account':'个人管理', '/admin':'管理页面'};
  const routePath = () => routes.includes(location.pathname.replace(/\/$/, '') || '/') ? location.pathname.replace(/\/$/, '') || '/' : '/';
  const storage = {get(key) {try {return localStorage.getItem(key);} catch {return null;}}, set(key, value) {try {value ? localStorage.setItem(key, value) : localStorage.removeItem(key);} catch {}}};
  const newSessionId = () => 'session_' + crypto.randomUUID();
  function tokenizeConfig(source, language) {
    const pattern = /#[^\n]*|"(?:\\.|[^"\\])*"|\[[^\]\n]+\]|\b[A-Za-z_][\w.-]*\b|[{}\[\]:=,]/g;
    const tokens = []; let cursor = 0;
    for (const match of source.matchAll(pattern)) {
      if (match.index > cursor) tokens.push({text:source.slice(cursor, match.index)});
      const text = match[0];
      const kind = text.startsWith('#') ? 'comment' : text.startsWith('"') ? (language === 'JSON' && /^\s*:/.test(source.slice(match.index + text.length)) ? 'key' : 'string') : text.startsWith('[') && text.length > 1 ? 'section' : /^[A-Za-z_]/.test(text) ? 'key' : 'punctuation';
      tokens.push({text, kind}); cursor = match.index + text.length;
    }
    if (cursor < source.length) tokens.push({text:source.slice(cursor)});
    return tokens;
  }
  const app = createApp({
    setup() {
      const page = ref(routePath()), mobileNav = ref(false), token = ref(storage.get('accessToken') || '');
      const currentUser = ref(null), authChecking = ref(!!token.value);
      const role = computed(() => currentUser.value?.role || 'guest');
      const isAuthenticated = computed(() => !!token.value && !!currentUser.value);
      const isAdmin = computed(() => role.value === 'admin');
      const displayName = computed(() => currentUser.value?.username || '');
      const pageTitle = computed(() => titles[page.value]);
      const workspaceLinks = [{path:'/workspace/knowledges',label:'我的知识库'}, {path:'/try',label:'试一下'}, {path:'/account',label:'个人管理'}];
      const isExplore = computed(() => page.value === '/' || page.value === '/services/knowledge');
      const isWorkspace = computed(() => workspaceLinks.some(link => link.path === page.value));
      const isDark = ref(document.documentElement.classList.contains('dark'));
      const motionQuery = matchMedia('(prefers-reduced-motion: reduce)'), systemTheme = matchMedia('(prefers-color-scheme: dark)');
      const brandAnimating = ref(false), brandWords = [{initial:'M',suffix:'eow',offset:0},{initial:'C',suffix:'onnect',offset:3},{initial:'P',suffix:'latform',offset:9}];
      let brandPlayed = false, brandTimer;
      try {brandPlayed = sessionStorage.getItem('mcp-brand-seen-v02') === '1';} catch {}
      function playBrand() {
        if (page.value !== '/' || brandPlayed) return;
        brandPlayed = true; try {sessionStorage.setItem('mcp-brand-seen-v02', '1');} catch {}
        if (motionQuery.matches) return;
        brandAnimating.value = true; brandTimer = setTimeout(() => {brandAnimating.value = false;}, 1100);
      }
      function toggleTheme() {isDark.value = !isDark.value; document.documentElement.classList.toggle('dark', isDark.value); storage.set('mcp-theme', isDark.value ? 'dark' : 'light');}
      const onSystemTheme = event => {if (!storage.get('mcp-theme')) {isDark.value = event.matches; document.documentElement.classList.toggle('dark', event.matches);}};
      const onMotion = event => {if (event.matches) brandAnimating.value = false;};
      systemTheme.addEventListener('change', onSystemTheme); motionQuery.addEventListener('change', onMotion);

      const connectionClient = ref('codex'), servicePanels = ref([]);
      const connectionEndpoint = 'https://fyism.cn/mcp/knowledge';
      const connectionNote = 'Streamable HTTP · MCP_API_KEY 为个人 API Key 环境变量';
      const connectionExamples = computed(() => [
        {id:'codex',label:'Codex',language:'TOML',file:'~/.codex/config.toml',source:`[mcp_servers.meowconnect_knowledge]\nurl = ${JSON.stringify(connectionEndpoint)}\nbearer_token_env_var = "MCP_API_KEY"`},
        {id:'claude',label:'Claude Code',language:'JSON',file:'.mcp.json',source:JSON.stringify({mcpServers:{meowconnect_knowledge:{type:'http',url:connectionEndpoint,headers:{Authorization:'Bearer ${MCP_API_KEY}'}}}},null,2)}
      ].map(example => ({...example,tokens:tokenizeConfig(example.source,example.language)})));

      let identityEpoch = 0, documentEpoch = 0, knowledgeEpoch = 0, resultEpoch = 0, sessionEpoch = 0;
      const authOpen = ref(false), authMode = ref('login'), authLoading = ref(false), authError = ref('');
      const authForm = reactive({username:'',password:'',invite:''}), authFormRef = ref(null), usernameInput = ref(null);
      const authRules = {username:[{required:true,whitespace:true,message:'请输入用户名',trigger:'blur'}],password:[{required:true,message:'请输入密码',trigger:'blur'}],invite:[{required:true,whitespace:true,message:'请输入邀请码',trigger:'blur'}]};
      const knowledges = ref([]), selectedKnowledgeId = ref(''), selectedKnowledgeIds = ref([]), knowledgeLoading = ref(false), knowledgeError = ref('');
      const documents = ref([]), documentsLoading = ref(false), documentsError = ref(''), documentBusyId = ref('');
      const knowledgeSettings = ref({allowed_extensions:[],max_upload_bytes:0,max_retrieval_results:20});
      const createOpen = ref(false), editOpen = ref(false), knowledgeName = ref(''), knowledgeDescription = ref(''), knowledgeEdit = reactive({name:'',description:''});
      const fileInput = ref(null), replaceInput = ref(null), replacingDocumentId = ref(''), isUploading = ref(false);
      const selectedKnowledge = computed(() => knowledges.value.find(kb => kb.id === selectedKnowledgeId.value));
      const acceptedExtensions = computed(() => knowledgeSettings.value.allowed_extensions.join(','));
      const uploadLimitLabel = computed(() => knowledgeSettings.value.max_upload_bytes ? `${Math.round(knowledgeSettings.value.max_upload_bytes / 1048576)} MB` : '以服务端限制为准');
      const scopeLabel = computed(() => selectedKnowledgeIds.value.length ? scopeNames(selectedKnowledgeIds.value) : '未选择知识库');
      let documentTimer = null, scopeInitialized = false;
      const toolName = ref('retrieve'), toolQuery = ref(''), topK = ref(5), toolLoading = ref(false), toolResult = ref(null), toolError = ref('');
      const messages = ref([]), chatInput = ref(''), chatLoading = ref(false), chatLog = ref(null), isComposing = ref(false);
      const sessions = ref([]), sessionsOpen = ref(false), sessionsLoading = ref(false), sessionsError = ref('');
      let sessionId = newSessionId(), streamController = null;
      const keys = ref([]), keyName = ref(''), newKey = ref(''), keysLoading = ref(false), keyBusy = ref(false), keyError = ref('');
      const invites = ref([]), newInvite = ref(''), invitesLoading = ref(false), inviteBusy = ref(false), inviteError = ref('');
      const invitationForm = reactive({max_uses:1,expires_at:''});
      const operations = ref(null), operationsLoading = ref(false), operationsError = ref('');

      function errorMessage(response, data, fallback) {
        let message = typeof data.detail === 'string' ? data.detail : Array.isArray(data.detail) ? data.detail.map(item => item.msg).join('；') : fallback;
        if (response.status === 429) message += `，请${response.headers.get('Retry-After') ? response.headers.get('Retry-After') + ' 秒后' : '稍后'}重试`;
        return message;
      }
      async function request(path, options = {}, authenticated = true) {
        const epoch = identityEpoch;
        const headers = new Headers(options.headers || {});
        if (authenticated && token.value) headers.set('Authorization', `Bearer ${token.value}`);
        const response = await fetch(path, {...options, headers});
        if (authenticated && epoch !== identityEpoch) throw new DOMException('账号已切换', 'AbortError');
        if (authenticated && response.status === 401) {logout(false); authOpen.value = true; throw new Error('登录已过期，请重新登录');}
        if (!response.ok) {const data = await response.json().catch(() => ({})); throw new Error(errorMessage(response, data, '请求失败'));}
        return response;
      }
      async function jsonRequest(path, options = {}, authenticated = true) {return (await request(path, options, authenticated)).json();}
      const jsonBody = (method, value) => ({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
      function report(error) {if (error.name !== 'AbortError') ElMessage.error(error.message);}
      async function confirmAction(message, title) {try {await ElMessageBox.confirm(message,title,{confirmButtonText:'确认',cancelButtonText:'取消',type:'warning',autofocus:false}); return true;} catch {return false;}}
      async function copyText(value) {try {await navigator.clipboard.writeText(value); ElMessage.success('已复制');} catch {ElMessage.warning('复制失败，请选中文本手动复制');}}
      function resetAuth() {authForm.password = ''; authForm.invite = ''; authError.value = ''; authFormRef.value?.clearValidate();}
      function openAuth(mode = 'login') {resetAuth(); authMode.value = mode; authOpen.value = true;}
      function switchAuth() {authMode.value = authMode.value === 'login' ? 'register' : 'login'; resetAuth();}
      function focusAuth() {usernameInput.value?.focus();}
      async function submitAuth() {
        if (authLoading.value) return;
        try {await authFormRef.value.validate();} catch {return;}
        authLoading.value = true; authError.value = '';
        try {
          const payload = {username:authForm.username.trim(),password:authForm.password};
          if (authMode.value === 'register') payload.invite_code = authForm.invite.trim();
          const data = await jsonRequest(authMode.value === 'login' ? '/auth/login' : '/auth/register',jsonBody('POST',payload),false);
          identityEpoch++; token.value = data.access_token; storage.set('accessToken',data.access_token);
          currentUser.value = {username:data.username,role:data.role}; authOpen.value = false; resetAuth();
          await loadPageData();
        } catch (error) {authError.value = error.message;} finally {authLoading.value = false;}
      }
      function stopDocumentPolling() {clearTimeout(documentTimer); documentTimer = null;}
      function stopChat() {streamController?.abort();}
      function clearToolResults() {resultEpoch++; toolResult.value = null; toolError.value = ''; toolLoading.value = false;}
      function logout(navigateHome = true) {
        identityEpoch++; knowledgeEpoch++; documentEpoch++; sessionEpoch++; stopChat(); stopDocumentPolling(); clearToolResults();
        token.value = ''; storage.set('accessToken',''); currentUser.value = null;
        newKey.value = ''; newInvite.value = ''; keys.value = []; invites.value = []; operations.value = null;
        knowledges.value = []; documents.value = []; selectedKnowledgeId.value = ''; selectedKnowledgeIds.value = []; scopeInitialized = false;
        messages.value = []; sessions.value = []; sessionId = newSessionId(); authChecking.value = false;
        knowledgeLoading.value = false; documentsLoading.value = false; keysLoading.value = false; invitesLoading.value = false; sessionsLoading.value = false; operationsLoading.value = false;
        keyBusy.value = false; inviteBusy.value = false; isUploading.value = false; documentBusyId.value = '';
        createOpen.value = false; editOpen.value = false; sessionsOpen.value = false;
        knowledgeError.value = ''; documentsError.value = ''; keyError.value = ''; inviteError.value = ''; operationsError.value = '';
        if (navigateHome) go('/');
      }
      function accountCommand(command) {command === 'account' ? go('/account') : logout();}
      function go(path) {
        mobileNav.value = false; if (!routes.includes(path) || page.value === path) return;
        history.pushState({},'',path); page.value = path; window.scrollTo({top:0,behavior:'instant'});
      }
      const onPopState = () => {page.value = routePath(); mobileNav.value = false;};
      async function loadPageData() {
        if (!isAuthenticated.value) return;
        if (['/workspace/knowledges', '/try', '/services/knowledge'].includes(page.value)) await loadKnowledges();
        else if (page.value === '/account') await loadKeys();
        else if (page.value === '/admin' && isAdmin.value) await Promise.all([loadInvites(),loadOperations()]);
      }

      async function loadKnowledges() {
        if (!isAuthenticated.value) return;
        const epoch = identityEpoch, call = ++knowledgeEpoch; knowledgeLoading.value = true; knowledgeError.value = '';
        try {
          if (!knowledgeSettings.value.max_upload_bytes) knowledgeSettings.value = await jsonRequest('/knowledge-settings',{},false);
          const data = await jsonRequest('/knowledges');
          if (epoch !== identityEpoch || call !== knowledgeEpoch) return;
          knowledges.value = data.knowledges || [];
          const owned = new Set(knowledges.value.map(kb => kb.id));
          if (!scopeInitialized) {
            const requested = new URLSearchParams(location.search).get('knowledge');
            selectedKnowledgeIds.value = requested && owned.has(requested) ? [requested] : knowledges.value.filter(kb => kb.has_ready_documents).slice(0,20).map(kb => kb.id);
            scopeInitialized = true;
          } else selectedKnowledgeIds.value = selectedKnowledgeIds.value.filter(id => owned.has(id));
          if (!owned.has(selectedKnowledgeId.value)) selectedKnowledgeId.value = knowledges.value[0]?.id || '';
          topK.value = Math.min(topK.value, knowledgeSettings.value.max_retrieval_results || 20);
          if (page.value === '/workspace/knowledges') await loadDocuments();
        } catch (error) {if (epoch === identityEpoch && call === knowledgeEpoch && error.name !== 'AbortError') knowledgeError.value = error.message;}
        finally {if (epoch === identityEpoch && call === knowledgeEpoch) knowledgeLoading.value = false;}
      }
      function selectKnowledge(id) {selectedKnowledgeId.value = id; documents.value = []; documentsError.value = ''; stopDocumentPolling(); loadDocuments();}
      async function loadDocuments() {
        const id = selectedKnowledgeId.value, epoch = identityEpoch, call = ++documentEpoch; stopDocumentPolling();
        if (!id || !isAuthenticated.value) {documents.value = []; documentsLoading.value = false; return;}
        documentsLoading.value = true; documentsError.value = '';
        try {
          const data = await jsonRequest(`/knowledges/${encodeURIComponent(id)}/documents`);
          if (epoch !== identityEpoch || call !== documentEpoch || id !== selectedKnowledgeId.value || page.value !== '/workspace/knowledges') return;
          documents.value = data.documents || [];
          const knowledge = selectedKnowledge.value;
          if (knowledge) {knowledge.ready_document_count = documents.value.filter(doc => doc.status === 'ready').length; knowledge.has_ready_documents = knowledge.ready_document_count > 0;}
          if (documents.value.some(doc => ['pending','processing','replacing','deleting'].includes(doc.status))) documentTimer = setTimeout(loadDocuments,2500);
        } catch (error) {if (epoch === identityEpoch && call === documentEpoch && error.name !== 'AbortError') documentsError.value = error.message;}
        finally {if (epoch === identityEpoch && call === documentEpoch) documentsLoading.value = false;}
      }
      async function createKnowledge() {
        if (!knowledgeName.value.trim() || knowledgeLoading.value) return;
        const epoch = identityEpoch; knowledgeLoading.value = true;
        try {
          const data = await jsonRequest('/knowledges',jsonBody('POST',{name:knowledgeName.value.trim(),description:knowledgeDescription.value.trim()}));
          if (epoch !== identityEpoch) return;
          knowledges.value.unshift(data); selectedKnowledgeId.value = data.id; createOpen.value = false; knowledgeName.value = ''; knowledgeDescription.value = '';
          await loadDocuments(); ElMessage.success('知识库已创建');
        } catch (error) {report(error);} finally {if (epoch === identityEpoch) knowledgeLoading.value = false;}
      }
      function openKnowledgeEdit() {Object.assign(knowledgeEdit,{name:selectedKnowledge.value?.name || '',description:selectedKnowledge.value?.description || ''}); editOpen.value = true;}
      async function updateKnowledge() {
        const id = selectedKnowledgeId.value, epoch = identityEpoch;
        if (!id || !knowledgeEdit.name.trim() || knowledgeLoading.value) return;
        knowledgeLoading.value = true;
        try {const data = await jsonRequest(`/knowledges/${encodeURIComponent(id)}`,jsonBody('PATCH',{name:knowledgeEdit.name.trim(),description:knowledgeEdit.description.trim()})); if (epoch !== identityEpoch) return; knowledges.value = knowledges.value.map(kb => kb.id === id ? data : kb); editOpen.value = false; ElMessage.success('已保存');}
        catch (error) {report(error);} finally {if (epoch === identityEpoch) knowledgeLoading.value = false;}
      }
      async function deleteKnowledge() {
        const id = selectedKnowledgeId.value, epoch = identityEpoch;
        if (!id || !await confirmAction(`删除“${selectedKnowledge.value.name}”及其文件、索引？此操作无法恢复。`,'删除知识库') || epoch !== identityEpoch) return;
        try {await jsonRequest(`/knowledges/${encodeURIComponent(id)}`,{method:'DELETE'}); if (epoch !== identityEpoch) return; selectedKnowledgeIds.value = selectedKnowledgeIds.value.filter(value => value !== id); await loadKnowledges(); ElMessage.success('知识库已删除');} catch (error) {report(error);}
      }
      function validateFile(file) {
        const ext = '.' + (file.name.split('.').pop() || '').toLowerCase();
        if (knowledgeSettings.value.allowed_extensions.length && !knowledgeSettings.value.allowed_extensions.includes(ext)) return `仅支持 ${knowledgeSettings.value.allowed_extensions.join('、')} 文件`;
        if (knowledgeSettings.value.max_upload_bytes && file.size > knowledgeSettings.value.max_upload_bytes) return `文件不能超过 ${uploadLimitLabel.value}`;
        return '';
      }
      async function uploadDocument(event) {
        const file = event.target.files?.[0], id = selectedKnowledgeId.value, epoch = identityEpoch; event.target.value = '';
        if (!file || !id || isUploading.value) return;
        const validationError = validateFile(file); if (validationError) {ElMessage.warning(validationError); return;}
        isUploading.value = true;
        try {const body = new FormData(); body.append('file',file); await jsonRequest(`/knowledges/${encodeURIComponent(id)}/documents`,{method:'POST',body}); if (epoch !== identityEpoch) return; if (id === selectedKnowledgeId.value) await loadDocuments(); ElMessage.success('已上传，处理状态将自动更新');}
        catch (error) {report(error);} finally {if (epoch === identityEpoch) isUploading.value = false;}
      }
      function chooseReplacement(doc) {replacingDocumentId.value = doc.id; replaceInput.value?.click();}
      async function replaceDocument(event) {
        const file = event.target.files?.[0], id = selectedKnowledgeId.value, documentId = replacingDocumentId.value, epoch = identityEpoch; event.target.value = ''; replacingDocumentId.value = '';
        if (!file || !documentId || !id) return;
        const validationError = validateFile(file); if (validationError) {ElMessage.warning(validationError); return;}
        if (!await confirmAction(`用“${file.name}”替换此文档？旧内容会立即退出检索。`,'替换文档') || epoch !== identityEpoch) return;
        documentBusyId.value = documentId;
        try {const body = new FormData(); body.append('file',file); await jsonRequest(`/knowledges/${encodeURIComponent(id)}/documents/${encodeURIComponent(documentId)}/content`,{method:'PUT',body}); if (epoch === identityEpoch && id === selectedKnowledgeId.value) await loadDocuments();}
        catch (error) {report(error);} finally {if (epoch === identityEpoch) documentBusyId.value = '';}
      }
      async function documentAction(doc, action) {
        const id = selectedKnowledgeId.value, epoch = identityEpoch;
        if (action === 'delete' && !await confirmAction(`删除“${doc.filename}”？此文件将退出检索。`,'删除文档')) return;
        if (epoch !== identityEpoch) return;
        documentBusyId.value = doc.id;
        try {await jsonRequest(`/knowledges/${encodeURIComponent(id)}/documents/${encodeURIComponent(doc.id)}${action === 'retry' ? '/retry' : ''}`,{method:action === 'retry' ? 'POST' : 'DELETE'}); if (epoch === identityEpoch && id === selectedKnowledgeId.value) await loadDocuments();}
        catch (error) {report(error);} finally {if (epoch === identityEpoch) documentBusyId.value = '';}
      }
      const statusLabel = status => ({pending:'等待处理',processing:'处理中',ready:'可检索',failed:'处理失败',replacing:'替换中',deleting:'删除中'}[status] || status);
      const statusIcon = status => ({ready:'CircleCheck',failed:'CircleClose'}[status] || 'Loading');
      const documentPending = doc => ['pending','processing','replacing','deleting'].includes(doc.status);
      function openTryWithKnowledge(id) {selectedKnowledgeIds.value = [id]; scopeInitialized = true; history.pushState({},'',`/try?knowledge=${encodeURIComponent(id)}`); page.value = '/try';}
      function scopeNames(ids) {return ids == null ? '历史会话未记录范围' : !ids.length ? '空范围' : ids.map(id => knowledges.value.find(kb => kb.id === id)?.name || '已删除的知识库').join('、');}
      async function runTool() {
        if (!isAuthenticated.value) {openAuth(); return;}
        if (page.value !== '/services/knowledge') return;
        if (toolLoading.value) return;
        const name = toolName.value, scope = [...selectedKnowledgeIds.value], query = toolQuery.value.trim();
        if (name === 'retrieve' && (!query || !scope.length)) {toolError.value = '请输入问题并选择至少一个知识库'; return;}
        const epoch = identityEpoch, call = ++resultEpoch; toolLoading.value = true; toolError.value = ''; toolResult.value = null;
        try {const data = await jsonRequest(`/tools/debug/${name}`,jsonBody('POST',name === 'retrieve' ? {query,knowledgeIds:scope,topK:topK.value} : {limit:50})); if (epoch === identityEpoch && call === resultEpoch && page.value === '/services/knowledge') toolResult.value = data;}
        catch (error) {if (epoch === identityEpoch && call === resultEpoch && error.name !== 'AbortError') toolError.value = error.message;}
        finally {if (epoch === identityEpoch && call === resultEpoch) toolLoading.value = false;}
      }
      function parseMarkdown(text) {return DOMPurify.sanitize(marked.parse(text || '',{breaks:true,gfm:true}),{USE_PROFILES:{html:true},FORBID_TAGS:['style','iframe'],FORBID_ATTR:['style']});}
      function traceFacts(trace) {
        if (!trace) return [];
        const fields = {
          tool_name:'工具', retrieval_stage:'检索阶段', retrieval_mode:'检索模式',
          grade_score:'相关性评分', grade_route:'评分决策', rewrite_needed:'需要重写',
          rewrite_strategy:'重写策略', rewrite_query:'重写查询', candidate_k:'候选数',
          leaf_retrieve_level:'召回层级', auto_merge_enabled:'启用合并',
          auto_merge_applied:'执行合并', auto_merge_threshold:'合并阈值',
          auto_merge_replaced_chunks:'替换片段数', auto_merge_steps:'合并轮次',
          rerank_enabled:'启用重排', rerank_applied:'执行重排', rerank_model:'重排模型',
          rerank_error:'重排状态', expansion_type:'扩展策略', step_back_question:'退步问题',
          expanded_query:'扩展查询', hypothetical_doc:'假设文档'
        };
        return Object.entries(fields).filter(([key]) => trace[key] !== null && trace[key] !== undefined && trace[key] !== '')
          .map(([key,label]) => ({label,value:typeof trace[key] === 'boolean' ? trace[key] ? '是' : '否' : trace[key]}));
      }
      function traceGroups(trace) {
        if (!trace) return [];
        return [
          {label:'检索来源',items:trace.retrieved_chunks},
          {label:'初次检索',items:trace.initial_retrieved_chunks},
          {label:'重写后检索',items:trace.expanded_retrieved_chunks}
        ].filter(group => group.items?.length);
      }
      async function scrollChat() {await nextTick(); if (chatLog.value) chatLog.value.scrollTop = chatLog.value.scrollHeight;}
      async function sendChat() {
        const text = chatInput.value.trim(); if (!text || chatLoading.value || isComposing.value || !isAuthenticated.value) return;
        const epoch = identityEpoch, scope = [...selectedKnowledgeIds.value], id = sessionId;
        messages.value.push({id:crypto.randomUUID(),author:'user',text,knowledgeIds:scope});
        const bot = reactive({id:crypto.randomUUID(),author:'agent',text:'',thinking:true,steps:[],trace:null,error:'',knowledgeIds:scope}); messages.value.push(bot);
        chatInput.value = ''; chatLoading.value = true; scrollChat();
        const controller = new AbortController(); streamController = controller;
        const applyEvent = event => {
          if (event.type === 'content') {bot.thinking = false; bot.text += event.content || '';}
          else if (event.type === 'trace') bot.trace = event.rag_trace;
          else if (event.type === 'rag_step') bot.steps.push(event.step);
          else if (event.type === 'error') {bot.thinking = false; bot.error = event.content || '对话服务暂时不可用';}
        };
        let reader;
        try {
          const response = await request('/chat/stream',{...jsonBody('POST',{message:text,session_id:id,knowledge_ids:scope}),signal:controller.signal});
          reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
          const consume = final => {
            let match;
            while ((match = /\r?\n\r?\n/.exec(buffer))) {
              const raw = buffer.slice(0,match.index); buffer = buffer.slice(match.index + match[0].length);
              const data = raw.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
              if (data && data !== '[DONE]') {try {applyEvent(JSON.parse(data));} catch {bot.error = '收到无法解析的响应，请重试';}}
            }
            if (final && buffer.trim()) {buffer += '\n\n'; consume(false);}
          };
          while (true) {const {done,value} = await reader.read(); if (epoch !== identityEpoch) break; if (done) {buffer += decoder.decode(); consume(true); break;} buffer += decoder.decode(value,{stream:true}); consume(false); scrollChat();}
        } catch (error) {if (epoch === identityEpoch) bot.error = error.name === 'AbortError' ? '已停止回答' : error.message;}
        finally {reader?.releaseLock(); bot.thinking = false; if (streamController === controller) {chatLoading.value = false; streamController = null;} scrollChat();}
      }
      function handleChatKey(event) {if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && !isComposing.value) {event.preventDefault(); sendChat();}}
      function newChat() {stopChat(); sessionEpoch++; messages.value = []; sessionId = newSessionId(); sessionsOpen.value = false;}
      async function loadSessions() {
        sessionsOpen.value = true; sessionsLoading.value = true; sessionsError.value = ''; const epoch = identityEpoch;
        try {const data = await jsonRequest('/sessions'); if (epoch === identityEpoch) sessions.value = data.sessions || [];}
        catch (error) {if (epoch === identityEpoch && error.name !== 'AbortError') sessionsError.value = error.message;}
        finally {if (epoch === identityEpoch) sessionsLoading.value = false;}
      }
      async function loadSession(item) {
        stopChat(); const epoch = identityEpoch, call = ++sessionEpoch; sessionsLoading.value = true;
        try {const data = await jsonRequest(`/sessions/${encodeURIComponent(item.session_id)}`); if (epoch !== identityEpoch || call !== sessionEpoch || page.value !== '/try') return; sessionId = item.session_id; messages.value = data.messages.map(msg => ({id:crypto.randomUUID(),author:msg.type === 'human' ? 'user' : 'agent',text:msg.content,trace:msg.rag_trace,knowledgeIds:msg.knowledge_ids})); if (Array.isArray(data.last_knowledge_ids)) selectedKnowledgeIds.value = data.last_knowledge_ids.filter(id => knowledges.value.some(kb => kb.id === id)).slice(0,20); sessionsOpen.value = false; scrollChat();}
        catch (error) {if (epoch === identityEpoch && call === sessionEpoch && error.name !== 'AbortError') sessionsError.value = error.message;}
        finally {if (epoch === identityEpoch && call === sessionEpoch) sessionsLoading.value = false;}
      }
      async function deleteSession(item) {
        const epoch = identityEpoch; if (!await confirmAction(`删除会话“${item.title || item.session_id}”？`,'删除会话') || epoch !== identityEpoch) return;
        try {await jsonRequest(`/sessions/${encodeURIComponent(item.session_id)}`,{method:'DELETE'}); if (epoch !== identityEpoch) return; sessions.value = sessions.value.filter(session => session.session_id !== item.session_id); if (sessionId === item.session_id) newChat();} catch (error) {report(error);}
      }

      async function loadKeys() {
        const epoch = identityEpoch; keysLoading.value = true; keyError.value = '';
        try {const data = await jsonRequest('/account/api-keys'); if (epoch === identityEpoch && page.value === '/account') keys.value = data.items || [];}
        catch (error) {if (epoch === identityEpoch && error.name !== 'AbortError') keyError.value = error.message;}
        finally {if (epoch === identityEpoch) keysLoading.value = false;}
      }
      async function createKey() {
        if (!keyName.value.trim() || keyBusy.value) return;
        const epoch = identityEpoch; keyBusy.value = true; keyError.value = ''; newKey.value = '';
        try {const data = await jsonRequest('/account/api-keys',jsonBody('POST',{name:keyName.value.trim()})); if (epoch !== identityEpoch || page.value !== '/account') return; newKey.value = data.key; keyName.value = ''; await loadKeys();}
        catch (error) {if (epoch === identityEpoch && error.name !== 'AbortError') keyError.value = error.message;}
        finally {if (epoch === identityEpoch) keyBusy.value = false;}
      }
      async function revokeKey(item) {
        const epoch = identityEpoch; if (keyBusy.value || item.revokedAt || !await confirmAction(`撤销“${item.name}”？使用它的客户端会立即失去访问权限。`,'撤销 API Key') || epoch !== identityEpoch) return;
        keyBusy.value = true;
        try {await jsonRequest(`/account/api-keys/${encodeURIComponent(item.id)}`,{method:'DELETE'}); if (epoch !== identityEpoch) return; newKey.value = ''; await loadKeys();}
        catch (error) {if (epoch === identityEpoch && error.name !== 'AbortError') keyError.value = error.message;}
        finally {if (epoch === identityEpoch) keyBusy.value = false;}
      }
      const utcDate = value => value ? new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : value + 'Z') : null;
      const formatDate = value => value ? utcDate(value).toLocaleString('zh-CN') : '—';
      const invitationStatus = item => item.revoked_at ? '已撤销' : item.used_count >= item.max_uses ? '已用尽' : item.expires_at && utcDate(item.expires_at) <= new Date() ? '已过期' : '可用';
      async function loadInvites() {
        const epoch = identityEpoch; invitesLoading.value = true; inviteError.value = '';
        try {const data = await jsonRequest('/admin/invitations'); if (epoch === identityEpoch && isAdmin.value && page.value === '/admin') invites.value = data;}
        catch (error) {if (epoch === identityEpoch && error.name !== 'AbortError') inviteError.value = error.message;}
        finally {if (epoch === identityEpoch) invitesLoading.value = false;}
      }
      async function createInvite() {
        if (!isAdmin.value || inviteBusy.value) return;
        const maxUses = Number(invitationForm.max_uses), expiry = invitationForm.expires_at;
        if (!Number.isInteger(maxUses) || maxUses < 1 || maxUses > 100) {inviteError.value = '使用次数须为 1–100 的整数'; return;}
        if (expiry && (!Number.isFinite(new Date(expiry).getTime()) || new Date(expiry) <= new Date())) {inviteError.value = '过期时间必须晚于当前时间'; return;}
        const epoch = identityEpoch; inviteBusy.value = true; inviteError.value = ''; newInvite.value = '';
        try {const data = await jsonRequest('/admin/invitations',jsonBody('POST',{max_uses:maxUses,expires_at:expiry ? new Date(expiry).toISOString() : null})); if (epoch !== identityEpoch || page.value !== '/admin' || !isAdmin.value) return; newInvite.value = data.invite_code; Object.assign(invitationForm,{max_uses:1,expires_at:''}); await loadInvites();}
        catch (error) {if (epoch === identityEpoch && error.name !== 'AbortError') inviteError.value = error.message;}
        finally {if (epoch === identityEpoch) inviteBusy.value = false;}
      }
      async function revokeInvite(item) {
        const epoch = identityEpoch; if (!isAdmin.value || inviteBusy.value || invitationStatus(item) !== '可用' || !await confirmAction('撤销此邀请码？剩余次数将立即失效。','撤销邀请码') || epoch !== identityEpoch) return;
        inviteBusy.value = true;
        try {await jsonRequest(`/admin/invitations/${encodeURIComponent(item.id)}/revoke`,{method:'POST'}); if (epoch !== identityEpoch) return; newInvite.value = ''; await loadInvites();}
        catch (error) {if (epoch === identityEpoch && error.name !== 'AbortError') inviteError.value = error.message;}
        finally {if (epoch === identityEpoch) inviteBusy.value = false;}
      }
      async function loadOperations() {
        const epoch = identityEpoch; operationsLoading.value = true; operationsError.value = '';
        try {const data = await jsonRequest('/admin/operations'); if (epoch === identityEpoch && isAdmin.value && page.value === '/admin') operations.value = data;}
        catch (error) {if (epoch === identityEpoch && error.name !== 'AbortError') operationsError.value = error.message;}
        finally {if (epoch === identityEpoch) operationsLoading.value = false;}
      }
      watch(selectedKnowledgeIds, () => {clearToolResults();}, {deep:true});
      watch(toolName, clearToolResults);
      watch(page, async () => {
        newKey.value = ''; newInvite.value = ''; stopDocumentPolling(); documentEpoch++; clearToolResults(); sessionEpoch++; stopChat();
        createOpen.value = false; editOpen.value = false; sessionsOpen.value = false;
        document.title = pageTitle.value + ' · MeowConnectPlatform'; playBrand(); await loadPageData(); await nextTick();
        document.getElementById('main')?.focus({preventScroll:true});
      });
      onMounted(async () => {
        document.title = pageTitle.value + ' · MeowConnectPlatform'; playBrand(); window.addEventListener('popstate',onPopState);
        if (token.value) {
          try {currentUser.value = await jsonRequest('/auth/me');} catch (error) {if (error.name !== 'AbortError') report(error);}
          finally {authChecking.value = false;}
        }
        await loadPageData();
      });
      onBeforeUnmount(() => {identityEpoch++; stopChat(); stopDocumentPolling(); clearTimeout(brandTimer); window.removeEventListener('popstate',onPopState); systemTheme.removeEventListener('change',onSystemTheme); motionQuery.removeEventListener('change',onMotion);});
      return {
        locale:ElementPlusLocaleZhCn,page,mobileNav,currentUser,authChecking,role,isAuthenticated,isAdmin,displayName,pageTitle,workspaceLinks,isExplore,isWorkspace,isDark,brandAnimating,brandWords,toggleTheme,go,accountCommand,
        connectionClient,servicePanels,connectionEndpoint,connectionNote,connectionExamples,
        authOpen,authMode,authLoading,authError,authForm,authFormRef,authRules,usernameInput,resetAuth,openAuth,switchAuth,focusAuth,submitAuth,logout,
        knowledges,selectedKnowledgeId,selectedKnowledgeIds,selectedKnowledge,knowledgeLoading,knowledgeError,loadKnowledges,selectKnowledge,createOpen,editOpen,knowledgeName,knowledgeDescription,knowledgeEdit,createKnowledge,openKnowledgeEdit,updateKnowledge,deleteKnowledge,
        documents,documentsLoading,documentsError,documentBusyId,loadDocuments,fileInput,replaceInput,isUploading,acceptedExtensions,uploadLimitLabel,uploadDocument,chooseReplacement,replaceDocument,documentAction,statusLabel,statusIcon,documentPending,openTryWithKnowledge,
        scopeLabel,scopeNames,toolName,toolQuery,topK,knowledgeSettings,toolLoading,toolResult,toolError,runTool,messages,chatInput,chatLoading,chatLog,isComposing,sendChat,stopChat,handleChatKey,parseMarkdown,traceFacts,traceGroups,newChat,sessions,sessionsOpen,sessionsLoading,sessionsError,loadSessions,loadSession,deleteSession,
        keys,keyName,newKey,keysLoading,keyBusy,keyError,loadKeys,createKey,revokeKey,invites,newInvite,invitesLoading,inviteBusy,inviteError,invitationForm,loadInvites,createInvite,revokeInvite,invitationStatus,formatDate,operations,operationsLoading,operationsError,loadOperations,copyText
      };
    }
  });
  app.use(ElementPlus);
  app.component('mcp-connection-example',{
    props:{modelValue:String,examples:Array,note:String},
    emits:['update:modelValue','copy'],
    template:'#connection-example-template'
  });
  app.component('mcp-icon',{props:{name:{type:String,required:true},size:{type:Number,default:20}},setup(props) {return () => Vue.h(ElementPlus.ElIcon,{size:props.size,'aria-hidden':'true'},() => Vue.h(ElementPlusIconsVue[props.name] || ElementPlusIconsVue.Document));}});
  app.mount('#app');
})();
