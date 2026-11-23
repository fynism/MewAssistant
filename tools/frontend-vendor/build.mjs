import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

await build({
  entryPoints: [fileURLToPath(new URL('./components.js', import.meta.url))],
  outfile: fileURLToPath(new URL('../../frontend/vendor/element-plus/components.js', import.meta.url)),
  bundle: true,
  minify: true,
  format: 'iife',
  globalName: 'ElementPlus',
  legalComments: 'eof',
  plugins: [{
    name: 'vue-global',
    setup(build) {
      // The HTML uses Vue's compiler-enabled global build for in-DOM templates.
      build.onResolve({ filter: /^vue$/ }, () => ({ path: 'vue', namespace: 'vue-global' }));
      build.onLoad({ filter: /.*/, namespace: 'vue-global' }, () => ({
        contents: 'module.exports = globalThis.Vue;',
      }));
    },
  }],
});
