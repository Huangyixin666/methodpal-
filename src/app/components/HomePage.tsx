import { getCurrentUsername } from "../lib/api";
import { useRef, useState } from "react";
import { ArrowRight, Clock, Database, FileCheck, FolderOpen, Loader2, Plus, Search, Sparkles, Trash2, TrendingUp } from "lucide-react";
import { useWorkspace } from "../context/WorkspaceContext";
import type { ProjectResponse } from "../lib/api";

interface HomePageProps {
  onEnterProject: (projectId: string, page?: string) => Promise<void>;
  enteringProject?: boolean;
}

function projectStatus(project: ProjectResponse) {
  if (project.analysis_id) return { text: "已有结果", color: "text-emerald-700 bg-emerald-50" };
  if (project.dataset_id) return { text: "分析准备中", color: "text-primary bg-secondary" };
  return { text: "待上传数据", color: "text-amber-700 bg-amber-50" };
}

function updatedLabel(value?: string | null) {
  if (!value) return "刚刚创建";
  // Server timestamps currently omit a timezone. Display their date directly
  // rather than shifting it using the browser's local timezone.
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? `${match[1]}年${Number(match[2])}月${Number(match[3])}日更新` : "最近更新";
}

export function HomePage({ onEnterProject, enteringProject }: HomePageProps) {
  const { projects, activeProject, createNewProject, deleteExistingProject, refreshProjects, loadingProjects } = useWorkspace();
  const [name, setName] = useState("");
  const [goal, setGoal] = useState("");
  const [creating, setCreating] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const creatingRef = useRef(false);
  const deletingRef = useRef(false);
  const busy = Boolean(enteringProject || creating || deletingId);
  const filteredProjects = projects.filter(project =>
    [project.name, project.research_goal, project.description].some(value => value?.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())),
  );

  function focusCreate() {
    const input = document.getElementById("methodpal-new-project");
    input?.scrollIntoView({block: "center", behavior: "smooth"});
    input?.focus({preventScroll: true});
  }

  async function handleCreate() {
    if (!name.trim() || creatingRef.current || busy) return;
    creatingRef.current = true;
    setCreating(true);
    setError("");
    try {
      const project = await createNewProject(name.trim(), goal.trim());
      setName(""); setGoal("");
      await onEnterProject(project.project_id, "upload");
    } catch (e: any) { setError(e.message || "项目创建失败，请重试。"); }
    finally { creatingRef.current = false; setCreating(false); }
  }

  async function handleDeleteProject(project: ProjectResponse) {
    if (busy || deletingRef.current) return;
    if (!window.confirm(`确定要删除项目“${project.name}”吗？\n\n这会清空该项目的数据、变量设置、分析结果、图表、AI解释、报告草稿和 Agent 对话记录。此操作不可撤销。`)) return;
    deletingRef.current = true;
    setDeletingId(project.project_id); setError("");
    try { await deleteExistingProject(project.project_id); }
    catch (e: any) { setError(e.message || "项目删除失败，请重试。"); }
    finally { deletingRef.current = false; setDeletingId(null); }
  }

  const stats = [
    {label: "全部项目", value: projects.length, hint: "每个项目都是独立的研究空间", icon: FileCheck, color: "text-primary bg-secondary"},
    {label: "已导入数据", value: projects.filter(project => project.dataset_id).length, hint: "数据已保存，可以继续配置分析", icon: Database, color: "text-violet-600 bg-violet-50"},
    {label: "已有分析结果", value: projects.filter(project => project.analysis_id).length, hint: "随时回看结果、图表与报告", icon: TrendingUp, color: "text-emerald-600 bg-emerald-50"},
  ];

  return (
    <div className="w-full max-w-[1360px] mx-auto px-6 lg:px-9 py-8 lg:py-10 space-y-7">
      {error && <div role="alert" className="rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      <section className="flex items-start justify-between gap-6 flex-wrap">
        <div>
          <div className="mb-3 inline-flex items-center gap-1.5 text-xs font-medium text-primary"><Sparkles size={14} /> 你的研究，从这里开始</div>
          <h1 className="text-2xl lg:text-3xl font-semibold tracking-tight text-foreground">欢迎回来，{getCurrentUsername()}</h1>
          <p className="mt-3 text-sm leading-6 text-muted-foreground">把研究问题变成项目，让数据、分析和对话各归其位。</p>
        </div>
        <button onClick={focusCreate} disabled={busy} className="mt-1 inline-flex h-11 items-center gap-2 rounded-xl bg-primary px-5 text-sm font-medium text-primary-foreground shadow-sm shadow-primary/15 hover:opacity-90 transition-opacity disabled:opacity-50"><Plus size={16} /> 新建项目</button>
      </section>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {stats.map(({label, value, hint, icon: Icon, color}) => (
          <div key={label} className="rounded-2xl border border-border/70 bg-card px-5 py-5 shadow-sm shadow-slate-900/[0.02]">
            <div className="flex items-center justify-between"><p className="text-xs font-medium text-muted-foreground">{label}</p><div className={`h-9 w-9 grid place-items-center rounded-xl ${color}`}><Icon size={17} /></div></div>
            <p className="mt-1 text-3xl font-semibold tabular-nums text-foreground">{value}</p>
            <p className="mt-2 text-[11px] leading-5 text-muted-foreground">{hint}</p>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_320px] gap-6 items-start">
        <section className="min-w-0 space-y-4">
          <div className="flex items-center justify-between gap-3">
            <div><h2 className="text-base font-semibold">我的项目 <span className="ml-2 text-xs font-normal text-muted-foreground">{projects.length}</span></h2><p className="mt-1 text-xs text-muted-foreground">选择一个项目，继续你的研究</p></div>
            <button onClick={() => void refreshProjects()} disabled={loadingProjects || busy} className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs text-muted-foreground hover:bg-card hover:text-primary disabled:opacity-50"><Clock size={13} className={loadingProjects ? "animate-spin" : ""} />{loadingProjects ? "刷新中" : "刷新"}</button>
          </div>
          {projects.length > 0 && <div className="flex h-10 items-center gap-2 rounded-xl border border-border/70 bg-card px-3"><Search size={15} className="shrink-0 text-muted-foreground"/><input aria-label="搜索项目" value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索项目名称或研究目标" className="min-w-0 flex-1 bg-transparent text-xs outline-none placeholder:text-muted-foreground"/>{query && <button onClick={() => setQuery("")} className="text-xs text-muted-foreground hover:text-primary">清除</button>}</div>}

          {loadingProjects && projects.length === 0 ? <div role="status" className="rounded-2xl border border-border bg-card p-10 text-center text-sm text-muted-foreground">正在读取你的项目...</div> : projects.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-primary/20 bg-card px-6 py-12 text-center">
              <div className="mx-auto mb-4 h-14 w-14 grid place-items-center rounded-2xl bg-secondary text-primary"><FolderOpen size={25}/></div>
              <h3 className="text-sm font-semibold">开始你的第一个研究项目</h3>
              <p className="mx-auto mt-2 max-w-sm text-xs leading-6 text-muted-foreground">填写项目名称即可开始，研究目标可以稍后完善。<br/>每个项目的数据和助手对话都会独立保存。</p>
              <button onClick={focusCreate} className="mt-5 inline-flex items-center gap-1.5 text-xs font-medium text-primary">创建第一个项目 <ArrowRight size={13}/></button>
            </div>
          ) : filteredProjects.length === 0 ? <div role="status" className="rounded-2xl border border-border bg-card p-10 text-center text-sm text-muted-foreground">没有找到匹配的项目，请换个关键词试试。</div> : (
            <div className="grid grid-cols-1 min-[1600px]:grid-cols-2 gap-4">
              {filteredProjects.map(project => {
                const status = projectStatus(project);
                const progress = Math.max(0, Math.min(100, project.progress || 0));
                return <article key={project.project_id} className="group min-w-0 rounded-2xl border border-border/70 bg-card p-5 shadow-sm shadow-slate-900/[0.02] hover:border-primary/25 hover:shadow-md hover:shadow-primary/[0.04] transition-all">
                  <div className="flex items-center justify-between gap-3"><div className="h-10 w-10 grid place-items-center rounded-xl bg-secondary text-primary"><FolderOpen size={19}/></div><span className={`rounded-full px-2.5 py-1 text-[10px] font-medium ${status.color}`}>{status.text}</span></div>
                  <button onClick={() => void onEnterProject(project.project_id, project.analysis_id ? "results" : "upload")} disabled={busy} className="mt-4 block w-full text-left rounded-lg focus-visible:outline-2 focus-visible:outline-primary disabled:opacity-50">
                    <h3 className="truncate text-sm font-semibold text-foreground group-hover:text-primary transition-colors" title={project.name}>{project.name}</h3>
                    <p className="mt-2 min-h-10 line-clamp-2 text-xs leading-5 text-muted-foreground">{project.research_goal || project.description || "还未填写研究目标，进入项目后可以和助手一起完善。"}</p>
                  </button>
                  <div className="mt-4 flex items-center justify-between text-[10px] text-muted-foreground"><span>{updatedLabel(project.updated_at || project.created_at)}</span><span>进度 {progress}%</span></div>
                  <div className="mt-2 h-1 rounded-full bg-muted overflow-hidden"><div className="h-full rounded-full bg-primary/65" style={{width:`${progress}%`}} /></div>
                  <div className="mt-4 flex items-center justify-between border-t border-border/50 pt-3">
                    <button aria-label={`删除项目 ${project.name}`} title="删除项目并清空相关数据" onClick={() => void handleDeleteProject(project)} disabled={busy} className="inline-flex items-center gap-1 rounded-lg p-1 text-[11px] text-muted-foreground hover:text-rose-600 hover:bg-rose-50 focus-visible:text-rose-600 disabled:opacity-50">{deletingId === project.project_id ? <Loader2 size={12} className="animate-spin"/> : <Trash2 size={12}/>} 删除</button>
                    <button onClick={() => void onEnterProject(project.project_id, project.analysis_id ? "results" : "upload")} disabled={busy} className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs font-medium text-primary hover:bg-secondary disabled:opacity-50">进入项目 <ArrowRight size={13}/></button>
                  </div>
                </article>;
              })}
            </div>
          )}
        </section>

        <aside className="min-w-0 space-y-4">
          <section aria-labelledby="new-project-title" className="rounded-2xl border border-primary/15 bg-card p-5 shadow-sm shadow-primary/[0.03]">
            <div className="flex items-center gap-2.5"><div className="h-9 w-9 grid place-items-center rounded-xl bg-secondary text-primary"><Plus size={18}/></div><h2 id="new-project-title" className="text-sm font-semibold">新建研究项目</h2></div>
            <p className="mt-3 text-xs leading-5 text-muted-foreground">给研究一个名字，剩下的和助手一起完成。</p>
            <form onSubmit={event => {event.preventDefault(); void handleCreate();}} className="mt-5 space-y-4">
              <div><label htmlFor="methodpal-new-project" className="mb-2 block text-xs font-medium">项目名称 <span className="text-primary">*</span></label><input id="methodpal-new-project" maxLength={200} required value={name} onChange={event => setName(event.target.value)} disabled={busy} className="h-10 w-full rounded-xl border border-border bg-background/50 px-3 text-xs outline-none focus:border-primary/50 focus:ring-2 focus:ring-primary/10 disabled:opacity-50" placeholder="例如：睡眠与心理健康研究"/></div>
              <div><label htmlFor="methodpal-project-goal" className="mb-2 block text-xs font-medium">研究目标 <span className="font-normal text-muted-foreground">（选填）</span></label><textarea id="methodpal-project-goal" value={goal} onChange={event => setGoal(event.target.value)} disabled={busy} className="min-h-24 w-full rounded-xl border border-border bg-background/50 px-3 py-2.5 text-xs leading-5 outline-none focus:border-primary/50 focus:ring-2 focus:ring-primary/10 resize-y disabled:opacity-50" placeholder="你想回答什么问题？例如，睡眠时间是否与心理健康得分有关。"/></div>
              <button type="submit" disabled={!name.trim() || busy} className="w-full h-10 inline-flex items-center justify-center gap-2 rounded-xl bg-primary text-primary-foreground text-xs font-medium hover:opacity-90 disabled:opacity-45 transition-opacity">{creating ? <><Loader2 size={14} className="animate-spin"/> 创建中...</> : <>创建并进入项目 <ArrowRight size={14}/></>}</button>
            </form>
            <p className="mt-3 text-[10px] leading-5 text-muted-foreground">创建后即可导入数据，项目之间的数据与对话互不混合。</p>
          </section>
          {activeProject && <button onClick={() => void onEnterProject(activeProject.project_id, activeProject.analysis_id ? "results" : "upload")} disabled={busy} className="group w-full rounded-2xl border border-border/70 bg-card p-4 text-left hover:border-primary/25 transition-colors disabled:opacity-50"><span className="flex items-center gap-1.5 text-[10px] text-muted-foreground"><Clock size={12}/> 上次打开的项目</span><span className="mt-2 flex items-center justify-between gap-2"><span className="truncate text-xs font-medium">{activeProject.name}</span><ArrowRight size={14} className="shrink-0 text-primary group-hover:translate-x-0.5 transition-transform"/></span></button>}
        </aside>
      </div>
      <p className="border-t border-border/50 pt-5 text-[11px] leading-5 text-muted-foreground">不知道从哪里开始？右侧接待助手可以介绍网站用法，或帮你把想法整理成研究问题。</p>
    </div>
  );
}
