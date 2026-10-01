// Exercise actual bundled components with deterministic hooks/API doubles.
// No browser, real accounts, production DB, or extra dependencies required.
import { build } from 'esbuild';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const cells = [];
let cursor = 0;
let workspace;
let calls;
let api;
let failures;
let hooks;
const data = { dataset_id: 'data-1', name: 'test', headers: ['x'], rows: [['1']], profile: { row_count: 1, column_count: 1, columns: [] } };
const project = { project_id: 'project-1', name: 'test', dataset_version: 0 };
const node = (type, props) => ({ type, props: props || {} });

const plugins = [{ name: 'test-doubles', setup(b) {
  b.onResolve({ filter: /^react(?:\/jsx-runtime)?$/ }, args => ({ path: args.path, namespace: 'double' }));
  b.onResolve({ filter: /\/lib\/api$/ }, () => ({ path: 'api', namespace: 'double' }));
  b.onResolve({ filter: /VirtualSpreadsheetGrid$/ }, () => ({ path: 'grid', namespace: 'double' }));
  b.onResolve({ filter: /lucide-react/ }, () => ({ path: 'icons', namespace: 'double' }));
  b.onResolve({ filter: /context\/WorkspaceContext$/ }, () => ({ path: 'workspace', namespace: 'double' }));
  b.onLoad({ filter: /.*/, namespace: 'double' }, ({ path }) => {
    let contents;
    if (path === 'react') contents = 'export default globalThis.__hooks; export const {useState,useEffect,useMemo,useRef,createContext,useContext}=globalThis.__hooks;';
    if (path === 'react/jsx-runtime') contents = 'export const jsx=globalThis.__node, jsxs=jsx, Fragment="fragment";';
    if (path === 'workspace') contents = 'export const useWorkspace=()=>globalThis.__workspace();';
    if (path === 'grid') contents = 'export const VirtualSpreadsheetGrid="grid";';
    if (path === 'icons') contents = 'export const ' + ['Upload','Table2','Plus','Trash2','Download','RefreshCw','CheckCircle2','AlertTriangle','Check','Info','Clipboard','FlaskConical','Lock','PlayCircle','SlidersHorizontal','Undo2','Redo2'].map(n => `${n}="${n}"`).join(',') + ';';
    if (path === 'api') contents = 'export const ' + ['getSampleDataset','pasteDataset','recommendMethods','runAnalysis','uploadDataset','clearAgentState','createProject','deleteProject','deleteDataset','deleteDatasetColumns','deleteDatasetRows','getAnalysis','getCurrentUserId','getDataset','getProject','getUserStorage','listProjects','patchDatasetCells','insertDatasetColumn','insertDatasetRows','reparseDatasetHeader','saveVariables','updateDataset','updateProjectWorkspace'].map(n => `${n}=(...args)=>globalThis.__api["${n}"](...args)`).join(',') + ';';
    return { contents, loader: 'js' };
  });
} }];

