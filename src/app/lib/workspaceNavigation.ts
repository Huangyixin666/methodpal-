import type { Page } from "../App";

export function workspacePage(page: string, projectOpen: boolean): Page {
  if (!projectOpen || page === "home") return "home";
  if (page === "variables" || page === "models") return "upload";
  return ["upload", "results", "charts", "explain", "report"].includes(page) ? page as Page : "upload";
}

export function conversationScope(userId: string, projectId?: string | null) {
  return `${userId}:${projectId || "no_project"}`;
}
