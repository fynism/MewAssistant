/* Vue 3 + Element Plus. Business data comes exclusively from the existing API. */
(() => {
  'use strict';
  const {createApp, ref, shallowRef, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick} = Vue;
  const {ElMessage, ElMessageBox} = ElementPlus;
  const routes = ['/', '/services/knowledge', '/workspace/knowledges', '/try', '/account', '/admin'];
  const titles = {'/':'个人知识库', '/services/knowledge':'客户端接入', '/workspace/knowledges':'个人知识库', '/try':'问资料', '/account':'个人管理', '/admin':'管理页面'};
  const routePath = () => routes.includes(location.pathname.replace(/\/$/, '') || '/') ? location.pathname.replace(/\/$/, '') || '/' : '/';
  const storage = {get(key) {try {return localStorage.getItem(key);} catch {return null;}}, set(key, value) {try {value ? localStorage.setItem(key, value) : localStorage.removeItem(key);} catch {}}};
  const newSessionId = () => 'session_' + crypto.randomUUID();
  const fileExtension = filename => /\.([^.]+)$/.exec(filename || '')?.[1].toLowerCase();
  function previewFormat(filename) {
    const extension = fileExtension(filename);
    if (['pdf','doc','docx'].includes(extension)) return 'pdf';
    if (['xls','xlsx'].includes(extension)) return 'spreadsheet';
    return ['txt','md'].includes(extension) ? extension : null;
  }
  function decodePreviewText(buffer) {
    const bytes = new Uint8Array(buffer);
    const view = new DataView(buffer);
    const bom = view.byteLength >= 4 ? view.getUint32(0) : 0;
    if (bom === 0xfffe0000 || bom === 0x0000feff) {
      // TextDecoder does not support UTF-32, and the LE BOM starts with the UTF-16 BOM.
      const chunks = [], points = [];
      const littleEndian = bom === 0xfffe0000;
      if (view.byteLength % 4) throw new Error('文本编码无法识别，请另存为 UTF-8');
      for (let offset = 4; offset < view.byteLength; offset += 4) {
        const point = view.getUint32(offset, littleEndian);
        if (point > 0x10ffff || (point >= 0xd800 && point <= 0xdfff)) throw new Error('文本编码无法识别，请另存为 UTF-8');
        points.push(point);
        if (points.length === 4096) {
          chunks.push(String.fromCodePoint(...points));
          points.length = 0;
        }
      }
      return chunks.join('') + String.fromCodePoint(...points);
    }
    if (bytes[0] === 0xff && bytes[1] === 0xfe) return new TextDecoder('utf-16le').decode(bytes);
    if (bytes[0] === 0xfe && bytes[1] === 0xff) return new TextDecoder('utf-16be').decode(bytes);
    try {return new TextDecoder('utf-8', {fatal:true}).decode(bytes);}
    catch {return new TextDecoder('gb18030').decode(bytes);}
  }
  function parseMarkdown(text, preview = false) {
    const tags = ['style','iframe'];
    const attrs = ['style'];
    // Opening a document must not request resources referenced in its Markdown.
    if (preview) {
      tags.push('img','picture','audio','video','source','track','object','embed','input','form','link','meta');
      attrs.push('src','srcset','poster','background');
    }
    return DOMPurify.sanitize(marked.parse(text || '', {breaks:true,gfm:true}), {
      USE_PROFILES:{html:true},FORBID_TAGS:tags,FORBID_ATTR:attrs
    });
  }
  function highlightParts(text, query) {
    if (!query) return [{text, match:false}];
    const literal = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return text.split(new RegExp(`(${literal})`, 'giu')).map((text, index) => ({text, match:index % 2 === 1}));
  }
  function searchSnippet(text, query) {
    const compact = (text || '').replace(/\s+/g, ' ').trim();
    const match = compact.toLowerCase().indexOf(query.toLowerCase());
    // Keep the match within the visible line of the results sidebar.
    const start = Math.max(0, match - 8);
    const end = start + Math.max(115, Math.min(query.length + 40, 224));
    return highlightParts((start ? '…' : '') + compact.slice(start, end) + (end < compact.length ? '…' : ''), query);
  }
  function chatPageNumber(value) {
    const page = typeof value === 'string' && value.trim() ? Number(value) : value;
    return Number.isSafeInteger(page) && page >= 0 && page < Number.MAX_SAFE_INTEGER ? page + 1 : null;
  }
  function collectChatSources(chunks) {
    const sources = new Map();
    for (const [index, chunk] of chunks.entries()) {
      if (!chunk || (!chunk.filename && !chunk.document_id && !chunk.text)) continue;
      const {knowledge_id:knowledgeId, document_id:documentId, page_number:rawPage} = chunk;
      const pageNumber = chatPageNumber(rawPage);
      const key = knowledgeId && documentId ? JSON.stringify([knowledgeId,documentId,pageNumber]) : index;
      let source = sources.get(key);
      if (!source) {
        source = {key,knowledgeId,documentId,pageNumber,filename:chunk.filename || '未命名文档',passages:[]};
        sources.set(key,source);
      }
      if (chunk.text && !source.passages.includes(chunk.text)) source.passages.push(chunk.text);
    }
    return [...sources.values()];
  }
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
      const isLibrary = computed(() => page.value === '/' || page.value === '/workspace/knowledges');
      const isDark = ref(document.documentElement.classList.contains('dark'));
      const systemTheme = matchMedia('(prefers-color-scheme: dark)');
      const readerMedia = matchMedia('(max-width: 880px)');
      const compactReader = ref(readerMedia.matches);
      const onReaderWidth = event => {compactReader.value = event.matches;};
      function toggleTheme() {isDark.value = !isDark.value; document.documentElement.classList.toggle('dark', isDark.value); storage.set('mcp-theme', isDark.value ? 'dark' : 'light');}
      const onSystemTheme = event => {if (!storage.get('mcp-theme')) {isDark.value = event.matches; document.documentElement.classList.toggle('dark', event.matches);}};
      systemTheme.addEventListener('change', onSystemTheme);
      readerMedia.addEventListener('change', onReaderWidth);

      const connectionClient = ref('codex'), servicePanels = ref([]);
      const connectionEndpoint = 'https://fyism.cn/mcp/knowledge';
      const helpTopic = ref(''), qualityReview = ref(null);
      const helpTitle = computed(() => ({connection:'客户端接入',tools:'工具说明',upload:'上传说明',scope:'知识库范围'})[helpTopic.value] || '帮助');
      const connectionExamples = computed(() => [
        {id:'codex',label:'Codex',language:'TOML',file:'~/.codex/config.toml',source:`[mcp_servers.meowconnect_knowledge]\nurl = ${JSON.stringify(connectionEndpoint)}\nbearer_token_env_var = "MCP_API_KEY"`},
        {id:'claude',label:'Claude Code',language:'JSON',file:'.mcp.json',source:JSON.stringify({mcpServers:{meowconnect_knowledge:{type:'http',url:connectionEndpoint,headers:{Authorization:'Bearer ${MCP_API_KEY}'}}}},null,2)}
      ].map(example => ({...example,tokens:tokenizeConfig(example.source,example.language)})));

      let identityEpoch = 0, documentEpoch = 0, knowledgeEpoch = 0, resultEpoch = 0, sessionEpoch = 0;
      const authOpen = ref(false), authMode = ref('login'), authLoading = ref(false), authError = ref('');
      const authView = ref('methods');
      const authTitle = computed(() => authMode.value === 'register' ? '邀请注册' : authView.value === 'password' ? '账号密码登录' : '登录');
      const githubEnabled = ref(false), githubLoading = ref(false);
      const authForm = reactive({username:'',password:'',invite:''}), authFormRef = ref(null), usernameInput = ref(null), githubLoginButton = ref(null);
      const authRules = {username:[{required:true,whitespace:true,message:'请输入用户名',trigger:'blur'}],password:[{required:true,message:'请输入密码',trigger:'blur'}],invite:[{required:true,whitespace:true,message:'请输入邀请码',trigger:'blur'}]};
      const knowledges = ref([]), selectedKnowledgeId = ref(''), selectedKnowledgeIds = ref([]), knowledgeLoading = ref(false), knowledgeError = ref('');
      const documents = ref([]), documentsLoading = ref(false), documentsError = ref(''), documentBusyId = ref('');
      const searchQuery = ref(''), submittedQuery = ref(''), searchResult = shallowRef(null), searchLoading = ref(false), searchError = ref('');
      const sidebarPane = ref('results'), sidebarHidden = ref(false);
      const activeSource = shallowRef(null), previewOpen = ref(false), previewZoom = ref(100);
      const downloadLoading = ref(false), searchInput = ref(null), previewTitle = ref(null), readingList = ref(null), previewCanvas = ref(null);
      const preview = useDocumentPreview(previewCanvas);
      let searchController;
      const activeDocument = computed(() => documents.value.find(doc => doc.id === activeSource.value?.documentId));
      const readingRows = computed(() => submittedQuery.value
        ? (searchResult.value?.results || []).map(source => ({...source, key:source.sourceId, query:submittedQuery.value, snippet:searchSnippet(source.text, submittedQuery.value)}))
        : documents.value.map(doc => ({key:doc.id, documentId:doc.id, knowledgeId:doc.knowledge_id,
          knowledgeName:knowledges.value.find(kb => kb.id === doc.knowledge_id)?.name, filename:doc.filename, doc})));
      const pageReview = ref(null), pageReviewLoading = ref(false);
      const knowledgeSettings = ref({allowed_extensions:[],max_upload_bytes:0,max_retrieval_results:20});
      const createOpen = ref(false), editOpen = ref(false), knowledgeName = ref(''), knowledgeDescription = ref(''), knowledgeEdit = reactive({name:'',description:''});
      const fileInput = ref(null), replaceInput = ref(null), replacingDocumentId = ref(''), isUploading = ref(false);
      const selectedKnowledge = computed(() => knowledges.value.find(kb => kb.id === selectedKnowledgeId.value));
      const acceptedExtensions = computed(() => knowledgeSettings.value.allowed_extensions.join(','));
      const uploadLimitLabel = computed(() => knowledgeSettings.value.max_upload_bytes ? `${Math.round(knowledgeSettings.value.max_upload_bytes / 1048576)} MB` : '以服务端限制为准');
      let documentTimer = null, scopeInitialized = false;
      const toolName = ref('retrieve'), toolQuery = ref(''), topK = ref(5), toolLoading = ref(false), toolResult = ref(null), toolError = ref('');
      const messages = ref([]), chatInput = ref(''), chatLoading = ref(false), chatLog = ref(null), isComposing = ref(false);
      const chatSourcesOpen = ref(false), chatSourceView = shallowRef(null), chatSourcesHeading = ref(null);
      const chatSourcePreview = ref(null), chatPreview = useDocumentPreview(chatSourcePreview);
      const chatOriginalLoading = ref(false);
      let chatSourceTrigger = null, chatOriginalController = null, chatPreviewScroll = 0;
      const chatSourcesByMessage = computed(() => new Map(messages.value
        .map(message => [message.id,collectChatSources(message.trace?.retrieved_chunks || [])])));
      const chatSources = computed(() => chatSourcesByMessage.value.get(chatSourceView.value?.messageId) || []);
      const chatSource = computed(() => chatSources.value.find(source => source.key === chatSourceView.value?.sourceKey) || chatSources.value[0]);
      const hasChatOriginal = computed(() => Boolean(chatSource.value?.knowledgeId && chatSource.value?.documentId));
      const chatPreviewFormat = computed(() => {
        if (!hasChatOriginal.value) return null;
        const {filename,pageNumber} = chatSource.value;
        if (fileExtension(filename) === 'pdf' && pageNumber === null) return null;
        return previewFormat(filename);
      });
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
        if (authenticated && response.status === 401) {logout(false); openAuth(); throw new Error('登录已过期，请重新登录');}
        if (!response.ok) {const data = await response.json().catch(() => ({})); throw new Error(errorMessage(response, data, '请求失败'));}
        return response;
      }
      async function jsonRequest(path, options = {}, authenticated = true) {return (await request(path, options, authenticated)).json();}
      const jsonBody = (method, value) => ({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
      function report(error) {if (error.name !== 'AbortError') ElMessage.error(error.message);}
      async function confirmAction(message, title) {try {await ElMessageBox.confirm(message,title,{confirmButtonText:'确认',cancelButtonText:'取消',type:'warning',autofocus:false}); return true;} catch {return false;}}
      async function copyText(value) {try {await navigator.clipboard.writeText(value); ElMessage.success('已复制');} catch {ElMessage.warning('复制失败，请选中文本手动复制');}}
      function resetAuth() {authForm.password = ''; authForm.invite = ''; authError.value = ''; authFormRef.value?.clearValidate();}
      function openAuth(mode = 'login') {resetAuth(); authMode.value = mode; authView.value = mode === 'login' && githubEnabled.value ? 'methods' : 'password'; authOpen.value = true;}
      function switchAuth() {authMode.value = authMode.value === 'login' ? 'register' : 'login'; resetAuth();}
      function focusAuth() {if (authView.value === 'password') usernameInput.value?.focus(); else githubLoginButton.value?.$el?.focus();}
      async function choosePasswordLogin() {
        if (authLoading.value || githubLoading.value) return;
        resetAuth(); authMode.value = 'login'; authView.value = 'password';
        await nextTick(); focusAuth();
      }
      async function showAuthMethods() {
        if (authLoading.value || githubLoading.value) return;
        resetAuth(); authMode.value = 'login'; authView.value = 'methods';
        await nextTick(); focusAuth();
      }
      function acceptLogin(data) {
        logout(false);
        token.value = data.access_token; storage.set('accessToken',data.access_token);
        currentUser.value = {username:data.username,role:data.role}; authOpen.value = false; resetAuth();
      }
      async function startGitHubLogin() {
        if (!githubEnabled.value || githubLoading.value || authLoading.value) return;
        githubLoading.value = true; authError.value = '';
        try {
          const data = await jsonRequest('/auth/github/start',jsonBody('POST',{next:location.pathname + location.search}),false);
          location.assign(data.authorization_url);
        } catch (error) {authError.value = error.message; githubLoading.value = false;}
      }
      async function restoreGitHubLogin() {
        const params = new URLSearchParams(location.search);
        const success = params.get('github') === 'success', failure = params.get('github_error');
        if (!success && !failure) return false;
        params.delete('github'); params.delete('github_error');
        history.replaceState({},'',location.pathname + (params.size ? '?' + params.toString() : '') + (location.hash || ''));
        // The marker is not a credential. The server consumes an HttpOnly, one-use ticket.
        if (success) {
          authChecking.value = true;
          try {acceptLogin(await jsonRequest('/auth/github/complete',jsonBody('POST',{}),false)); return true;}
          catch (error) {authError.value = error.message;}
          finally {authChecking.value = false;}
        } else {
          const reasons = {cancelled:'已取消 GitHub 授权，可以重新尝试',expired:'GitHub 登录已过期，请重新尝试',unavailable:'GitHub 登录暂时不可用，请稍后重试',failed:'GitHub 登录失败，请重新尝试'};
          authError.value = reasons[failure] || 'GitHub 登录失败，请重新尝试';
        }
        authMode.value = 'login'; authView.value = githubEnabled.value ? 'methods' : 'password'; authOpen.value = true; return false;
      }
      async function submitAuth() {
        if (authLoading.value || githubLoading.value) return;
        try {await authFormRef.value.validate();} catch {return;}
        authLoading.value = true; authError.value = '';
        try {
          const payload = {username:authForm.username.trim(),password:authForm.password};
          if (authMode.value === 'register') payload.invite_code = authForm.invite.trim();
          const data = await jsonRequest(authMode.value === 'login' ? '/auth/login' : '/auth/register',jsonBody('POST',payload),false);
          acceptLogin(data);
          await loadPageData();
        } catch (error) {authError.value = error.message;} finally {authLoading.value = false;}
      }
      function stopDocumentPolling() {clearTimeout(documentTimer); documentTimer = null;}
      function stopChat() {streamController?.abort();}
      function clearToolResults() {resultEpoch++; toolResult.value = null; toolError.value = ''; toolLoading.value = false;}
      function logout(navigateHome = true) {
        identityEpoch++; knowledgeEpoch++; documentEpoch++; sessionEpoch++; stopChat(); stopDocumentPolling(); clearToolResults();
        searchQuery.value = ''; clearSearch(); sidebarPane.value = 'results'; sidebarHidden.value = false; downloadLoading.value = false;
        token.value = ''; storage.set('accessToken',''); currentUser.value = null;
        newKey.value = ''; newInvite.value = ''; keys.value = []; invites.value = []; operations.value = null;
        knowledges.value = []; documents.value = []; selectedKnowledgeId.value = ''; selectedKnowledgeIds.value = []; scopeInitialized = false;
        messages.value = []; sessions.value = []; sessionId = newSessionId(); authChecking.value = false;
        chatInput.value = ''; resetChatSources();
        knowledgeLoading.value = false; documentsLoading.value = false; keysLoading.value = false; invitesLoading.value = false; sessionsLoading.value = false; operationsLoading.value = false;
        keyBusy.value = false; inviteBusy.value = false; isUploading.value = false; documentBusyId.value = '';
        createOpen.value = false; editOpen.value = false; sessionsOpen.value = false;
        pageReview.value = null; pageReviewLoading.value = false; qualityReview.value = null; helpTopic.value = '';
        knowledgeError.value = ''; documentsError.value = ''; keyError.value = ''; inviteError.value = ''; operationsError.value = '';
        if (navigateHome) go('/');
      }
      function accountCommand(command) {
        if (command === 'logout') logout();
        else if (command === 'theme') toggleTheme();
        else go('/' + command);
      }
      function go(path) {
        mobileNav.value = false; if (!routes.includes(path) || page.value === path) return;
        history.pushState({},'',path); page.value = path; window.scrollTo({top:0,behavior:'instant'});
      }
      const onPopState = () => {page.value = routePath(); mobileNav.value = false;};
      async function loadPageData() {
        if (!isAuthenticated.value) return;
        if (isLibrary.value || ['/try', '/services/knowledge'].includes(page.value)) await loadKnowledges();
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
          if (!owned.has(selectedKnowledgeId.value)) selectedKnowledgeId.value = '';
          topK.value = Math.min(topK.value, knowledgeSettings.value.max_retrieval_results || 20);
          if (isLibrary.value) await loadDocuments();
        } catch (error) {if (epoch === identityEpoch && call === knowledgeEpoch && error.name !== 'AbortError') knowledgeError.value = error.message;}
        finally {if (epoch === identityEpoch && call === knowledgeEpoch) knowledgeLoading.value = false;}
      }
      function selectKnowledge(id) {
        if (selectedKnowledgeId.value === id) return;
        selectedKnowledgeId.value = id;
        documents.value = [];
        resetPreview();
        const loading = loadDocuments();
        if (submittedQuery.value) runSearch(submittedQuery.value);
        nextTick(() => readingList.value?.scrollTo(0, 0));
        return loading;
      }
      function browseKnowledge(id) {
        sidebarPane.value = 'results';
        selectKnowledge(id);
        nextTick(() => searchInput.value?.focus({preventScroll:true}));
      }
      async function loadDocuments() {
        const id = selectedKnowledgeId.value, epoch = identityEpoch, call = ++documentEpoch; stopDocumentPolling();
        if (!isAuthenticated.value || !isLibrary.value) return;
        documentsLoading.value = true; documentsError.value = '';
        try {
          const data = await jsonRequest(id ? `/knowledges/${encodeURIComponent(id)}/documents` : '/documents');
          if (epoch !== identityEpoch || call !== documentEpoch || id !== selectedKnowledgeId.value || !isLibrary.value) return;
          documents.value = data.documents || [];
          const readyCounts = new Map();
          for (const doc of documents.value) if (doc.status === 'ready') readyCounts.set(doc.knowledge_id, (readyCounts.get(doc.knowledge_id) || 0) + 1);
          for (const kb of knowledges.value) {
            if (!id || id === kb.id) {
              kb.ready_document_count = readyCounts.get(kb.id) || 0;
              kb.has_ready_documents = kb.ready_document_count > 0;
            }
          }
          if (documents.value.some(doc => ['pending','processing','replacing','deleting'].includes(doc.status))) documentTimer = setTimeout(loadDocuments,2500);
        } catch (error) {if (epoch === identityEpoch && call === documentEpoch && error.name !== 'AbortError') documentsError.value = error.message;}
        finally {if (epoch === identityEpoch && call === documentEpoch) documentsLoading.value = false;}
      }
      function clearSearch() {
        searchController?.abort();
        submittedQuery.value = ''; searchResult.value = null; searchError.value = ''; searchLoading.value = false;
        resetPreview();
      }
      function clearSearchInput() {
        searchQuery.value = '';
        clearSearch();
        readingList.value?.scrollTo(0, 0);
        searchInput.value?.focus();
      }
      function guardSearchComposition(event) {
        if (event.isComposing || event.keyCode === 229) event.preventDefault();
      }
      async function runSearch(query = searchQuery.value.trim()) {
        if (!query) {clearSearch(); return;}
        searchController?.abort();
        const controller = new AbortController(); searchController = controller;
        submittedQuery.value = query; searchLoading.value = true; searchError.value = ''; searchResult.value = null;
        resetPreview(); readingList.value?.scrollTo(0, 0);
        try {
          const body = jsonBody('POST', {query, knowledgeId:selectedKnowledgeId.value || null, topK:Math.min(10, knowledgeSettings.value.max_retrieval_results || 20)});
          const data = await jsonRequest('/documents/search', {...body, signal:controller.signal});
          if (!controller.signal.aborted) searchResult.value = data;
        } catch (error) {
          if (!controller.signal.aborted && error.name !== 'AbortError') searchError.value = error.message;
        } finally {
          if (!controller.signal.aborted) searchLoading.value = false;
        }
      }
      function resetPreview() {
        preview.reset();
        activeSource.value = null; previewOpen.value = false; previewZoom.value = 100;
      }
      function sourcePath(source) {
        return `/knowledges/${encodeURIComponent(source.knowledgeId)}/documents/${encodeURIComponent(source.documentId)}`;
      }
      function useDocumentPreview(canvas) {
        let controller, scrollPosition = 0, lastSheetPosition = null;
        const state = reactive({
          url:'', text:null, page:null, pageCount:0, loading:false, error:'',
          workbook:shallowRef(null), citation:null,
          get hasContent() {return Boolean(this.url || this.workbook) || this.text !== null;},
          clear, reset, load, restoreImageScroll
        });
        return state;

        function clear(keepWorkbook = false) {
          controller?.abort();
          controller = null;
          if (state.url) URL.revokeObjectURL(state.url);
          Object.assign(state, {url:'',text:null,error:'',loading:false});
          if (!keepWorkbook) state.workbook = null;
        }
        function reset() {
          clear();
          state.page = null;
          state.pageCount = 0;
          state.citation = null;
          lastSheetPosition = null;
        }
        function restoreImageScroll(event) {
          if (event.target.src === state.url) canvas.value?.scrollTo(0, scrollPosition);
        }
        async function load(source, {pageNumber, scroll = 0, citationPage, sheetPosition = lastSheetPosition}) {
          const format = previewFormat(source?.filename);
          if (!format) return;
          clear(format === 'spreadsheet');
          const requestController = new AbortController();
          controller = requestController;
          const {signal} = requestController;
          state.page = pageNumber;
          state.loading = true;
          scrollPosition = scroll;
          canvas.value?.scrollTo(0, 0);
          const path = sourcePath(source);
          try {
            if (format === 'pdf') {
              const response = await request(`${path}/preview/${pageNumber}`, {
                ...jsonBody('POST', {query:source.query || ''}), signal
              });
              const blob = await response.blob();
              if (signal.aborted) return;
              state.pageCount = Number(response.headers.get('X-PDF-Page-Count')) || 0;
              state.url = URL.createObjectURL(blob);
            } else if (format === 'spreadsheet') {
              lastSheetPosition = sheetPosition;
              const params = new URLSearchParams(sheetPosition || (citationPage ? {citation_page:citationPage} : {}));
              const query = params.size ? '?' + params : '';
              const workbook = await jsonRequest(`${path}/spreadsheet-preview${query}`, {signal});
              if (signal.aborted) return;
              state.workbook = workbook;
              if (workbook.citation_range) state.citation = {sheet:workbook.sheet,range:workbook.citation_range};
            } else {
              const response = await request(`${path}/content`, {signal});
              const bytes = await response.arrayBuffer();
              if (signal.aborted) return;
              state.text = decodePreviewText(bytes);
            }
          } catch (error) {
            if (!signal.aborted && error.name !== 'AbortError') state.error = error.message;
          } finally {
            if (!signal.aborted) state.loading = false;
          }
          if (!signal.aborted && scroll && !state.url && state.hasContent) {
            await nextTick();
            if (!signal.aborted) canvas.value?.scrollTo(0, scroll);
          }
        }
      }
      function loadPreview(pageNumber = preview.page ?? 1, sheetPosition) {
        const source = activeSource.value;
        return preview.load(source, {pageNumber,sheetPosition,citationPage:chatPageNumber(source?.pageNumber)});
      }
      async function openPreview(source, event) {
        if (activeSource.value?.key !== source.key) {
          resetPreview();
          activeSource.value = source;
          loadPreview(chatPageNumber(source.pageNumber) ?? 1);
        }
        previewOpen.value = true;
        if (event.detail === 0 || compactReader.value) {
          await nextTick();
          previewTitle.value?.focus({preventScroll:true});
        }
      }
      async function toggleReadingSidebar() {
        if (!compactReader.value && !sidebarHidden.value) {
          sidebarHidden.value = true;
          return;
        }
        if (compactReader.value) previewOpen.value = false;
        sidebarHidden.value = false;
        sidebarPane.value = 'results';
        await nextTick();
        const target = readingList.value?.querySelector('[aria-pressed="true"]') || searchInput.value;
        target?.focus({preventScroll:true});
      }
      async function downloadSource() {
        const source = activeSource.value, epoch = identityEpoch;
        if (!source || downloadLoading.value) return;
        downloadLoading.value = true;
        try {
          const response = await request(`${sourcePath(source)}/content`);
          const blob = await response.blob();
          if (epoch !== identityEpoch) return;
          saveOriginal(blob, source.filename);
        } catch (error) {report(error);}
        finally {if (epoch === identityEpoch) downloadLoading.value = false;}
      }
      function saveOriginal(blob, filename) {
        const url = URL.createObjectURL(blob), link = document.createElement('a');
        link.href = url; link.download = filename;
        document.body.append(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
      async function createKnowledge() {
        if (!knowledgeName.value.trim() || knowledgeLoading.value) return;
        const epoch = identityEpoch; knowledgeLoading.value = true;
        try {
          const data = await jsonRequest('/knowledges',jsonBody('POST',{name:knowledgeName.value.trim(),description:knowledgeDescription.value.trim()}));
          if (epoch !== identityEpoch) return;
          knowledges.value.unshift(data); createOpen.value = false; knowledgeName.value = ''; knowledgeDescription.value = '';
          clearSearchInput(); await selectKnowledge(data.id); ElMessage.success('知识库已创建');
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
        try {await jsonRequest(`/knowledges/${encodeURIComponent(id)}`,{method:'DELETE'}); if (epoch !== identityEpoch) return; selectedKnowledgeIds.value = selectedKnowledgeIds.value.filter(value => value !== id); clearSearchInput(); await loadKnowledges(); ElMessage.success('知识库已删除');} catch (error) {report(error);}
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
        try {const body = new FormData(); body.append('file',file); await jsonRequest(`/knowledges/${encodeURIComponent(id)}/documents`,{method:'POST',body}); if (epoch !== identityEpoch) return; if (!selectedKnowledgeId.value || id === selectedKnowledgeId.value) await loadDocuments(); ElMessage.success('已上传，处理状态将自动更新');}
        catch (error) {report(error);} finally {if (epoch === identityEpoch) isUploading.value = false;}
      }
      function chooseReplacement(doc) {replacingDocumentId.value = {id:doc.id, knowledgeId:doc.knowledge_id}; replaceInput.value?.click();}
      async function replaceDocument(event) {
        const file = event.target.files?.[0], replacement = replacingDocumentId.value, epoch = identityEpoch; event.target.value = ''; replacingDocumentId.value = '';
        if (!file || !replacement) return;
        const {id:documentId, knowledgeId:id} = replacement;
        const validationError = validateFile(file); if (validationError) {ElMessage.warning(validationError); return;}
        if (!await confirmAction(`用“${file.name}”替换此文档？旧内容会立即退出检索。`,'替换文档') || epoch !== identityEpoch) return;
        documentBusyId.value = documentId;
        try {const body = new FormData(); body.append('file',file); await jsonRequest(`/knowledges/${encodeURIComponent(id)}/documents/${encodeURIComponent(documentId)}/content`,{method:'PUT',body}); if (epoch === identityEpoch) await refreshChangedDocument(id, documentId);}
        catch (error) {report(error);} finally {if (epoch === identityEpoch) documentBusyId.value = '';}
      }
      async function documentAction(doc, action) {
        const id = doc.knowledge_id, epoch = identityEpoch;
        if (action === 'delete' && !await confirmAction(`删除“${doc.filename}”？此文件将退出检索。`,'删除文档')) return;
        if (epoch !== identityEpoch) return;
        documentBusyId.value = doc.id;
        try {await jsonRequest(`/knowledges/${encodeURIComponent(id)}/documents/${encodeURIComponent(doc.id)}${action === 'delete' ? '' : '/' + action}`,{method:action === 'delete' ? 'DELETE' : 'POST'}); if (epoch === identityEpoch) await refreshChangedDocument(id, doc.id);}
        catch (error) {report(error);} finally {if (epoch === identityEpoch) documentBusyId.value = '';}
      }
      async function refreshChangedDocument(knowledgeId, documentId) {
        if (activeSource.value?.documentId === documentId) resetPreview();
        if (searchResult.value?.results?.some(source => source.documentId === documentId)) runSearch(submittedQuery.value);
        if (!selectedKnowledgeId.value || knowledgeId === selectedKnowledgeId.value) await loadDocuments();
      }
      const statusLabel = status => ({pending:'等待处理',processing:'处理中',ready:'可检索',failed:'处理失败',replacing:'替换中',deleting:'删除中'}[status] || status);
      const documentPending = doc => ['pending','processing','replacing','deleting'].includes(doc.status);
      async function viewDocumentPage(doc, pageNumber) {
        const id = doc.knowledge_id, epoch = identityEpoch;
        const review = reactive({filename:doc.filename,pageNumber,data:null,error:''});
        pageReview.value = review; pageReviewLoading.value = true;
        try {const data = await jsonRequest(`/knowledges/${encodeURIComponent(id)}/documents/${encodeURIComponent(doc.id)}/pages/${pageNumber}`); if (epoch === identityEpoch && pageReview.value === review) review.data = data;}
        catch (error) {if (epoch === identityEpoch && pageReview.value === review) review.error = error.message;}
        finally {if (epoch === identityEpoch && pageReview.value === review) pageReviewLoading.value = false;}
      }
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
      async function toggleChatSources(messageId, event) {
        if (chatSourcesOpen.value && chatSourceView.value?.messageId === messageId) return closeChatSources();
        if (chatSourceView.value?.messageId !== messageId) {
          resetChatSources();
          chatSourceView.value = {messageId,sourceKey:null};
        }
        chatSourceTrigger = event?.currentTarget || null;
        chatSourcesOpen.value = true;
        loadChatPreview(chatPreview.page ?? chatSource.value?.pageNumber ?? 1, chatPreviewScroll);
        await nextTick();
        chatSourcesHeading.value?.focus({preventScroll:true});
      }
      function selectChatSource(source) {
        if (source.key === chatSource.value?.key) return;
        clearChatSourceContent(true);
        chatSourceView.value = {...chatSourceView.value,sourceKey:source.key};
        chatPreviewScroll = 0;
        loadChatPreview(source.pageNumber ?? 1);
      }
      async function closeChatSources() {
        chatPreviewScroll = chatSourcePreview.value?.scrollTop || 0;
        clearChatSourceContent();
        chatSourcesOpen.value = false;
        const trigger = chatSourceTrigger;
        chatSourceTrigger = null;
        await nextTick();
        if (!chatSourcesOpen.value) trigger?.focus({preventScroll:true});
      }
      function resetChatSources() {
        clearChatSourceContent(true);
        chatSourcesOpen.value = false; chatSourceView.value = null; chatSourceTrigger = null;
        chatPreviewScroll = 0;
      }
      function clearChatSourceContent(resetPosition = false) {
        if (resetPosition) chatPreview.reset();
        else chatPreview.clear();
        chatOriginalController?.abort(); chatOriginalController = null;
        chatOriginalLoading.value = false;
      }
      function loadChatPreview(pageNumber = chatPreview.page ?? 1, scroll = 0, sheetPosition) {
        if (!chatSourcesOpen.value || !chatPreviewFormat.value) return;
        chatPreviewScroll = scroll;
        const source = chatSource.value;
        return chatPreview.load(source, {pageNumber,scroll,sheetPosition,citationPage:source.pageNumber});
      }
      async function downloadChatOriginal() {
        if (!hasChatOriginal.value || chatOriginalLoading.value) return;
        const source = chatSource.value, controller = new AbortController();
        chatOriginalController = controller; chatOriginalLoading.value = true;
        try {
          const response = await request(`${sourcePath(source)}/content`, {signal:controller.signal});
          const blob = await response.blob();
          if (!controller.signal.aborted) saveOriginal(blob, source.filename);
        } catch (error) {if (!controller.signal.aborted) report(error);}
        finally {
          if (!controller.signal.aborted) {
            chatOriginalController = null; chatOriginalLoading.value = false;
          }
        }
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
      function newChat() {stopChat(); sessionEpoch++; resetChatSources(); messages.value = []; sessionId = newSessionId(); sessionsOpen.value = false;}
      async function loadSessions() {
        sessionsOpen.value = true; sessionsLoading.value = true; sessionsError.value = ''; const epoch = identityEpoch;
        try {const data = await jsonRequest('/sessions'); if (epoch === identityEpoch) sessions.value = data.sessions || [];}
        catch (error) {if (epoch === identityEpoch && error.name !== 'AbortError') sessionsError.value = error.message;}
        finally {if (epoch === identityEpoch) sessionsLoading.value = false;}
      }
      async function loadSession(item) {
        stopChat(); const epoch = identityEpoch, call = ++sessionEpoch; sessionsLoading.value = true;
        try {
          const data = await jsonRequest(`/sessions/${encodeURIComponent(item.session_id)}`);
          if (epoch !== identityEpoch || call !== sessionEpoch || page.value !== '/try') return;
          resetChatSources(); sessionId = item.session_id;
          messages.value = data.messages.map(msg => ({id:crypto.randomUUID(),author:msg.type === 'human' ? 'user' : 'agent',text:msg.content,trace:msg.rag_trace,knowledgeIds:msg.knowledge_ids}));
          if (Array.isArray(data.last_knowledge_ids)) selectedKnowledgeIds.value = data.last_knowledge_ids.filter(id => knowledges.value.some(kb => kb.id === id)).slice(0,20);
          else if (data.legacy_scope_unknown) selectedKnowledgeIds.value = [];
          sessionsOpen.value = false; scrollChat();
        }
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
        resetChatSources();
        createOpen.value = false; editOpen.value = false; sessionsOpen.value = false; helpTopic.value = ''; qualityReview.value = null;
        searchQuery.value = ''; clearSearch(); sidebarPane.value = 'results'; sidebarHidden.value = false; pageReview.value = null;
        document.title = pageTitle.value + ' · MeowConnectPlatform'; await loadPageData(); await nextTick();
        document.getElementById('main')?.focus({preventScroll:true});
      });
      onMounted(async () => {
        document.title = pageTitle.value + ' · MeowConnectPlatform'; window.addEventListener('popstate',onPopState);
        try {githubEnabled.value = (await jsonRequest('/auth/providers',{},false)).github === true;} catch {}
        const githubRestored = await restoreGitHubLogin();
        if (token.value && !githubRestored) {
          try {currentUser.value = await jsonRequest('/auth/me');} catch (error) {if (error.name !== 'AbortError') report(error);}
          finally {authChecking.value = false;}
        }
        await loadPageData();
      });
      onBeforeUnmount(() => {identityEpoch++; stopChat(); stopDocumentPolling(); clearSearch(); resetChatSources(); window.removeEventListener('popstate',onPopState); systemTheme.removeEventListener('change',onSystemTheme); readerMedia.removeEventListener('change',onReaderWidth);});
      return {
        locale:ElementPlusLocaleZhCn,page,mobileNav,currentUser,authChecking,isAuthenticated,isAdmin,displayName,pageTitle,isLibrary,isDark,toggleTheme,go,accountCommand,
        connectionClient,servicePanels,connectionEndpoint,connectionExamples,helpTopic,helpTitle,qualityReview,
        authOpen,authMode,authView,authTitle,authLoading,authError,authForm,authFormRef,authRules,usernameInput,githubLoginButton,resetAuth,openAuth,switchAuth,focusAuth,choosePasswordLogin,showAuthMethods,submitAuth,logout,githubEnabled,githubLoading,startGitHubLogin,
        knowledges,selectedKnowledgeId,selectedKnowledgeIds,selectedKnowledge,knowledgeLoading,knowledgeError,loadKnowledges,selectKnowledge,createOpen,editOpen,knowledgeName,knowledgeDescription,knowledgeEdit,createKnowledge,openKnowledgeEdit,updateKnowledge,deleteKnowledge,
        documents,documentsLoading,documentsError,documentBusyId,loadDocuments,fileInput,replaceInput,isUploading,acceptedExtensions,uploadLimitLabel,uploadDocument,chooseReplacement,replaceDocument,documentAction,statusLabel,documentPending,openTryWithKnowledge,pageReview,pageReviewLoading,viewDocumentPage,
        searchQuery,submittedQuery,searchResult,searchLoading,searchError,readingRows,searchInput,readingList,runSearch,clearSearchInput,guardSearchComposition,
        compactReader,sidebarPane,sidebarHidden,browseKnowledge,toggleReadingSidebar,
        activeSource,activeDocument,previewOpen,preview,previewFormat,previewTitle,previewCanvas,previewZoom,openPreview,loadPreview,downloadSource,downloadLoading,
        scopeNames,toolName,toolQuery,topK,knowledgeSettings,toolLoading,toolResult,toolError,runTool,messages,chatInput,chatLoading,chatLog,isComposing,sendChat,stopChat,handleChatKey,parseMarkdown,traceFacts,traceGroups,newChat,sessions,sessionsOpen,sessionsLoading,sessionsError,loadSessions,loadSession,deleteSession,
        chatSourcesOpen,chatSourceView,chatSourcesHeading,chatSourcesByMessage,chatSources,chatSource,toggleChatSources,selectChatSource,closeChatSources,
        chatPageNumber,chatSourcePreview,chatPreview,
        hasChatOriginal,chatPreviewFormat,chatOriginalLoading,loadChatPreview,downloadChatOriginal,
        keys,keyName,newKey,keysLoading,keyBusy,keyError,loadKeys,createKey,revokeKey,invites,newInvite,invitesLoading,inviteBusy,inviteError,invitationForm,loadInvites,createInvite,revokeInvite,invitationStatus,formatDate,operations,operationsLoading,operationsError,loadOperations,copyText
      };
    }
  });
  app.use(ElementPlus);
  app.component('mcp-connection-example',{
    props:{modelValue:String,examples:Array},
    emits:['update:modelValue','copy','help'],
    template:'#connection-example-template'
  });
  app.component('mcp-document-preview',{
    props:{preview:Object,filename:String,query:String,zoom:{type:Number,default:100},compact:Boolean},
    emits:['sheet-navigation'],
    setup(props, {emit}) {
      const isMarkdown = computed(() => previewFormat(props.filename) === 'md');
      const markdown = computed(() => parseMarkdown(props.preview.text, true));
      const textParts = computed(() => highlightParts(props.preview.text || '', props.query));
      const workbook = computed(() => props.preview.workbook);
      const sheetGrid = ref(null);
      const citation = computed(() => {
        const reference = props.preview.citation;
        if (!reference || reference.sheet !== workbook.value?.sheet) return null;
        const range = /^([A-Z]+)([0-9]+)(?::([A-Z]+)([0-9]+))?$/.exec(reference.range);
        if (!range) return null;
        const [,startColumn,startRow,endColumn = startColumn,endRow = startRow] = range;
        const columnNumber = letters => [...letters].reduce((number, letter) => number * 26 + letter.charCodeAt(0) - 64, 0);
        return {firstRow:Number(startRow),lastRow:Number(endRow),firstColumn:columnNumber(startColumn),lastColumn:columnNumber(endColumn)};
      });
      function isCitationCell(row, column) {
        const bounds = citation.value;
        return bounds && row >= bounds.firstRow && row <= bounds.lastRow && column >= bounds.firstColumn && column <= bounds.lastColumn;
      }
      function navigateSheet(sheet, row, column) {
        if (!props.preview.loading) emit('sheet-navigation', {sheet,row,column});
      }
      watch(workbook, () => sheetGrid.value?.scrollTo(0, 0), {flush:'post'});
      return {isMarkdown,markdown,textParts,workbook,sheetGrid,citation,isCitationCell,navigateSheet};
    },
    template:'#document-preview-template'
  });
  app.component('mcp-icon',{props:{name:{type:String,required:true},size:{type:Number,default:20}},setup(props) {return () => Vue.h(ElementPlus.ElIcon,{size:props.size,'aria-hidden':'true'},() => Vue.h(ElementPlusIconsVue[props.name] || ElementPlusIconsVue.Document));}});
  app.mount('#app');
})();
