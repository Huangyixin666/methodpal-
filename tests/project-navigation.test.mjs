import { build } from 'esbuild';
import vm from 'node:vm';
import assert from 'node:assert/strict';

let slots, cursor, effects, workspace, api, owner;
const jsx = (type, props, key) => ({ type, props: props || {}, key });
function reset() {
  slots = []; cursor = 0; effects = []; owner = 'alice';
  workspace = { userId: owner, activeProject: {project_id:'remembered', name:'旧项目'}, dataset:null, projects:[],
    selectedModel:null, selectedVariables:null, analysisResult:null, storageDir:'private',
    selectProject: async id => { workspace.activeProject = {project_id:id, name:id}; },
    createNewProject: async () => ({project_id:'created', name:'新项目'}) };
  api = { getCurrentUserId: () => owner, getCurrentUsername: () => 'alice', logout: async () => {} };
}
function walk(tree, fn) {
  if (!tree || typeof tree !== 'object') return null;
  if (Array.isArray(tree)) { for(const item of tree) { const result = walk(item, fn); if(result) return result; } return null; }
  return fn(tree) ? tree : walk(tree.props?.children, fn);
}
function text(tree) { if(Array.isArray(tree)) return tree.map(text).join(''); if(typeof tree?.type==='function')return text(tree.type(tree.props)); return typeof tree === 'object' ? text(tree?.props?.children) : String(tree ?? ''); }
const tick = async () => { for(let i=0;i<8;i++) await new Promise(resolve => setImmediate(resolve)); };
const icons = ['Search','Upload','Play','Clock','ArrowRight','TrendingUp','Database','FileCheck','Users','Plus','Trash2','Loader2','Send','Sparkles','ChevronDown','CornerDownLeft','Wand2','ListChecks','SlidersHorizontal','PlayCircle','FolderOpen'];
async function load(entry) {
  const hooks = {
    useState(initial) { const i=cursor++; if(!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
      return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }]; },
    useRef(initial) { const i=cursor++; if(!(i in slots)) slots[i]={current:initial}; return slots[i]; },
    useMemo(fn) { return fn(); },
    useEffect(fn, deps) {
      const i=cursor++;const previous=slots[i];
      if (!previous || !deps || deps.some((value,index)=>value!==previous.deps?.[index])) {
        slots[i]={deps, cleanup:previous?.cleanup};
        effects.push(()=>{previous?.cleanup?.();slots[i].cleanup=fn();});
      }
    },
  };
  const bundle = await build({ entryPoints:[entry], bundle:true, write:false, format:'cjs', platform:'node', jsx:'automatic', plugins:[{
    name:'test', setup(b) {
      b.onResolve({filter:/^react(?:\/jsx-runtime)?$/}, args => ({path:args.path,namespace:'test'}));
      b.onResolve({filter:/\/lib\/api$|^\.\/api$/}, () => ({path:'api',namespace:'test'}));
      b.onResolve({filter:/context\/WorkspaceContext$/}, () => ({path:'workspace',namespace:'test'}));
      b.onResolve({filter:/lucide-react/}, () => ({path:'icons',namespace:'test'}));
      if(entry.endsWith('/App.tsx')) b.onResolve({filter:/\.\/components\//}, args => ({path:args.path.split('/').pop(),namespace:'test'}));
      b.onResolve({filter:/AccountSettings$/}, () => ({path:'AccountSettings',namespace:'test'}));
      b.onLoad({filter:/.*/,namespace:'test'}, ({path}) => {
        let contents;
        if(path==='react') contents='export const {useState,useRef,useEffect,useMemo}=globalThis.hooks;';
        else if(path==='react/jsx-runtime') contents='export const jsx=globalThis.jsx,jsxs=jsx,Fragment="fragment";';
        else if(path==='workspace') contents='export const WorkspaceProvider="WorkspaceProvider",useWorkspace=()=>globalThis.workspace();';
        else if(path==='icons') contents='export const '+icons.map(n=>`${n}="${n}"`).join(',')+';';
        else if(path==='api') contents='export const api=globalThis.api; export const getCurrentUserId=()=>globalThis.api.getCurrentUserId(),getCurrentUsername=()=>globalThis.api.getCurrentUsername(),logout=(...a)=>globalThis.api.logout(...a),saveAgentHistory=(...a)=>globalThis.api.saveAgentHistory(...a);';
        else { const name=path==='ModelSelectionPage'?'ModelSelectionDialog':path; contents=`export const ${name}="${name}";`; }
        return {contents,loader:'js'};
      });
    }
  }]});
  const storage=new Map();
  const sandbox={ module:{exports:{}}, hooks, jsx, workspace:()=>workspace, api,
    console, setTimeout, clearTimeout,
    localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    document:{getElementById:()=>({focus(){},scrollIntoView(){}})}, window:{confirm:()=>true} };
  vm.runInNewContext(bundle.outputFiles[0].text,sandbox);
  return sandbox.module.exports;
}
let passed=0;
async function test(name, fn) {reset();await fn();passed++;console.log('PASS',name);}

await test('wide correlation tables page rows and columns without removing values',async()=>{
  const {PagedDataTable}=await load('src/app/components/PagedDataTable.tsx');
  const rows=Array.from({length:100},(_,i)=>({variable:`row${i}`,...Object.fromEntries(Array.from({length:100},(_,j)=>[`c${j}`,`${i}:${j}`]))}));
  const props={title:'相关分析结果',rows,fixedColumn:'variable',renderValue:v=>String(v)};
  let tree=PagedDataTable(props);
  assert.ok(text(tree).includes('显示 1–50 / 100 行'));
  assert.equal(walk(tree,n=>n.type==='tbody').props.children.length,50);
  assert.equal(walk(tree,n=>n.type==='thead').props.children.props.children.length,13);
  walk(tree,n=>n.props?.['aria-label']==='相关分析结果下一组列').props.onClick();cursor=0;tree=PagedDataTable(props);
  assert.ok(text(tree).includes('c12'));assert.ok(text(tree).includes('variable'));assert.ok(text(tree).includes('row0'));
  walk(tree,n=>n.props?.['aria-label']==='相关分析结果下一页').props.onClick();cursor=0;tree=PagedDataTable(props);
  assert.ok(text(tree).includes('显示 51–100 / 100 行'));assert.ok(text(tree).includes('50:12'));
  assert.equal(rows[99].c99,'99:99');
});

await test('lobby shows no project page or project task panel, even with remembered project', async()=>{
  const {AppShell}=await load('src/app/App.tsx'); const tree=AppShell();
  assert.ok(walk(tree,n=>n.type==='HomePage')); assert.equal(walk(tree,n=>n.type==='UploadPage'),null);
  assert.equal(walk(tree,n=>n.type==='TaskPanel'),null);
  assert.equal(walk(tree,n=>n.type==='AiPanel').key,'alice:no_project');
  assert.equal(walk(tree,n=>n.type==='ModelSelectionDialog').props.open,false);
});
await test('lobby cannot bypass project entry through navigation or model button', async()=>{
  const {AppShell}=await load('src/app/App.tsx'); let tree=AppShell();
  walk(tree,n=>n.type==='Sidebar').props.onNavigate('upload');
  walk(tree,n=>n.type==='AiPanel').props.onOpenModelSelector(); cursor=0;tree=AppShell();
  assert.ok(walk(tree,n=>n.type==='HomePage')); assert.equal(walk(tree,n=>n.type==='ModelSelectionDialog').props.open,false);
});
await test('explicit entry, project page changes, return, and another project use independent scopes', async()=>{
  const {AppShell}=await load('src/app/App.tsx'); let tree=AppShell();
  await walk(tree,n=>n.type==='HomePage').props.onEnterProject('a'); cursor=0;tree=AppShell();
  assert.equal(walk(tree,n=>n.type==='AiPanel').key,'alice:a'); assert.ok(walk(tree,n=>n.type==='UploadPage'));
  walk(tree,n=>n.type==='Sidebar').props.onNavigate('charts'); cursor=0;tree=AppShell();
  assert.ok(walk(tree,n=>n.type==='ChartsPage'));assert.equal(walk(tree,n=>n.type==='AiPanel').key,'alice:a');
  walk(tree,n=>n.type==='TopBar').props.onReturnHome();cursor=0;tree=AppShell();
  assert.ok(walk(tree,n=>n.type==='HomePage'));assert.equal(walk(tree,n=>n.type==='AiPanel').key,'alice:no_project');
  await walk(tree,n=>n.type==='HomePage').props.onEnterProject('b','results');cursor=0;tree=AppShell();
  assert.ok(walk(tree,n=>n.type==='ResultsPage'));assert.equal(walk(tree,n=>n.type==='AiPanel').key,'alice:b');
});
await test('project shell has unique sibling keys during repeated edits and page changes', async()=>{
  const {AppShell}=await load('src/app/App.tsx'); let tree=AppShell();
  await walk(tree,n=>n.type==='HomePage').props.onEnterProject('a');
  for (let index=0; index<20; index++) {
    workspace.activeProject={...workspace.activeProject, progress:index, updated_at:String(index)};
    cursor=0; tree=AppShell();
    function check(node) {
      if (!node || typeof node!=='object') return;
      if (Array.isArray(node)) { node.forEach(check); return; }
      const children=node.props?.children;
      if (Array.isArray(children)) {
        const keys=children.filter(child=>child && typeof child==='object' && child.key!=null).map(child=>child.key);
        assert.equal(new Set(keys).size,keys.length,'duplicate React sibling keys can accumulate task bars');
      }
      check(children);
    }
    check(tree);
    walk(tree,n=>n.type==='Sidebar').props.onNavigate(index%2 ? 'results' : 'upload');
  }
});
await test('failed project entry keeps lobby and exposes error', async()=>{
  workspace.selectProject=async()=>{throw new Error('不存在的项目');};
  const {AppShell}=await load('src/app/App.tsx');let tree=AppShell();
  await walk(tree,n=>n.type==='HomePage').props.onEnterProject('bad');cursor=0;tree=AppShell();
  assert.ok(walk(tree,n=>n.type==='HomePage'));assert.ok(text(tree).includes('不存在的项目'));
});
await test('late entry cannot reopen project after returning to lobby', async()=>{
  let resolve; workspace.selectProject=()=>new Promise(r=>{resolve=r;});
  const {AppShell}=await load('src/app/App.tsx');let tree=AppShell();
  const entering=walk(tree,n=>n.type==='HomePage').props.onEnterProject('a');
  walk(tree,n=>n.type==='Sidebar').props.onNavigate('home');resolve();await entering;cursor=0;tree=AppShell();
  assert.ok(walk(tree,n=>n.type==='HomePage'));
});
await test('sidebar hides project functions in lobby and offers new project entry',async()=>{
  const {Sidebar}=await load('src/app/components/Sidebar.tsx');
  const tree=Sidebar({currentPage:'home',onNavigate(){},onEnterProject:async()=>{}});
  for(const label of ['数据工作区','分析结果','图表分析','AI 解释','报告导出']) assert.equal(walk(tree,n=>n.type==='button'&&text(n).endsWith(label)),null);
  assert.equal(walk(tree,n=>n.type==='select'),null);
  assert.ok(text(tree).includes('我的项目'));
  walk(tree,n=>n.type==='button'&&text(n).endsWith('新建项目')).props.onClick();
});
await test('project sidebar and top bar offer return without leaking project label to lobby',async()=>{
  const {Sidebar}=await load('src/app/components/Sidebar.tsx');let tree=Sidebar({currentPage:'upload',onNavigate(){},onEnterProject:async()=>{}});
  assert.equal(Boolean(walk(tree,n=>n.type==='button'&&text(n).endsWith('数据工作区')).props.disabled),false);
  assert.ok(walk(tree,n=>n.type==='button'&&text(n).endsWith('返回工作台')));
  reset();const {TopBar}=await load('src/app/components/TopBar.tsx'); tree=TopBar({currentPage:'home'});
  assert.ok(!text(tree).includes('旧项目'));
});
await test('creating project uses its returned ID to enter and catches errors',async()=>{
  workspace.activeProject=null;
  const {HomePage}=await load('src/app/components/HomePage.tsx');let entered;
  const props={onEnterProject:async id=>{entered=id;}};let tree=HomePage(props);
  walk(tree,n=>n.props?.id==='methodpal-new-project').props.onChange({target:{value:'研究'}});
  cursor=0;tree=HomePage(props); walk(tree,n=>n.type==='form').props.onSubmit({preventDefault(){}});await tick();assert.equal(entered,'created');
});
await test('workbench search filters project names and goals without changing project scope',async()=>{
  workspace.projects=[{project_id:'a',name:'收入研究',research_goal:'教育与收入',updated_at:'2026-10-01T10:00:00',progress:30},
    {project_id:'b',name:'睡眠研究',research_goal:'心理健康',updated_at:'2026-09-30T10:00:00',progress:0}];
  const {HomePage}=await load('src/app/components/HomePage.tsx');const props={onEnterProject:async()=>{}};
  let tree=HomePage(props);assert.ok(text(tree).includes('2026年10月1日更新'));
  walk(tree,n=>n.props?.['aria-label']==='搜索项目').props.onChange({target:{value:'心理'}});
  cursor=0;tree=HomePage(props);assert.ok(text(tree).includes('睡眠研究'));assert.ok(!text(tree).includes('收入研究'));
  walk(tree,n=>n.props?.['aria-label']==='搜索项目').props.onChange({target:{value:'不存在'}});
  cursor=0;tree=HomePage(props);assert.ok(text(tree).includes('没有找到匹配的项目'));assert.equal(workspace.activeProject.project_id,'remembered');
});
await test('workbench empty state provides a first project entry and a keyboard-submit form',async()=>{
  workspace.activeProject=null;
  const {HomePage}=await load('src/app/components/HomePage.tsx');const tree=HomePage({onEnterProject:async()=>{}});
  assert.ok(walk(tree,n=>n.type==='form'));assert.equal(walk(tree,n=>n.props?.['aria-label']==='搜索项目'),null);
  assert.equal(walk(tree,n=>n.props?.id==='methodpal-new-project').props.required,true);
  assert.ok(text(walk(tree,n=>n.type==='button'&&n.props.type==='submit')).includes('创建并进入项目'));
});
await test('history writes are ordered per scope and return waits for latest snapshot',async()=>{
  const records=new Map(), calls=[]; let release;
  api.saveAgentHistory=async(id,messages)=>{calls.push(id+':'+messages[0].content);if(messages[0].content==='old')await new Promise(r=>{release=r;});records.set(id,messages);};
  const {persistChatHistory,waitForChatHistory}=await load('src/app/lib/chatHistoryPersistence.ts');
  const old=persistChatHistory(owner,'a',[{role:'user',content:'old'}]);await tick();
  const next=persistChatHistory(owner,'a',[{role:'user',content:'new'}]);
  await persistChatHistory(owner,'no_project',[{role:'user',content:'lobby'}]);
  await persistChatHistory(owner,'b',[{role:'user',content:'other'}]);
  let done=false;const waiting=waitForChatHistory(owner,'a').then(()=>{done=true;});await tick();assert.equal(done,false);
  release();await Promise.all([old,next,waiting]);assert.equal(records.get('a')[0].content,'new');
  assert.equal(records.get('no_project')[0].content,'lobby');assert.equal(records.get('b')[0].content,'other');
  assert.ok(calls.indexOf('a:old')<calls.indexOf('a:new'));
});
await test('queued history is never sent after account switches',async()=>{
  let count=0;api.saveAgentHistory=async()=>{count++;};
  const {persistChatHistory}=await load('src/app/lib/chatHistoryPersistence.ts');
  const task=persistChatHistory('alice','a',[]);owner='bob';await task;assert.equal(count,0);
});
await test('actual chat panel restores lobby, A, B independently; clear affects only current scope',async()=>{
  const histories=new Map([
    ['no_project',[{role:'user',content:'LOBBY_ONLY',time:'10:00'}]],
    ['a',[{role:'user',content:'PROJECT_A_ONLY',time:'10:01'}]],
    ['b',[{role:'user',content:'PROJECT_B_ONLY',time:'10:02'}]],
  ]);
  api.getAgentHistory=async id=>({messages:histories.get(id)||[]});
  api.getAgentState=async()=>({mode:'assisted'});
  api.saveAgentHistory=async(id,messages)=>{histories.set(id,messages);};
  api.clearAgentState=async()=>{};
  api.agentMessage=async()=>({message:'工作台新回复',actions:[]});
  const {AiPanel}=await load('src/app/components/AiPanel.tsx');
  let currentPage='home'; const props=()=>({currentPage,onNavigate(){},onEnterProject:async()=>{}});
  const render=()=>{cursor=0;return AiPanel(props());};
  const settle=async()=>{let tree;for(let i=0;i<5;i++){tree=render();effects.splice(0).forEach(fn=>fn());await tick();}return tree;};
  const unmount=()=>{slots.forEach(slot=>slot?.cleanup?.());slots=[];effects=[];cursor=0;};
  let tree=await settle();assert.ok(text(tree).includes('LOBBY_ONLY'));assert.ok(!text(tree).includes('PROJECT_A_ONLY'));
  walk(tree,n=>n.type==='textarea').props.onChange({target:{value:'LOBBY_NEW_QUESTION'}});tree=render();
  await walk(tree,n=>n.type==='button'&&walk(n,k=>k.type==='Send')).props.onClick();await settle();
  unmount();workspace.activeProject={project_id:'a',name:'A'};currentPage='upload';tree=await settle();
  assert.ok(text(tree).includes('PROJECT_A_ONLY'));assert.ok(!text(tree).includes('LOBBY_ONLY'));assert.ok(!text(tree).includes('工作台新回复'));
  unmount();workspace.activeProject={project_id:'b',name:'B'};tree=await settle();
  assert.ok(text(tree).includes('PROJECT_B_ONLY'));assert.ok(!text(tree).includes('PROJECT_A_ONLY'));
  await walk(tree,n=>n.type==='button'&&text(n).endsWith('清空')).props.onClick();await settle();
  assert.ok(!histories.get('b').some(m=>m.content==='PROJECT_B_ONLY'));assert.equal(histories.get('a')[0].content,'PROJECT_A_ONLY');
  unmount();currentPage='home';tree=await settle();
  assert.ok(text(tree).includes('LOBBY_ONLY'));assert.ok(text(tree).includes('工作台新回复'));assert.ok(text(tree).includes('LOBBY_NEW_QUESTION'));
  assert.ok(!text(tree).includes('PROJECT_B_ONLY'));
  unmount();workspace.activeProject={project_id:'a',name:'A'};currentPage='results';tree=await settle();
  assert.ok(text(tree).includes('PROJECT_A_ONLY'));assert.ok(!text(tree).includes('LOBBY_ONLY'));unmount();
});
await test('late LLM answer after chat unmount cannot execute navigation or workspace changes',async()=>{
  let answer, navigations=0, writes=0;
  api.getAgentHistory=async()=>({messages:[]});api.getAgentState=async()=>({mode:'assisted'});api.saveAgentHistory=async()=>{};
  api.agentMessage=()=>new Promise(resolve=>{answer=resolve;});workspace.setAnalysisResult=()=>{writes++;};
  const {AiPanel}=await load('src/app/components/AiPanel.tsx');const props={currentPage:'home',onNavigate(){navigations++;},onEnterProject:async()=>{}};
  let tree;for(let i=0;i<4;i++){cursor=0;tree=AiPanel(props);effects.splice(0).forEach(fn=>fn());await tick();}
  walk(tree,n=>n.type==='textarea').props.onChange({target:{value:'正在提问'}});cursor=0;tree=AiPanel(props);
  const pending=walk(tree,n=>n.type==='button'&&walk(n,k=>k.type==='Send')).props.onClick();
  slots.forEach(slot=>slot?.cleanup?.());answer({message:'STALE_REPLY',execution_result:{analysis_id:'old'}});await pending;
  assert.equal(navigations,0);assert.equal(writes,0);
  assert.ok(!JSON.stringify(slots).includes('STALE_REPLY'));
});
await test('late chart generation after leaving project cannot navigate the new workspace',async()=>{
  workspace.activeProject={project_id:'a',name:'A'};let finish,navigations=0;
  api.getAgentHistory=async()=>({messages:[{role:'ai',content:'可生成图表',time:'10:00',actions:[{
    type:'generate_chart',label:'生成测试图',payload:{chart_type:'scatter',dataset_id:'data-a'}}]}]});
  api.getAgentState=async()=>({mode:'assisted'});api.saveAgentHistory=async()=>{};
  api.generateChart=()=>new Promise(resolve=>{finish=resolve;});
  const {AiPanel}=await load('src/app/components/AiPanel.tsx');
  const props={currentPage:'charts',onNavigate(){navigations++;},onEnterProject:async()=>{}};
  let tree;for(let i=0;i<4;i++){cursor=0;tree=AiPanel(props);effects.splice(0).forEach(fn=>fn());await tick();}
  const pending=walk(tree,n=>n.type==='button'&&text(n)==='生成测试图').props.onClick();
  slots.forEach(slot=>slot?.cleanup?.());finish({chart:{title:'旧项目图'}});await pending;
  assert.equal(navigations,0);
});
console.log(`${passed} project navigation/history checks passed.`);