function reset() {
  cells.length = 0; cursor = 0; calls = []; failures = {};
  hooks = {
    useState(initial) {
      const i = cursor++;
      if (!(i in cells)) cells[i] = typeof initial === 'function' ? initial() : initial;
      return [cells[i], value => { cells[i] = typeof value === 'function' ? value(cells[i]) : value; }];
    },
    useRef(initial) { const i = cursor++; if (!(i in cells)) cells[i] = { current: initial }; return cells[i]; },
    useMemo(fn) { return fn(); }, useEffect() {},
    createContext() { return { Provider: 'provider' }; }, useContext() { return workspace; },
    createElement: (type, props, ...children) => node(type, { ...props, children }),
  };
  api = new Proxy({}, { get: (target, key) => key in target ? target[key] : key === 'getCurrentUserId' ? () => 'owner' : async (...args) => {
    calls.push([key, ...args]);
    if (failures[key]) throw new Error(failures[key]);
    if (key === 'createProject') return project;
    if (key === 'getProject') return project;
    if (key === 'updateProjectWorkspace') return { ...project, ...args[1] };
    return data;
  } });
  workspace = {
    activeProject: null, dataset: null, selectedVariables: null, selectedModel: null,
    modelOptions: {}, dataReviewStatus: 'pending',
    createNewProject: (...args) => api.createProject(...args),
    attachImportedDataset: async (...args) => {
      calls.push(['attach', ...args]);
      if (failures.attach) throw new Error(failures.attach);
      workspace.dataset = args[0];
    },
  };
}
async function bundle(entry) {
  const result = await build({ entryPoints: [entry], bundle: true, write: false, format: 'cjs', platform: 'node', jsx: 'automatic', plugins });
  const sandbox = { module: { exports: {} }, __hooks: hooks, __node: node, __workspace: () => workspace, __api: api,
    localStorage: { getItem: () => '{}', setItem() {} }, console };
  vm.runInNewContext(result.outputFiles[0].text, sandbox);
  sandbox.exports = sandbox.module.exports;
  return sandbox;
}
function walk(tree, predicate) {
  if (!tree || typeof tree !== 'object') return null;
  if (Array.isArray(tree)) { for (const x of tree) { const result = walk(x, predicate); if (result) return result; } return null; }
  if (predicate(tree)) return tree;
  return walk(tree.props?.children, predicate);
}
function label(tree) { if (Array.isArray(tree)) return tree.map(label).join(''); if (typeof tree === 'object') return label(tree?.props?.children); return String(tree ?? ''); }
let passed = 0;
async function test(name, fn) { reset(); await fn(); passed++; console.log(`PASS ${name}`); }
async function ui() {
  const sandbox = await bundle('src/app/components/UploadPage.tsx');
  const render = () => { cursor = 0; return sandbox.exports.UploadPage({}); };
  let tree = render();
  return {
    render,
    inputName(value) { tree = render(); walk(tree, n => n.props?.['aria-label'] === '项目名称').props.onChange({ target: { value } }); },
    tabGrid() { tree = render(); walk(tree, n => n.type === 'button' && label(n) === '粘贴表格').props.onClick(); },
    async click(text) {
      tree = render(); const b = walk(tree, n => n.type === 'button' && label(n).includes(text)); assert.ok(b, text);
      await b.props.onClick({ stopPropagation() {} });
      for (let i = 0; i < 12; i++) await new Promise(resolve => setImmediate(resolve));
      return render();
    },
  };
}

