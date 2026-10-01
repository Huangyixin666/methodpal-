import { useEffect, useState } from 'react';
import { listJobs, cancelJob, retryJob, BackgroundJob, getCurrentUserId } from '../lib/api';
import { useWorkspace } from '../context/WorkspaceContext';

export function TaskPanel({onNavigate, onEnterProject}: {onNavigate: (page: string) => void; onEnterProject: (id: string, page?: string) => Promise<void>}) {
  const {activeProject} = useWorkspace();
  const [jobs, setJobs] = useState<BackgroundJob[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let alive = true;
    const owner = getCurrentUserId();
    let refreshing = false;
    const refresh = async () => {
      if (refreshing || !alive) return;
      refreshing = true;
      try {
        const res = await listJobs(activeProject?.project_id);
        if (alive && owner === getCurrentUserId()) setJobs(res.items || []);
      } catch { /* A failed poll must not clear saved tasks or duplicate the panel. */ }
      finally { refreshing = false; }
    };
    refresh(); const timer = setInterval(refresh, 2000);
    window.addEventListener('methodpal:jobs', refresh);
    return () => {alive = false; clearInterval(timer); window.removeEventListener('methodpal:jobs', refresh);};
  }, [activeProject?.project_id]);
  const active = jobs.filter(j => ['queued', 'running'].includes(j.status));
  if (!jobs.length) return null;
  async function act(job: BackgroundJob, action: string) {
    try {
      setError('');
      if (action === 'cancel') await cancelJob(job.job_id);
      if (action === 'retry') await retryJob(job.job_id);
      if (action === 'open' && job.project_id) {
        await onEnterProject(job.project_id, job.kind === 'report' ? 'report' : job.result?.status === 'waiting_for_user' ? 'upload' : 'results');
      }
      window.dispatchEvent(new Event('methodpal:jobs'));
    } catch (e: any) {setError(e.message || '任务操作失败');}
  }
  return <section data-testid="server-task-panel" aria-label="服务器任务" className="shrink-0 border-b border-border bg-card px-6 py-2 text-xs max-h-48 overflow-y-auto">
    <button aria-expanded={expanded} onClick={() => setExpanded(!expanded)} className={active.length ? 'text-primary' : 'text-muted-foreground'}>服务器任务 · {active.length ? `${active.length} 个进行中` : '暂无进行中的任务'} · {expanded ? '收起' : '查看进度和历史'}</button>
    {(expanded ? jobs.slice(0, 8) : active.slice(0, 2)).map(job => <div key={job.job_id} className="flex items-center gap-3 py-1 flex-wrap">
      <span>{job.kind === 'report' ? '报告' : job.kind === 'agent' ? 'Agent' : '分析'}：{job.stage} {['queued','running'].includes(job.status) ? `${job.progress}%` : ''}</span>
      {job.error && <span className="text-amber-700">{job.error}</span>}
      {job.workspace_changed && <span className="text-amber-700">历史任务：当前分析配置已改变</span>}
      {['queued','running'].includes(job.status) && <button onClick={() => act(job,'cancel')} className="text-red-600">取消任务</button>}
      {['failed','cancelled','interrupted'].includes(job.status) && <button onClick={() => act(job,'retry')} className="text-primary">重试</button>}
      {job.status === 'completed' && <button onClick={() => act(job,'open')} className="text-primary">{job.workspace_changed ? '进入当前项目' : '打开结果'}</button>}
    </div>)}
    {error && <p className="text-red-600">{error}</p>}
  </section>;
}
