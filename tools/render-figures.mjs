/** DIAGRAMPLAY_DIR로 지정한 고정 커밋에서 문서 그림 22개를 두 번 생성해 대조한 뒤 저장한다. 실패 시 종료 코드는 1이다. */
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const RENDERER_COMMIT = '0f35b9b1c400355e769f8302a3f3f348a07d69f5';
const ROOT = fileURLToPath(new URL('../', import.meta.url));
const ASSETS = path.join(ROOT, 'docs/assets');
const NAMES = Object.freeze([
  'architecture', 'storage-layout', 'storage-publication',
  'page-reclamation', 'read-publish-locks', 'transaction-queue', 'queue-lifecycle',
]);

async function main() {
  const renderer = process.env.DIAGRAMPLAY_DIR;
  if (!renderer) throw new Error('DIAGRAMPLAY_DIR is required');
  const head = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: renderer, encoding: 'utf8' }).trim();
  if (head !== RENDERER_COMMIT) throw new Error(`renderer commit must be ${RENDERER_COMMIT}`);
  const status = execFileSync('git', ['status', '--porcelain', '--untracked-files=no'], { cwd: renderer, encoding: 'utf8' });
  if (status) throw new Error('renderer tracked files must be clean');
  const api = await import(pathToFileURL(path.resolve(renderer, 'src/index.js')));
  const { reflowFigure } = await import(pathToFileURL(path.resolve(renderer, 'src/build.js')));
  const first = await generate({ ...api, reflowFigure });
  const second = await generate({ ...api, reflowFigure });
  assert.deepEqual(second, first, 'two generations must be byte-identical');
  assert.equal(first.size, 22);
  for (const [name, content] of first) writeFileSync(path.join(ASSETS, name), content);
  console.log(JSON.stringify({ renderer: head, node: process.version, outputs: [...first].map(([name, content]) => ({ name, sha256: createHash('sha256').update(content).digest('hex') })) }, null, 2));
}

async function generate(api) {
  const outputs = new Map();
  for (const [index, name] of NAMES.entries()) {
    const source = readFileSync(path.join(ASSETS, `${name}.diagramplay`), 'utf8');
    const options = { baseDir: ASSETS, strict: true };
    if (index < 5) options.layoutWidth = 1000;
    const reference = await api.buildFigure(source, options);
    const narrow = await api.reflowFigure(reference, { layoutWidth: 390, strict: true });
    assert.strictEqual(narrow.figure, reference.figure);
    assert.equal(narrow.figure.source, source);
    assert.deepEqual(clock(narrow.timeline), clock(reference.timeline));
    await addViews(outputs, { api, name, index, reference, narrow });
  }
  return outputs;
}

function clock(timeline) {
  return JSON.parse(JSON.stringify(timeline, (key, value) =>
    key === 'chipPath' ? undefined : value));
}

async function addViews(outputs, { api, name, index, reference, narrow }) {
  const wideOptions = index < 5 ? { isStatic: index < 2, name } : { name };
  const narrowOptions = index < 5 ? { isStatic: index < 2 } : { name };
  outputs.set(`${name}.svg`, await api.toSvg(reference, wideOptions));
  outputs.set(`${name}-narrow.svg`, await api.toSvg(narrow, narrowOptions));
  if (index < 2) return;
  outputs.set(`${name}.html`, await api.toHtml(reference, name));
  if ([2, 5, 6].includes(index)) outputs.set(`${name}-narrow.html`, await api.toHtml(narrow, name));
}

await main();
