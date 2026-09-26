const { createApp } = Vue;

createApp({
    data() {
        return {
            messages: [],
            userInput: '',
            isLoading: false,
            activeNav: 'newChat',
            page: ['/', '/services/knowledge', '/account', '/knowledges', '/try'].includes(window.location.pathname.replace(/\/$/, '') || '/') ? (window.location.pathname.replace(/\/$/, '') || '/') : '/',
            showAuth: false,
            services: [{ id: 'knowledge', category: 'KNOWLEDGE', name: '知识库 MCP', description: '把私有文件转为可检索知识，先通过站内工具验证结果与来源。', tools: ['listKnowledges', 'retrieve'], path: '/services/knowledge' }],
            abortController: null,
            sessionId: 'session_' + Date.now(),
            sessions: [],
            showHistorySidebar: false,
            tryMode: 'chat',
            debugQuery: '',
            debugTopK: 5,
            debugTool: '',
            debugLoading: false,
            debugResult: null,
            debugError: '',
            isComposing: false,
            documents: [],
            documentsLoading: false,
            knowledges: [],
            selectedKnowledgeId: '',
            selectedKnowledgeIds: [],
            scopeInitialized: false,
            knowledgeEdit: { name: '', description: '' },
            knowledgeSettings: { allowed_extensions: [], max_upload_bytes: 0 },
            notice: '',
            documentPollTimer: null,
            documentRequestId: 0,
            documentBusyId: '',
            replacingDocumentId: '',
            newKnowledgeName: '',
            knowledgeLoading: false,
            selectedFile: null,
            isUploading: false,
            uploadProgress: '',
            token: localStorage.getItem('accessToken') || '',
            currentUser: null,
            authMode: 'login',
            authForm: {
                username: '',
                password: '',
                invite_code: ''
            },
            authLoading: false,
            showNotice: true
        };
    },
    computed: {
        isAuthenticated() {
            return !!this.token && !!this.currentUser;
        },
        isAdmin() {
            return this.currentUser?.role === 'admin';
        },
        isProtectedPage() {
            return ['/account', '/knowledges', '/try'].includes(this.page);
        },
        selectedKnowledge() {
            return this.knowledges.find(item => item.id === this.selectedKnowledgeId) || null;
        },
        selectedScopeLabel() {
            if (!this.selectedKnowledgeIds.length) return '未选择知识库（空范围）';
            return this.selectedKnowledgeIds.map(id => this.knowledges.find(item => item.id === id)?.name || '已删除的知识库').join('、');
        }
    },
    async mounted() {
        this.configureMarked();
        window.addEventListener('popstate', this.syncRoute);
        if (this.token) {
            try {
                await this.fetchMe();
            } catch (_) {
                this.handleLogout();
            }
        }
        if (this.isAuthenticated && ['/knowledges', '/try'].includes(this.page)) await this.loadKnowledges();
    },
    beforeUnmount() {
        window.removeEventListener('popstate', this.syncRoute);
        this.stopDocumentPolling();
    },
    methods: {
        syncRoute() {
            const path = window.location.pathname.replace(/\/$/, '') || '/';
            this.page = ['/', '/services/knowledge', '/account', '/knowledges', '/try'].includes(path) ? path : '/';
            if (this.page === '/knowledges' || this.page === '/try') this.loadKnowledges();
            if (this.page !== '/knowledges') this.stopDocumentPolling();
            if (this.page !== '/try') this.showHistorySidebar = false;
        },
        navigate(path) {
            if (this.page !== path) window.history.pushState({}, '', path);
            this.syncRoute();
        },
        openLogin() { this.showAuth = true; },
        scopeNames(ids) {
            if (ids === null || ids === undefined) return '旧会话：未记录知识库范围';
            if (!ids.length) return '空范围';
            return ids.map(id => this.knowledges.find(item => item.id === id)?.name || `已删除的知识库 (${id.slice(0, 8)})`).join('、');
        },
        async runDebug(tool) {
            if (this.debugLoading) return;
            const query = this.debugQuery.trim();
            if (tool === 'retrieve' && !query) { this.debugError = '请输入检索问题。'; return; }
            this.debugTool = tool;
            this.debugResult = null;
            this.debugError = '';
            this.debugLoading = true;
            try {
                const payload = tool === 'retrieve' ? {query, knowledgeIds: [...this.selectedKnowledgeIds], topK: Number(this.debugTopK)} : {limit: 50};
                const response = await this.authFetch(`/tools/debug/${tool}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(this.apiError(response, data, '工具调试失败'));
                this.debugResult = data;
            } catch (error) { this.debugError = error.message; }
            finally { this.debugLoading = false; }
        },
        apiError(response, payload, fallback) {
            const detail = typeof payload.detail === 'string' ? payload.detail : fallback;
            if (response.status === 409) return `操作冲突：${detail}。请稍后重试。`;
            if (response.status === 413) return `文件超过大小限制。${detail}`;
            if (response.status === 403 || response.status === 404) return '资源已删除或无权访问，请刷新列表。';
            return detail;
        },
        setNotice(message) { this.notice = message; },
        configureMarked() {
            marked.setOptions({
                highlight: function(code, lang) {
                    const language = hljs.getLanguage(lang) ? lang : 'plaintext';
                    return hljs.highlight(code, { language }).value;
                },
                langPrefix: 'hljs language-',
                breaks: true,
                gfm: true
            });
        },

        parseMarkdown(text) {
            return marked.parse(text);
        },

        escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        },

        authHeaders(extra = {}) {
            const headers = { ...extra };
            if (this.token) {
                headers.Authorization = `Bearer ${this.token}`;
            }
            return headers;
        },

        async authFetch(url, options = {}) {
            const opts = { ...options };
            opts.headers = this.authHeaders(opts.headers || {});
            const response = await fetch(url, opts);
            if (response.status === 401) {
                this.handleLogout();
                throw new Error('登录已过期，请重新登录');
            }
            return response;
        },

        async fetchMe() {
            const response = await this.authFetch('/auth/me');
            if (!response.ok) {
                throw new Error('认证失败');
            }
            this.currentUser = await response.json();
        },

        async handleAuthSubmit() {
            if (this.authLoading) return;
            const username = this.authForm.username.trim();
            const password = this.authForm.password.trim();
            if (!username || !password) {
                alert('用户名和密码不能为空');
                return;
            }

            this.authLoading = true;
            try {
                const endpoint = this.authMode === 'login' ? '/auth/login' : '/auth/register';
                const payload = {
                    username,
                    password
                };
                if (this.authMode === 'register') {
                    payload.invite_code = this.authForm.invite_code.trim();
                }

                const response = await fetch(endpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(data.detail || '认证失败');
                }

                this.token = data.access_token;
                this.currentUser = { username: data.username, role: data.role };
                localStorage.setItem('accessToken', this.token);
                this.authForm.password = '';
                this.authForm.invite_code = '';
                this.messages = [];
                this.sessionId = 'session_' + Date.now();
                this.activeNav = 'newChat';
                this.showAuth = false;
                if (['/knowledges', '/try'].includes(this.page)) await this.loadKnowledges();
            } catch (error) {
                alert(error.message);
            } finally {
                this.authLoading = false;
            }
        },

        handleLogout() {
            this.token = '';
            this.currentUser = null;
            this.messages = [];
            this.sessions = [];
            this.documents = [];
            this.knowledges = [];
            this.selectedKnowledgeId = '';
            this.selectedKnowledgeIds = [];
            this.scopeInitialized = false;
            this.stopDocumentPolling();
            this.newKnowledgeName = '';
            this.activeNav = 'newChat';
            this.showHistorySidebar = false;
            this.showNotice = true;
            this.showAuth = false;
            localStorage.removeItem('accessToken');
        },

        handleCompositionStart() {
            this.isComposing = true;
        },

        handleCompositionEnd() {
            this.isComposing = false;
        },

        handleKeyDown(event) {
            if (event.key === 'Enter' && !event.shiftKey && !this.isComposing) {
                event.preventDefault();
                this.handleSend();
            }
        },

        handleStop() {
            if (this.abortController) {
                this.abortController.abort();
            }
        },

        async handleSend() {
            if (!this.isAuthenticated) {
                alert('请先登录');
                return;
            }

            const text = this.userInput.trim();
            if (!text || this.isLoading || this.isComposing) return;
            const scopeSnapshot = [...this.selectedKnowledgeIds];

            this.messages.push({
                text: text,
                isUser: true,
                knowledgeIds: scopeSnapshot
            });

            this.userInput = '';
            this.$nextTick(() => {
                this.resetTextareaHeight();
                this.scrollToBottom();
            });

            this.isLoading = true;
            this.messages.push({
                text: '',
                isUser: false,
                isThinking: true,
                ragTrace: null,
                ragSteps: [],
                knowledgeIds: scopeSnapshot
            });
            const botMsgIdx = this.messages.length - 1;

            this.abortController = new AbortController();

            try {
                const response = await this.authFetch('/chat/stream', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        message: text,
                        session_id: this.sessionId,
                        knowledge_ids: scopeSnapshot
                    }),
                    signal: this.abortController.signal,
                });

                if (!response.ok) throw new Error(`HTTP ${response.status}`);

                const reader = response.body.getReader();
                const decoder = new TextDecoder();

                let buffer = '';
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });

                    let eventEndIndex;
                    while ((eventEndIndex = buffer.indexOf('\n\n')) !== -1) {
                        const eventStr = buffer.slice(0, eventEndIndex);
                        buffer = buffer.slice(eventEndIndex + 2);

                        if (eventStr.startsWith('data: ')) {
                            const dataStr = eventStr.slice(6);
                            if (dataStr === '[DONE]') continue;
                            try {
                                const data = JSON.parse(dataStr);
                                if (data.type === 'content') {
                                    if (this.messages[botMsgIdx].isThinking) {
                                        this.messages[botMsgIdx].isThinking = false;
                                    }
                                    this.messages[botMsgIdx].text += data.content;
                                } else if (data.type === 'trace') {
                                    this.messages[botMsgIdx].ragTrace = data.rag_trace;
                                } else if (data.type === 'rag_step') {
                                    if (!this.messages[botMsgIdx].ragSteps) {
                                        this.messages[botMsgIdx].ragSteps = [];
                                    }
                                    this.messages[botMsgIdx].ragSteps.push(data.step);
                                } else if (data.type === 'error') {
                                    this.messages[botMsgIdx].isThinking = false;
                                    this.messages[botMsgIdx].text += `\n[Error: ${data.content}]`;
                                }
                            } catch (e) {
                                console.warn('SSE parse error:', e);
                            }
                        }
                    }
                    this.$nextTick(() => this.scrollToBottom());
                }

            } catch (error) {
                if (error.name === 'AbortError') {
                    this.messages[botMsgIdx].isThinking = false;
                    if (!this.messages[botMsgIdx].text) {
                        this.messages[botMsgIdx].text = '(已终止回答)';
                    } else {
                        this.messages[botMsgIdx].text += '\n\n_(回答已被终止)_';
                    }
                } else {
                    this.messages[botMsgIdx].isThinking = false;
                    this.messages[botMsgIdx].text = `喵呜...出了点问题：${error.message}`;
                }
            } finally {
                this.isLoading = false;
                this.abortController = null;
                this.$nextTick(() => this.scrollToBottom());
            }
        },

        autoResize(event) {
            const textarea = event.target;
            textarea.style.height = 'auto';
            textarea.style.height = textarea.scrollHeight + 'px';
        },

        resetTextareaHeight() {
            if (this.$refs.textarea) {
                this.$refs.textarea.style.height = 'auto';
            }
        },

        scrollToBottom() {
            if (this.$refs.chatContainer) {
                this.$refs.chatContainer.scrollTop = this.$refs.chatContainer.scrollHeight;
            }
        },

        handleNewChat() {
            if (!this.isAuthenticated) return;
            this.navigate('/try');
            this.messages = [];
            this.sessionId = 'session_' + Date.now();
            this.activeNav = 'newChat';
            this.showHistorySidebar = false;
        },

        handleClearChat() {
            if (confirm('确定要清空当前对话吗？喵~')) {
                this.messages = [];
            }
        },

        async handleHistory() {
            if (!this.isAuthenticated) return;
            this.navigate('/try');
            this.activeNav = 'history';
            this.showHistorySidebar = true;
            try {
                const response = await this.authFetch('/sessions');
                if (!response.ok) {
                    throw new Error('Failed to load sessions');
                }
                const data = await response.json();
                this.sessions = data.sessions;
            } catch (error) {
                alert('加载历史记录失败：' + error.message);
            }
        },

        async loadSession(sessionId) {
            this.sessionId = sessionId;
            this.showHistorySidebar = false;
            this.activeNav = 'newChat';

            try {
                const response = await this.authFetch(`/sessions/${encodeURIComponent(sessionId)}`);
                if (!response.ok) {
                    throw new Error('Failed to load session messages');
                }
                const data = await response.json();
                this.messages = data.messages.map(msg => ({
                    text: msg.content,
                    isUser: msg.type === 'human',
                    ragTrace: msg.rag_trace || null,
                    knowledgeIds: msg.knowledge_ids
                }));
                if (Array.isArray(data.last_knowledge_ids)) {
                    const owned = new Set(this.knowledges.map(item => item.id));
                    this.selectedKnowledgeIds = data.last_knowledge_ids.filter(id => owned.has(id));
                }

                this.$nextTick(() => {
                    this.scrollToBottom();
                });
            } catch (error) {
                alert('加载会话失败：' + error.message);
                this.messages = [];
            }
        },

        async deleteSession(sessionId) {
            const session = this.sessions.find(s => s.session_id === sessionId);
            const label = session?.title || sessionId;
            if (!confirm(`确定要删除会话 "${label}" 吗？`)) {
                return;
            }

            try {
                const response = await this.authFetch(`/sessions/${encodeURIComponent(sessionId)}`, {
                    method: 'DELETE'
                });

                const payload = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(payload.detail || 'Delete failed');
                }

                this.sessions = this.sessions.filter(s => s.session_id !== sessionId);

                if (this.sessionId === sessionId) {
                    this.messages = [];
                    this.sessionId = 'session_' + Date.now();
                    this.activeNav = 'newChat';
                }

                if (payload.message) {
                    alert(payload.message);
                }
            } catch (error) {
                alert('删除会话失败：' + error.message);
            }
        },

        handleUploadClick() {
            this.navigate('/knowledges');
        },

        handleSettings() {
            if (!this.isAuthenticated) return;
            this.activeNav = 'settings';
            this.showHistorySidebar = false;
            this.navigate('/knowledges');
        },

        async loadKnowledges() {
            this.knowledgeLoading = true;
            try {
                if (!this.knowledgeSettings.max_upload_bytes) {
                    const settingsResponse = await fetch('/knowledge-settings');
                    if (settingsResponse.ok) this.knowledgeSettings = await settingsResponse.json();
                }
                const response = await this.authFetch('/knowledges');
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || '加载知识库失败');
                this.knowledges = data.knowledges || [];
                const availableIds = new Set(this.knowledges.map(item => item.id));
                if (!this.scopeInitialized) {
                    const preselected = new URLSearchParams(window.location.search).get('knowledge');
                    this.selectedKnowledgeIds = preselected && availableIds.has(preselected) ? [preselected] : this.knowledges.filter(item => item.has_ready_documents).map(item => item.id);
                    this.scopeInitialized = true;
                } else {
                    this.selectedKnowledgeIds = this.selectedKnowledgeIds.filter(id => availableIds.has(id));
                }
                if (!this.knowledges.some(item => item.id === this.selectedKnowledgeId)) {
                    this.selectedKnowledgeId = this.knowledges[0]?.id || '';
                }
                this.syncKnowledgeEdit();
                if (this.page === '/knowledges') await this.loadDocuments();
            } catch (error) {
                this.setNotice('加载知识库失败：' + error.message);
            } finally {
                this.knowledgeLoading = false;
            }
        },
        syncKnowledgeEdit() {
            this.knowledgeEdit = { name: this.selectedKnowledge?.name || '', description: this.selectedKnowledge?.description || '' };
        },
        selectKnowledge() {
            this.stopDocumentPolling();
            this.selectedFile = null;
            this.syncKnowledgeEdit();
            this.loadDocuments();
        },
        openTryWithKnowledge(id) {
            this.selectedKnowledgeIds = [id];
            window.history.pushState({}, '', `/try?knowledge=${encodeURIComponent(id)}`);
            this.syncRoute();
        },
        async updateKnowledge() {
            const id = this.selectedKnowledgeId;
            const name = this.knowledgeEdit.name.trim();
            if (!id || !name || this.knowledgeLoading) return;
            this.knowledgeLoading = true;
            try {
                const response = await this.authFetch(`/knowledges/${encodeURIComponent(id)}`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name, description: this.knowledgeEdit.description.trim()})});
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(this.apiError(response, data, '更新失败'));
                this.knowledges = this.knowledges.map(item => item.id === id ? data : item);
                this.setNotice('知识库信息已保存。');
            } catch (error) { this.setNotice(error.message); }
            finally { this.knowledgeLoading = false; }
        },
        async deleteKnowledge() {
            const id = this.selectedKnowledgeId;
            const name = this.selectedKnowledge?.name;
            if (!id || !confirm(`确定删除知识库“${name}”吗？库内文件和索引会一并删除，此操作不可恢复。`)) return;
            this.knowledgeLoading = true;
            try {
                const response = await this.authFetch(`/knowledges/${encodeURIComponent(id)}`, {method: 'DELETE'});
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(this.apiError(response, data, '删除失败'));
                this.knowledges = this.knowledges.filter(item => item.id !== id);
                this.selectedKnowledgeIds = this.selectedKnowledgeIds.filter(item => item !== id);
                this.selectedKnowledgeId = this.knowledges[0]?.id || '';
                this.syncKnowledgeEdit();
                await this.loadDocuments();
                this.setNotice('知识库已删除。现有会话的历史内容仍可查看；后续提问需重新选择知识库。');
            } catch (error) { this.setNotice(error.message); }
            finally { this.knowledgeLoading = false; }
        },

        async createKnowledge() {
            const name = this.newKnowledgeName.trim();
            if (!name || this.knowledgeLoading) return;
            this.knowledgeLoading = true;
            try {
                const response = await this.authFetch('/knowledges', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name })
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || '创建知识库失败');
                this.newKnowledgeName = '';
                this.knowledges.unshift(data);
                this.selectedKnowledgeId = data.id;
                this.syncKnowledgeEdit();
                await this.loadDocuments();
            } catch (error) {
                this.setNotice('创建知识库失败：' + error.message);
            } finally {
                this.knowledgeLoading = false;
            }
        },

        async loadDocuments() {
            const knowledgeId = this.selectedKnowledgeId;
            const requestId = ++this.documentRequestId;
            this.stopDocumentPolling();
            if (!knowledgeId) {
                this.documents = [];
                return;
            }
            this.documentsLoading = true;
            try {
                const response = await this.authFetch(`/knowledges/${encodeURIComponent(knowledgeId)}/documents`);
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw new Error(this.apiError(response, data, '加载文档失败'));
                }
                const data = await response.json();
                if (this.selectedKnowledgeId !== knowledgeId || requestId !== this.documentRequestId) return;
                this.documents = data.documents || [];
                const knowledge = this.knowledges.find(item => item.id === knowledgeId);
                if (knowledge) {
                    knowledge.ready_document_count = this.documents.filter(item => item.status === 'ready').length;
                    knowledge.has_ready_documents = knowledge.ready_document_count > 0;
                }
                if (this.page === '/knowledges' && this.documents.some(item => ['pending', 'processing', 'replacing', 'deleting'].includes(item.status))) {
                    this.documentPollTimer = setTimeout(() => this.loadDocuments(), 2500);
                }
            } catch (error) {
                if (this.selectedKnowledgeId === knowledgeId) this.setNotice('加载文档列表失败：' + error.message);
            } finally {
                if (this.selectedKnowledgeId === knowledgeId && requestId === this.documentRequestId) this.documentsLoading = false;
            }
        },
        stopDocumentPolling() {
            if (this.documentPollTimer) clearTimeout(this.documentPollTimer);
            this.documentPollTimer = null;
        },
        validateFile(file) {
            const ext = '.' + (file.name.split('.').pop() || '').toLowerCase();
            const allowed = this.knowledgeSettings.allowed_extensions || [];
            if (allowed.length && !allowed.includes(ext)) return `仅支持 ${allowed.join('、')} 文件。`;
            if (this.knowledgeSettings.max_upload_bytes && file.size > this.knowledgeSettings.max_upload_bytes) return `文件不能超过 ${this.uploadLimitLabel()}。`;
            return '';
        },
        uploadLimitLabel() {
            return this.knowledgeSettings.max_upload_bytes ? `${Math.round(this.knowledgeSettings.max_upload_bytes / 1024 / 1024)} MB` : '服务端限制';
        },

        handleFileSelect(event) {
            const files = event.target.files;
            if (files && files.length > 0) {
                const error = this.validateFile(files[0]);
                this.selectedFile = error ? null : files[0];
                this.uploadProgress = error;
            }
        },

        async uploadDocument() {
            if (!this.selectedKnowledgeId) {
                alert('请先选择知识库');
                return;
            }
            if (!this.selectedFile) {
                alert('请先选择文件');
                return;
            }

            const knowledgeId = this.selectedKnowledgeId;
            const file = this.selectedFile;
            const validationError = this.validateFile(file);
            if (validationError) { this.uploadProgress = validationError; return; }
            this.isUploading = true;
            this.uploadProgress = '正在上传...';

            try {
                const formData = new FormData();
                formData.append('file', file);

                const response = await this.authFetch(`/knowledges/${encodeURIComponent(knowledgeId)}/documents`, {
                    method: 'POST',
                    body: formData
                });

                if (!response.ok) {
                    const error = await response.json().catch(() => ({}));
                    throw new Error(this.apiError(response, error, '上传失败'));
                }

                this.uploadProgress = '文件已上传，正在处理。状态会自动更新。';

                this.selectedFile = null;
                if (this.$refs.fileInput) {
                    this.$refs.fileInput.value = '';
                }

                if (this.selectedKnowledgeId === knowledgeId) await this.loadDocuments();

                setTimeout(() => {
                    this.uploadProgress = '';
                }, 3000);

            } catch (error) {
                this.uploadProgress = '上传失败：' + error.message;
            } finally {
                this.isUploading = false;
            }
        },

        async deleteDocument(doc) {
            if (!confirm(`确定要删除文档 "${doc.filename}" 吗？`)) {
                return;
            }
            const knowledgeId = this.selectedKnowledgeId;
            this.documentBusyId = doc.id;
            try {
                const response = await this.authFetch(`/knowledges/${encodeURIComponent(knowledgeId)}/documents/${encodeURIComponent(doc.id)}`, {
                    method: 'DELETE'
                });

                if (!response.ok) {
                    const error = await response.json().catch(() => ({}));
                    throw new Error(this.apiError(response, error, '删除失败'));
                }

                if (this.selectedKnowledgeId === knowledgeId) await this.loadDocuments();

            } catch (error) {
                this.setNotice('删除文档失败：' + error.message);
            } finally { this.documentBusyId = ''; }
        },

        chooseReplacement(doc) {
            this.replacingDocumentId = doc.id;
            this.$nextTick(() => this.$refs.replaceInput?.click());
        },
        async replaceDocument(event) {
            const file = event.target.files?.[0];
            const documentId = this.replacingDocumentId;
            const knowledgeId = this.selectedKnowledgeId;
            event.target.value = '';
            if (!file || !documentId || !knowledgeId) return;
            const validationError = this.validateFile(file);
            if (validationError) { this.setNotice(validationError); return; }
            if (!confirm(`确定用“${file.name}”替换此文档吗？替换开始后旧内容立即退出检索。`)) return;
            this.documentBusyId = documentId;
            try {
                const body = new FormData(); body.append('file', file);
                const response = await this.authFetch(`/knowledges/${encodeURIComponent(knowledgeId)}/documents/${encodeURIComponent(documentId)}/content`, {method: 'PUT', body});
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(this.apiError(response, data, '替换失败'));
                this.setNotice('替换任务已开始，旧内容已退出检索；状态会自动更新。');
                if (this.selectedKnowledgeId === knowledgeId) await this.loadDocuments();
            } catch (error) { this.setNotice(error.message); }
            finally { this.documentBusyId = ''; this.replacingDocumentId = ''; }
        },

        async retryDocument(doc) {
            const knowledgeId = this.selectedKnowledgeId;
            this.documentBusyId = doc.id;
            try {
                const response = await this.authFetch(`/knowledges/${encodeURIComponent(knowledgeId)}/documents/${encodeURIComponent(doc.id)}/retry`, {
                    method: 'POST'
                });
                const data = await response.json();
                if (!response.ok) throw new Error(this.apiError(response, data, '重试失败'));
                if (this.selectedKnowledgeId === knowledgeId) await this.loadDocuments();
            } catch (error) {
                this.setNotice('重试处理失败：' + error.message);
            } finally { this.documentBusyId = ''; }
        },

        documentStatusLabel(status) {
            return {
                pending: '等待处理', processing: '处理中', ready: '可检索',
                failed: '处理失败', deleting: '删除中', replacing: '替换中'
            }[status] || status;
        },

        getFileIcon(fileType) {
            if (fileType === '.pdf') {
                return 'fas fa-file-pdf';
            } else if (fileType === '.doc' || fileType === '.docx') {
                return 'fas fa-file-word';
            } else if (fileType === '.xls' || fileType === '.xlsx') {
                return 'fas fa-file-excel';
            }
            return 'fas fa-file';
        }
    },
    watch: {
        messages: {
            handler() {
                this.$nextTick(() => {
                    this.scrollToBottom();
                });
            },
            deep: true
        }
    }
}).mount('#app');