await test('sample without project name warns beside input; no API request', async () => {
  const page = await ui(); const tree = await page.click('加载示例数据');
  assert.ok(label(tree).includes('请先填写项目名称')); assert.ok(walk(tree, n => n.props?.role === 'alert')); assert.equal(calls.length, 0);
});
await test('empty table rejected before creating a project', async () => {
  const page = await ui(); page.inputName('test'); page.tabGrid();
  assert.ok(label(await page.click('检查并导入')).includes('至少填写一行')); assert.equal(calls.length, 0);
});
await test('sample creates project, then attaches to explicit returned project', async () => {
  const page = await ui(); page.inputName('test'); await page.click('加载示例数据');
  assert.deepEqual(calls.map(x => x[0]), ['createProject', 'getSampleDataset', 'attach']); assert.equal(calls[2][2].project_id, project.project_id);
});
await test('existing project imports without creating another', async () => {
  workspace.activeProject = project; const page = await ui(); await page.click('加载示例数据');
  assert.deepEqual(calls.map(x => x[0]), ['getSampleDataset', 'attach']);
});
await test('project creation failures shown rather than unhandled rejection', async () => {
  failures.createProject = '项目创建失败测试'; const page = await ui(); page.inputName('test');
  assert.ok(label(await page.click('加载示例数据')).includes('项目创建失败测试')); assert.equal(calls.length, 1);
});
await test('attachment failure is not reported as import success', async () => {
  failures.attach = '项目保存失败测试'; const page = await ui(); page.inputName('test');
  assert.ok(label(await page.click('加载示例数据')).includes('项目保存失败测试')); assert.equal(workspace.dataset, null);
});
await test('manual table: blank name preserves rows and warns, then imports', async () => {
  const page = await ui(); page.tabGrid();
  let tree = page.render(); const cell = walk(tree, n => n.type === 'input' && n.props?.className === 'w-full bg-transparent focus:outline-none text-foreground');
  assert.ok(cell, 'editable data cell'); cell.props.onChange({ target: { value: '1' } });
  assert.ok(label(await page.click('检查并导入')).includes('请先填写项目名称')); assert.equal(calls.length, 0);
  page.inputName('manual'); await page.click('检查并导入');
  assert.deepEqual(calls.map(x => x[0]), ['createProject', 'pasteDataset', 'attach']);
  assert.equal(calls[1][1].rows.length, 1); assert.equal(calls[1][1].rows[0][0], '1');
});
await test('file upload creates project and awaits attachment', async () => {
  const page = await ui(); page.inputName('file'); const tree = page.render();
  const input = walk(tree, n => n.type === 'input' && n.props?.type === 'file');
  input.props.onChange({ target: { files: [{ name: 'a.csv' }] }, currentTarget: { value: 'a.csv' } });
  for (let i=0;i<12;i++) await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls.map(x => x[0]), ['createProject', 'uploadDataset', 'attach']);
});
await test('double-click cannot create duplicate projects', async () => {
  const page = await ui(); page.inputName('test'); const tree = page.render();
  const b = walk(tree, n => n.type === 'button' && label(n) === '加载示例数据');
  b.props.onClick({ stopPropagation() {} }); b.props.onClick({ stopPropagation() {} });
  for (let i=0;i<12;i++) await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.filter(x => x[0] === 'createProject').length, 1);
});

// Provider persistence tests intentionally reuse the pre-create render's method.
await test('provider persists new project attachment despite stale render closure', async () => {
  const sandbox = await bundle('src/app/context/WorkspaceContext.tsx'); cursor = 0;
  const value = sandbox.exports.WorkspaceProvider({}).props.value;
  const created = await value.createNewProject('test');
  await value.attachImportedDataset(data, created);
  assert.equal(calls.find(x => x[0] === 'updateProjectWorkspace')[1], project.project_id);
  assert.equal(cells[0].activeProject.dataset_id, data.dataset_id); assert.equal(cells[0].dataset.dataset_id, data.dataset_id);
});
await test('provider surfaces persistence failure without local false success', async () => {
  const sandbox = await bundle('src/app/context/WorkspaceContext.tsx'); cursor = 0;
  const value = sandbox.exports.WorkspaceProvider({}).props.value;
  const created = await value.createNewProject('test'); failures.updateProjectWorkspace = 'save failed';
  await assert.rejects(value.attachImportedDataset(data, created), /save failed/); assert.equal(cells[0].dataset, null);
});
await test('slow import does not contaminate another open project', async () => {
  const sandbox = await bundle('src/app/context/WorkspaceContext.tsx'); cursor = 0;
  const value = sandbox.exports.WorkspaceProvider({}).props.value;
  await value.createNewProject('test'); value.setActiveProject({ project_id: 'other', name: 'other' });
  await value.attachImportedDataset(data, project);
  assert.equal(cells[0].activeProject.project_id, 'other'); assert.equal(cells[0].dataset, null);
});
await test('overlapping project loads keep latest selection, not the slow previous project', async () => {
  const pending = new Map();
  api.getProject = id => new Promise(resolve => pending.set(id, resolve));
  const sandbox = await bundle('src/app/context/WorkspaceContext.tsx'); cursor = 0;
  const value = sandbox.exports.WorkspaceProvider({}).props.value;
  const a = value.selectProject('a').catch(error => error);
  const b = value.selectProject('b');
  pending.get('b')({project_id:'b',name:'B'}); await b;
  pending.get('a')({project_id:'a',name:'A'}); assert.match((await a).message, /切换已取消/);
  assert.equal(cells[0].activeProject.project_id, 'b');
});
console.log(`${passed} import UI/provider regression checks passed.`);
