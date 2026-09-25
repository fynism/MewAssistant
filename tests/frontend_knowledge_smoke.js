// Runs the real Vue methods with an in-memory HTTP stub, without a browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

let component;
const source = fs.readFileSync('frontend/script.js', 'utf8');
vm.runInNewContext(source, {
  Vue: { createApp(options) { component = options; return { mount() {} }; } },
  localStorage: { getItem() { return ''; } },
  FormData, Blob, setTimeout() {}, confirm() { return true; }, console,
});

const app = Object.assign(component.data(), component.methods);
Object.defineProperty(app, 'isAuthenticated', { get() { return true; } });
app.currentUser = { username: 'alice', role: 'user' };
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
  assert.equal(app.activeNav, 'settings');
  assert.deepEqual(requests.slice(0, 2), [
    'GET /knowledges', `GET /knowledges/${knowledgeId}/documents`,
  ]);
  app.selectedFile = new Blob(['test']);
  await app.uploadDocument();
  assert(requests.includes(`POST /knowledges/${knowledgeId}/documents`));
  await app.deleteDocument({ id: 'doc', filename: 'same.pdf' });
  assert(requests.includes(`DELETE /knowledges/${knowledgeId}/documents/doc`));
  assert(!requests.some(request => /(?:^|\s)\/documents(?:\b|\/)/.test(request)));
  console.log('Frontend knowledge attachment smoke passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
