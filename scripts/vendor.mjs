import { copyFileSync, mkdirSync } from 'node:fs';
mkdirSync('frontend/vendor', { recursive: true });
for (const [from, to] of [
  ['node_modules/marked/lib/marked.umd.js', 'marked.js'],
  ['node_modules/dompurify/dist/purify.min.js', 'purify.js'],
  ['node_modules/marked/LICENSE', 'marked.LICENSE.md'],
  ['node_modules/dompurify/LICENSE', 'dompurify.LICENSE'],
]) copyFileSync(from, `frontend/vendor/${to}`);
