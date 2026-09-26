// Runs the real Vue methods with an in-memory HTTP stub, without a browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

let component;
const globalLocation = { pathname: '/try', search: '' };
const source = fs.readFileSync('frontend/script.js', 'utf8');
vm.runInNewContext(source, {
  Vue: { createApp(options) { component = options; return { mount() {} }; } },
  localStorage: { getItem() { return ''; } },
  window: { location: globalLocation, history: { pushState(_, __, path) { globalLocation.pathname = path; } } },
  fetch: async () => ({ ok: true, json: async () => ({ allowed_extensions: ['.pdf'], max_upload_bytes: 1000000 }) }),
  URLSearchParams, FormData, Blob, TextDecoder, AbortController, setTimeout() {}, clearTimeout() {}, confirm() { return true; }, console,
});

const app = Object.assign(component.data(), component.methods);
Object.defineProperty(app, 'isAuthenticated', { get() { return true; } });
Object.defineProperty(app, 'selectedKnowledge', { get() { return app.knowledges.find(item => item.id === app.selectedKnowledgeId); } });
app.currentUser = { username: 'alice', role: 'user' };
app.$nextTick = fn => fn();
app.$refs = { fileInput: { value: '' } };
const requests = [];
const knowledgeId = '11111111-1111-1111-1111-111111111111';
app.authFetch = async (url, options = {}) => {
  requests.push(`${options.method || 'GET'} ${url}`);
  const payload = url === '/knowledges'
    ? { knowledges: [{ id: knowledgeId, name: 'Private' }] }
    : url.endsWith('/documents') && (options.method || 'GET') === 'GET'
      ? { documents: [] }
      : url.endsWith('/documents') ? { id: 'doc', status: 'pending' }
      : { deleted: true };
  return { ok: true, json: async () => payload };
};

(async () => {
  app.handleUploadClick();
  await new Promise(setImmediate);
  assert.equal(app.page, '/workspace/knowledges');
  assert.deepEqual(requests.slice(0, 2), [
    'GET /knowledges', `GET /knowledges/${knowledgeId}/documents`,
  ]);
  app.selectedFile = new Blob(['test']); app.selectedFile.name = 'same.pdf';
  await app.uploadDocument();
  assert(requests.includes(`POST /knowledges/${knowledgeId}/documents`));
  await app.deleteDocument({ id: 'doc', filename: 'same.pdf' });
  assert(requests.includes(`DELETE /knowledges/${knowledgeId}/documents/doc`));
  assert(!requests.some(request => /(?:^|\s)\/documents(?:\b|\/)/.test(request)));
  const payloads = [];
  app.authFetch = async (url, options = {}) => {
    payloads.push([url, options.body && JSON.parse(options.body)]);
    if (url === '/chat/stream') return { ok: true, body: { getReader() { return { read: async () => ({ done: true }) }; } } };
    return { ok: true, json: async () => url.endsWith('retrieve') ? { status: 'no_match', results: [] } : { items: [], nextCursor: null } };
  };
  app.selectedKnowledgeIds = [knowledgeId];
  app.userInput = '测试问题';
  await app.handleSend();
  assert.equal(payloads[0][0], '/chat/stream');
  assert.deepEqual(JSON.parse(JSON.stringify(payloads[0][1].knowledge_ids)), [knowledgeId]);
  assert.equal(app.messages[0].knowledgeIds[0], knowledgeId);
  app.selectedKnowledgeIds = [];
  app.debugQuery = '测试检索';
  await app.runDebug('retrieve');
  assert.equal(payloads[1][0], '/tools/debug/retrieve');
  assert.deepEqual(JSON.parse(JSON.stringify(payloads[1][1].knowledgeIds)), []);
  assert.equal(app.debugResult.status, 'no_match');
  console.log('Frontend knowledge attachment smoke passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
