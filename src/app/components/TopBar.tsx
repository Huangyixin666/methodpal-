import { getCurrentUsername } from "../lib/api";
import {useState} from 'react';
import {AccountSettings} from './AccountSettings';
import { useWorkspace } from "../context/WorkspaceContext";

type Props = {
  currentPage: string;
  onReturnHome?: () => void;
};

const labels: Record<string, string> = {
  home: "我的工作台",
  upload: "数据工作区",
  results: "分析结果",
  charts: "图表分析",
  explain: "AI 解释",
  report: "报告导出",
};

export function TopBar({ currentPage, onReturnHome }: Props) {
  const [accountOpen,setAccountOpen] = useState(false);
  const { userId, activeProject, dataset } = useWorkspace();

  return (
    <header className="h-16 shrink-0 border-b border-border/60 bg-card/90 px-6 lg:px-8 flex items-center justify-between">
      <div className="flex items-center gap-4 min-w-0">
        {currentPage !== "home" && <button onClick={onReturnHome} className="shrink-0 rounded-lg border border-border px-3 py-2 text-xs hover:bg-muted">← 返回工作台</button>}
        <div className="min-w-0">
        <h2 className="text-base font-semibold m-0">
          {labels[currentPage] || currentPage}
        </h2>
        <div className="text-xs text-muted-foreground mt-0.5">
          {getCurrentUsername()} / {currentPage === "home" ? "我的项目" : activeProject?.name || "请先创建项目"}
          {currentPage !== "home" && dataset ? ` / ${dataset.name}` : ""}
        </div>
        </div>
      </div>
      <button className="rounded-xl border border-border bg-card px-3.5 py-2 text-xs text-muted-foreground hover:border-primary/30 hover:text-primary transition-colors" onClick={()=>setAccountOpen(true)}>账户与安全</button>
      {accountOpen && <AccountSettings onClose={()=>setAccountOpen(false)}/>}
    </header>
  );
}
