import { AuthGate } from "./components/AuthGate";
import { TaskPanel } from './components/TaskPanel';
import { useRef, useState } from "react";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { AiPanel } from "./components/AiPanel";
import { HomePage } from "./components/HomePage";
import { UploadPage } from "./components/UploadPage";
import { ModelSelectionDialog } from "./components/ModelSelectionPage";
import { ResultsPage } from "./components/ResultsPage";
import { ChartsPage } from "./components/ChartsPage";
import { ExplainPage } from "./components/ExplainPage";
import { ReportPage } from "./components/ReportPage";
import { WorkspaceProvider, useWorkspace } from "./context/WorkspaceContext";
import { conversationScope, workspacePage } from "./lib/workspaceNavigation";

export type Page =
  | "home"
  | "upload"
  | "variables"
  | "results"
  | "charts"
  | "explain"
  | "report";

export function AppShell() {
  const { activeProject, userId, selectProject } = useWorkspace();
  const [currentPage, setCurrentPage] = useState<Page>("home");
  const [enteringProject, setEnteringProject] = useState(false);
  const [navigationError, setNavigationError] = useState("");
  const navigationSequenceRef = useRef(0);
  const page = workspacePage(currentPage, currentPage !== "home" && Boolean(activeProject));
  const projectOpen = page !== "home";
  const [rightWidth, setRightWidth] = useState(320);
  const [modelDialogOpen, setModelDialogOpen] = useState(false);
  const isDragging = useRef(false);
  const dragStartX = useRef(0);
  const dragStartWidth = useRef(0);

  const navigate = (page: string) => {
    if (page === "home") {
      navigationSequenceRef.current += 1;
      setEnteringProject(false);
    }
    if (!projectOpen && page !== "home") return;
    setNavigationError("");
    if (page === "models") {
      setCurrentPage("upload");
      setModelDialogOpen(true);
      return;
    }
    setModelDialogOpen(false);
    setCurrentPage(workspacePage(page, projectOpen));
  };

  const enterProject = async (projectId: string, destination = "upload") => {
    const sequence = ++navigationSequenceRef.current;
    setEnteringProject(true);
    setNavigationError("");
    setModelDialogOpen(false);
    try {
      await selectProject(projectId);
      if (sequence === navigationSequenceRef.current) setCurrentPage(workspacePage(destination, true));
    } catch (error: any) {
      if (sequence === navigationSequenceRef.current) setNavigationError(error.message || "无法进入项目，请重试。");
    } finally {
      if (sequence === navigationSequenceRef.current) setEnteringProject(false);
    }
  };

  const openModelSelector = () => {
    if (!projectOpen) return;
    setCurrentPage("upload");
    setModelDialogOpen(true);
  };

  const handleDividerMouseDown = (e: React.MouseEvent) => {
    isDragging.current = true;
    dragStartX.current = e.clientX;
    dragStartWidth.current = rightWidth;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const onMouseMove = (e: MouseEvent) => {
      if (!isDragging.current) return;
      const delta = dragStartX.current - e.clientX;
      const newWidth = Math.max(
        240,
        Math.min(480, dragStartWidth.current + delta),
      );
      setRightWidth(newWidth);
    };

    const onMouseUp = () => {
      isDragging.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
    };

    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  };

  return (
    <div
      className="flex h-screen bg-background overflow-hidden"
      style={{
        fontFamily:
          "'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', sans-serif",
      }}
    >
      <Sidebar currentPage={page} onNavigate={navigate} onEnterProject={enterProject} enteringProject={enteringProject} />
      <div className="flex flex-1 min-w-0 overflow-hidden">
        <div className="relative flex flex-col flex-1 min-w-0 overflow-hidden">
          <TopBar currentPage={page} onReturnHome={() => navigate("home")} />
          {navigationError && <div role="alert" className="px-6 py-3 bg-red-50 text-red-700 text-sm">{navigationError}</div>}
          {projectOpen && <TaskPanel key={`tasks:${activeProject!.project_id}`} onNavigate={navigate} onEnterProject={enterProject} />}
          {enteringProject && <div role="status" className="px-6 py-2 bg-secondary text-primary text-sm">正在打开项目，请稍候...</div>}
          <main aria-busy={enteringProject} className={`flex-1 overflow-y-auto ${enteringProject ? "pointer-events-none opacity-60" : ""}`}>
            {page === "home" && <HomePage onEnterProject={enterProject} enteringProject={enteringProject} />}
            {page === "upload" && (
              <UploadPage
                key={activeProject!.project_id}
                onNavigate={navigate}
                onOpenModelSelector={openModelSelector}
              />
            )}
            {page === "results" && <ResultsPage key={activeProject!.project_id} onNavigate={navigate} />}
            {page === "charts" && <ChartsPage key={activeProject!.project_id} onNavigate={navigate} />}
            {page === "explain" && <ExplainPage key={activeProject!.project_id} onNavigate={navigate} />}
            {page === "report" && <ReportPage key={activeProject!.project_id} />}
          </main>
          <ModelSelectionDialog
            key={`model-dialog:${projectOpen ? activeProject!.project_id : "lobby"}`}
            open={projectOpen && modelDialogOpen && !enteringProject}
            onClose={() => setModelDialogOpen(false)}
          />
        </div>
        <div
          className="w-1 shrink-0 cursor-col-resize bg-border hover:bg-primary/40 transition-colors active:bg-primary"
          onMouseDown={handleDividerMouseDown}
        />
        <div
          className={`shrink-0 overflow-hidden border-l border-border ${enteringProject ? "pointer-events-none opacity-60" : ""}`}
          style={{ width: rightWidth }}
        >
          <AiPanel
            key={conversationScope(userId, projectOpen ? activeProject?.project_id : null)}
            currentPage={page}
            onNavigate={navigate}
            onEnterProject={enterProject}
            onOpenModelSelector={openModelSelector}
          />
        </div>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <AuthGate><WorkspaceProvider>
      <AppShell />
    </WorkspaceProvider></AuthGate>
  );
}
