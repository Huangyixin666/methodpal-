import { getCurrentUsername, logout } from "../lib/api";
import { useWorkspace } from "../context/WorkspaceContext";

type Props = {
  currentPage: string;
  onNavigate: (page: string) => void;
  onEnterProject: (projectId: string, page?: string) => Promise<void>;
  enteringProject?: boolean;
};

const items = [
  ["home", "⌂", "工作台"],
  ["upload", "▦", "数据工作区"],
  ["results", "▤", "分析结果"],
  ["charts", "▧", "图表分析"],
  ["explain", "✦", "AI 解释"],
  ["report", "▥", "报告导出"],
];

function calcProgress(
  activeProject: any,
  dataset: any,
  selectedModel: any,
  selectedVariables: any,
  analysisResult: any,
) {
  if (analysisResult) return 85;
  const hasRoles = Boolean(
    selectedVariables?.dependent_variable ||
    selectedVariables?.independent_variables?.length ||
    selectedVariables?.group_variable,
  );
  if (selectedModel && hasRoles) return 70;
  if (selectedModel) return 55;
  if (dataset) return 35;
  if (activeProject) return 15;
  return 0;
}

export function Sidebar({ currentPage, onNavigate, onEnterProject, enteringProject }: Props) {
  const projectOpen = currentPage !== "home";
  const {
    projects,
    activeProject,
    dataset,
    selectedModel,
    selectedVariables,
    analysisResult,
    storageDir,
  } = useWorkspace();


  const progress =
    activeProject?.progress ??
    calcProgress(
      activeProject,
      dataset,
      selectedModel,
      selectedVariables,
      analysisResult,
    );

  return (
    <aside className={`${projectOpen ? "w-60" : "w-48 xl:w-56"} shrink-0 border-r border-border/70 bg-card px-4 py-7 flex flex-col`}>
      <div className="flex items-center gap-3 px-2 pb-8">
        <div className="h-9 w-9 rounded-xl bg-primary text-primary-foreground grid place-items-center font-bold shadow-sm">
          M
        </div>
        <div>
          <div className="font-bold">MethodPal</div>
          <div className="text-[11px] text-muted-foreground">
            {projectOpen ? "项目研究空间" : "社科研究 · 方法搭子"}
          </div>
        </div>
      </div>

      {projectOpen && <div className="px-2 pb-4 space-y-2">
        <div>
          <div className="text-sm font-medium truncate">{getCurrentUsername()}</div>
          <button className="text-xs text-primary mt-2" onClick={() => logout().catch((error) => window.alert(error.message))}>退出登录</button>
        </div>
        {projectOpen && <div>
          <label className="text-[11px] text-muted-foreground">当前项目</label>
          <select
            value={activeProject?.project_id || ""}
            onChange={(e) => {
              if (e.target.value) void onEnterProject(e.target.value);
            }}
            disabled={enteringProject}
            className="mt-1 h-8 w-full rounded-lg border border-border bg-background px-2 text-xs outline-none focus:border-primary"
          >
            <option value="">尚未选择项目</option>
            {projects.map((project) => (
              <option key={project.project_id} value={project.project_id}>
                {project.name}
              </option>
            ))}
          </select>
        </div>}
      </div>}

      {!projectOpen && <p className="px-3 mb-3 text-[10px] font-medium tracking-widest text-muted-foreground">个人空间</p>}
      <nav aria-label={projectOpen ? "项目导航" : "工作台导航"} className="grid gap-2">
        {(projectOpen ? items : [items[0]]).map(([id, icon, label]) => {
          const active = currentPage === id;
          const disabled = id !== "home" && (!projectOpen || enteringProject);
          return (
            <button
              key={id}
              disabled={disabled}
              title={disabled ? "请先在工作台创建或进入一个项目" : undefined}
              onClick={() => onNavigate(id)}
              className={[
                "h-11 rounded-xl px-3 text-sm flex items-center gap-3 transition-colors text-left disabled:opacity-35 disabled:cursor-not-allowed",
                active
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              ].join(" ")}
            >
              <span>{icon}</span>
              <span>{id === "home" ? projectOpen ? "返回工作台" : "我的项目" : label}</span>
            </button>
          );
        })}
        {!projectOpen && <button onClick={() => {
          const input = document.getElementById("methodpal-new-project");
          input?.scrollIntoView({block:"center",behavior:"smooth"});
          input?.focus({preventScroll:true});
        }} className="h-11 rounded-xl px-3 text-sm flex items-center gap-3 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"><span aria-hidden="true">＋</span><span>新建项目</span></button>}
      </nav>

      {!projectOpen ? <div className="mt-auto space-y-5">
        <div className="rounded-2xl border border-primary/10 bg-secondary/50 p-4">
          <p className="text-xs font-medium text-foreground mb-2">从一个研究问题开始</p>
          <p className="text-[11px] leading-5 text-muted-foreground">创建项目，整理数据。右侧助手可以帮你了解网站、梳理研究思路。</p>
        </div>
        <div className="flex items-center gap-3 border-t border-border/70 pt-4 px-1">
          <div className="h-9 w-9 shrink-0 rounded-full bg-secondary text-primary grid place-items-center text-sm font-semibold">{getCurrentUsername().slice(0,1).toUpperCase()}</div>
          <div className="min-w-0 flex-1"><p className="text-xs font-medium truncate" title={getCurrentUsername()}>{getCurrentUsername()}</p><button className="mt-1 text-[11px] text-muted-foreground hover:text-primary" onClick={() => logout().catch((error) => window.alert(error.message))}>退出登录</button></div>
        </div>
      </div> : <div className="mt-auto rounded-xl bg-muted/60 p-3 text-xs text-muted-foreground">
        <div className="font-semibold text-foreground mb-1">工作区状态</div>
        <div className="line-clamp-2">
          {activeProject ? activeProject.name : "尚未创建项目"}
        </div>
        <div className="mt-2 text-[11px]">
          {dataset ? `数据集：${dataset.name}` : "还没有上传数据"}
        </div>
        <div className="mt-1 text-[11px]">
          {selectedModel ? `模型：${selectedModel}` : "尚未选择模型"}
        </div>
        <div className="mt-2 text-[10px] line-clamp-2" title={storageDir || ""}>
          保存位置：
          {storageDir ? storageDir.replaceAll("\\", "/") : "后端未连接"}
        </div>
        <div className="mt-3 h-1.5 rounded-full bg-background overflow-hidden">
          <div
            className="h-full rounded-full bg-primary"
            style={{ width: `${progress}%` }}
          />
        </div>
        <div className="mt-1">进度 {progress}%</div>
      </div>}
    </aside>
  );
}
