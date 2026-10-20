// Real shipped Vue methods; simulate full-page OAuth returns without external services.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');

function page(url, responder, oldToken = '') {
  const parsed = new URL(url), calls = [], navigations = [], stored = new Map();
  if (oldToken) stored.set('accessToken', oldToken);
  const location = {pathname:parsed.pathname,search:parsed.search,hash:parsed.hash,origin:parsed.origin,assign:value=>navigations.push(value)};
  let app, mounted;
  const context = vm.createContext({console,setTimeout,clearTimeout,Headers,URL,URLSearchParams,AbortController,DOMException,crypto:require('node:crypto').webcrypto});
  vm.runInContext(fs.readFileSync(path.join(root,'frontend/vendor/vue/vue.global.prod.js'),'utf8'),context);
  const vue = context.Vue;
  Object.assign(context, {
    Vue:{...vue,onMounted(fn) {mounted=fn;},onBeforeUnmount() {},createApp(definition) {return {use() {},component() {},mount() {app=vue.proxyRefs(definition.setup());}};}},
    ElementPlus:{ElMessage:{success() {},warning() {},error() {}},ElMessageBox:{}},ElementPlusLocaleZhCn:{},
    location,history:{replaceState(_,__,value) {const next=new URL(value,location.origin);location.pathname=next.pathname;location.search=next.search;location.hash=next.hash;}},
    window:{addEventListener() {},removeEventListener() {},scrollTo() {}},
    document:{documentElement:{classList:{contains:()=>false}},getElementById:()=>null},
    matchMedia:()=>({matches:true,addEventListener() {},removeEventListener() {}}),
    localStorage:{getItem:key=>stored.get(key),setItem:(key,value)=>stored.set(key,value),removeItem:key=>stored.delete(key)},
    sessionStorage:{getItem:()=>null,setItem() {}},
    fetch:async(url,options={})=>{calls.push({url,options});return responder(url,options);}
  });
  vm.runInContext(fs.readFileSync(path.join(root,'frontend/app.js'),'utf8'),context);
  return {get app() {return app;},mount:()=>mounted(),location,calls,navigations,stored};
}
const json = (data,status=200) => ({ok:status>=200&&status<300,status,headers:new Headers(),json:async()=>data});
const responder = async url => {
  if (url === '/auth/providers') return json({github:true});
  if (url === '/auth/github/start') return json({authorization_url:'https://github.com/login/oauth/authorize?state=fixture'});
  if (url === '/auth/github/complete') return json({access_token:'site-jwt',username:'gh_cat_123',role:'user'});
  if (url === '/account/api-keys') return json({items:[]});
  if (url === '/knowledge-settings') return json({allowed_extensions:[],max_upload_bytes:1});
  if (url === '/knowledges') return json({knowledges:[]});
  return json({});
};

(async () => {
  const login = page('https://platform.example/account',responder);
  await login.mount();
  assert.equal(login.app.githubEnabled,true);
  await login.app.startGitHubLogin();
  assert.equal(login.navigations[0],'https://github.com/login/oauth/authorize?state=fixture');
  assert.equal(JSON.parse(login.calls.find(call=>call.url==='/auth/github/start').options.body).next,'/account');

  const callback = page('https://platform.example/try?knowledge=mine&github=success',responder,'old-account-jwt');
  await callback.mount();
  assert.equal(callback.app.isAuthenticated,true);
  assert.equal(callback.app.displayName,'gh_cat_123');
  assert.equal(callback.app.role,'user');
  assert.equal(callback.app.authChecking,false);
  assert.equal(callback.stored.get('accessToken'),'site-jwt');
  assert.equal(callback.location.pathname,'/try');
  assert.equal(callback.location.search,'?knowledge=mine');
  const complete = callback.calls.find(call=>call.url==='/auth/github/complete');
  assert.equal(complete.options.method,'POST');
  assert.equal(complete.options.headers.has('Authorization'),false);
  assert.equal(callback.calls.filter(call=>call.url==='/auth/me').length,0);

  for (const error of ['cancelled','expired','failed','unavailable','<untrusted>']) {
    const failure = page('https://platform.example/account?github_error='+encodeURIComponent(error),responder);
    await failure.mount();
    assert.equal(failure.app.authOpen,true);
    assert.equal(failure.app.authMode,'login');
    assert.equal(failure.app.isAuthenticated,false);
    assert.ok(failure.app.authError.includes('GitHub'));
    assert.ok(!failure.app.authError.includes('<untrusted>'));
    assert.equal(failure.location.search,'');
    assert.ok(!failure.calls.some(call=>call.url==='/auth/github/complete'));
  }
  const expired = page('https://platform.example/?github=success',async(url,options)=>url==='/auth/github/complete'?json({detail:'登录已失效'},401):responder(url,options));
  await expired.mount();
  assert.equal(expired.app.authOpen,true);
  assert.equal(expired.app.authError,'登录已失效');
  assert.equal(expired.app.isAuthenticated,false);
  assert.equal(expired.app.authChecking,false);

  const unavailable = page('https://platform.example/',async(url,options)=>url==='/auth/github/start'?json({detail:'请稍后重试'},503):responder(url,options));
  await unavailable.mount(); await unavailable.app.startGitHubLogin();
  assert.equal(unavailable.app.githubLoading,false);
  assert.equal(unavailable.app.authError,'请稍后重试');
  assert.equal(unavailable.navigations.length,0);

  const disabled = page('https://platform.example/',async(url,options)=>url==='/auth/providers'?json({github:false}):responder(url,options));
  await disabled.mount(); await disabled.app.startGitHubLogin();
  assert.equal(disabled.app.githubEnabled,false);
  assert.equal(disabled.navigations.length,0);
  console.log('PASS: GitHub login start, return page, account replacement, token storage, cancellation, expiry, safe errors, disabled provider.');
})().catch(error=>{console.error(error);process.exitCode=1;});
