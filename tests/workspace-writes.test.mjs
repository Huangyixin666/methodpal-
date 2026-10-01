import { build } from 'esbuild';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const output = await build({ entryPoints:['src/app/lib/workspaceWrites.ts'],bundle:true,write:false,format:'cjs',platform:'node' });
const sandbox={module:{exports:{}}}; vm.runInNewContext(output.outputFiles[0].text,sandbox);
const {createWorkspaceWriteQueue}=sandbox.module.exports;
const tick=()=>new Promise(resolve=>setImmediate(resolve));
let owner='a';
const queue=createWorkspaceWriteQueue(()=>owner);
let release; const calls=[];
const first=queue.enqueue('project',async()=>{calls.push('first');await new Promise(r=>{release=r;});});
const second=queue.enqueue('project',async()=>{calls.push('second');});
let ready=false; const barrier=queue.flush('project').then(()=>{ready=true;});
await tick(); assert.deepEqual(calls,['first']); assert.equal(ready,false);
release(); await Promise.all([first,second,barrier]); assert.deepEqual(calls,['first','second']); assert.equal(ready,true);
console.log('PASS autosaves serialize and run waits for both writes');

let releaseA; const waiting=queue.enqueue('p',async()=>{await new Promise(r=>{releaseA=r;});});
await queue.enqueue('q',async()=>{calls.push('independent');});
await queue.flush('q'); releaseA(); await waiting;
console.log('PASS independent projects do not block one another');

await queue.enqueue('failed',async()=>{throw new Error('offline');}).catch(()=>{});
await assert.rejects(queue.flush('failed'),/配置保存失败/);
await queue.enqueue('failed',async()=>{}); await queue.flush('failed');
console.log('PASS save failure blocks analysis and explicit resave recovers');

let releaseOld; const old=queue.enqueue('account',async()=>{await new Promise(r=>{releaseOld=r;});});
let incorrect=false; const late=queue.enqueue('account',async()=>{incorrect=true;}).catch(e=>e);
const flushOld=queue.flush('account').catch(e=>e);
await tick();owner='b'; releaseOld();await old;
assert.match((await late).message,/账户已变化/);assert.match((await flushOld).message,/账户已变化/);assert.equal(incorrect,false);
await queue.enqueue('account',async()=>{});await queue.flush('account');
console.log('PASS queued writes cannot cross accounts');
