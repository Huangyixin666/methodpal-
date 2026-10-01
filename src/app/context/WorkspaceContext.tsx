import React, {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  clearAgentState,
  createProject,
  deleteProject as apiDeleteProject,
  deleteDataset as apiDeleteDataset,
  deleteDatasetColumns as apiDeleteDatasetColumns,
  deleteDatasetRows as apiDeleteDatasetRows,
  getAnalysis,
  getCurrentUserId,
  getDataset,
  getProject,
  getUserStorage,
  listProjects,
  patchDatasetCells,
  insertDatasetColumn as apiInsertDatasetColumn,
  insertDatasetRows as apiInsertDatasetRows,
  reparseDatasetHeader as apiReparseDatasetHeader,
  saveVariables,
  updateDataset,
  updateProjectWorkspace,
} from "../lib/api";

import type {
  DatasetCellChange,
  DatasetResponse,
  ProjectResponse,
  VariableSelection,
} from "../lib/api";
import {
  datasetColumns,
  normalizeVariableSelection,
  typesOnlySelection,
} from "../lib/variableWorkspace";

type WorkspaceState = {
  userId: string;
  projects: ProjectResponse[];
  activeProject: ProjectResponse | null;
  dataset: DatasetResponse | null;
  selectedVariables: VariableSelection | null;
  selectedModel: string | null;
  modelOptions: Record<string, any>;
  dataReviewStatus: string;
  datasetVersion: number;
  analysisResult: any | null;
  aiExplanation: any | null;
  storageDir: string | null;
  loadingProjects: boolean;
};

type WorkspaceContextValue = WorkspaceState & {
  refreshProjects: () => Promise<void>;
  createNewProject: (
    name: string,
    researchGoal?: string,
  ) => Promise<ProjectResponse>;
  selectProject: (projectId: string) => Promise<void>;
  deleteExistingProject: (projectId: string) => Promise<void>;
  setActiveProject: (project: ProjectResponse | null) => void;
  setDataset: (dataset: DatasetResponse | null) => void;
  attachImportedDataset: (dataset: DatasetResponse, project: ProjectResponse) => Promise<void>;
  replaceCurrentDataset: (dataset: DatasetResponse) => Promise<{ cleanupWarning?: string }>;
  deleteCurrentDataset: () => Promise<void>;
  reparseCurrentDatasetHeader: (options: { header_mode: "row" | "none"; header_row?: number }) => Promise<DatasetResponse | null>;
  insertCurrentDatasetRows: (index?: number | null, count?: number) => Promise<DatasetResponse | null>;
  deleteCurrentDatasetRows: (indices: number[]) => Promise<DatasetResponse | null>;
  insertCurrentDatasetColumn: (options: { name: string; variableType?: string; index?: number | null; defaultValue?: string }) => Promise<DatasetResponse | null>;
  deleteCurrentDatasetColumns: (indices: number[]) => Promise<DatasetResponse | null>;
  updateCell: (rowIndex: number, colIndex: number, value: string) => void;
  addRow: () => void;
  addColumn: (name?: string) => void;
  deleteRow: (rowIndex: number) => void;
  deleteColumn: (colIndex: number) => void;
  saveDatasetEdits: () => Promise<DatasetResponse | null>;
  saveDatasetCellChanges: (changes: DatasetCellChange[]) => Promise<DatasetResponse | null>;
  setSelectedVariables: (selection: VariableSelection | null) => void;
  setSelectedModel: (
    model: string | null,
    options?: Record<string, any>,
    selectionOverride?: VariableSelection | null,
  ) => void;
  setModelOptions: (options: Record<string, any>) => void;
  setDataReviewStatus: (status: string) => void;
  setAnalysisResult: (result: any | null) => void;
  setAiExplanation: (explanation: any | null) => void;
  resetLocalWorkspace: () => void;
};

const ACTIVE_PROJECT_KEY = "methodpal_active_project_by_user_v045";

function readActiveProjectMap(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(ACTIVE_PROJECT_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveActiveProject(userId: string, projectId: string | null) {
  const map = readActiveProjectMap();
  if (projectId) map[userId] = projectId;
  else delete map[userId];
  localStorage.setItem(ACTIVE_PROJECT_KEY, JSON.stringify(map));
}

function clearProjectBrowserCaches(projectId: string) {
  try {
    const exactKeys = [
      `methodpal_project_charts_v070:${projectId}`,
      `methodpal_project_report_draft_v073:${projectId}`,
    ];
    exactKeys.forEach((key) => localStorage.removeItem(key));
    const prefixes = [
      `methodpal_report_job_v075:${projectId}:`,
      `methodpal_report_job_state_v075:${projectId}:`,
      `methodpal_explain_job_v075:${projectId}:`,
      `methodpal_explain_result_v075:${projectId}:`,
      `methodpal_explain_pending_v075:${projectId}:`,
    ];
    const toRemove: string[] = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i);
      if (!key) continue;
      if (prefixes.some((prefix) => key.startsWith(prefix))) toRemove.push(key);
      else if (key.includes(projectId) && key.startsWith("methodpal_"))
        toRemove.push(key);
    }
    toRemove.forEach((key) => localStorage.removeItem(key));
  } catch {
    // Browser cache is best-effort only.
  }
}

function hasRoleConfiguration(selection: VariableSelection | null) {
  return Boolean(
    selection?.dependent_variable ||
    selection?.group_variable ||
    selection?.independent_variables?.length ||
    selection?.mediator_variables?.length ||
    selection?.moderator_variables?.length,
  );
}

function progressOf(
  project: ProjectResponse | null,
  dataset: DatasetResponse | null,
  selectedModel: string | null,
  selectedVariables: VariableSelection | null,
  analysis: any | null,
  explanation: any | null = null,
) {
  if (explanation) return 95;
  if (analysis) return 85;
  if (selectedModel && hasRoleConfiguration(selectedVariables)) return 70;
  if (selectedModel) return 55;
  if (dataset) return 35;
  if (project) return 15;
  return 0;
}

const emptyState: WorkspaceState = {
  userId: getCurrentUserId(),
  projects: [],
  activeProject: null,
  dataset: null,
  selectedVariables: null,
  selectedModel: null,
  modelOptions: {},
  dataReviewStatus: "pending",
  datasetVersion: 0,
  analysisResult: null,
  aiExplanation: null,
  storageDir: null,
  loadingProjects: false,
};

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<WorkspaceState>(emptyState);
  const hydrationSequenceRef = useRef(0);

  function mergeProject(project: ProjectResponse) {
    setState((prev) => ({
      ...prev,
      activeProject:
        prev.activeProject?.project_id === project.project_id
          ? project
          : prev.activeProject,
      projects: prev.projects.map((p) =>
        p.project_id === project.project_id ? project : p,
      ),
    }));
  }

  async function hydrateProject(project: ProjectResponse | null) {
    const sequence = ++hydrationSequenceRef.current;
    const owner = getCurrentUserId();
    if (!project) {
      setState((prev) => ({
        ...prev,
        ...emptyState,
        userId: prev.userId,
        projects: prev.projects,
        storageDir: prev.storageDir,
      }));
      return;
    }

    const fullProject = await getProject(project.project_id);

    let dataset: DatasetResponse | null = null;
    let analysisResult: any | null = null;
    if (fullProject.dataset_id) {
      try {
        dataset = await getDataset(fullProject.dataset_id);
      } catch {
        dataset = null;
      }
    }
    if (fullProject.analysis_id) {
      try {
        analysisResult = await getAnalysis(fullProject.analysis_id);
      } catch {
        analysisResult = null;
      }
    }

    if (sequence !== hydrationSequenceRef.current || owner !== getCurrentUserId()) return null;
    setState((prev) => ({
      ...prev,
      activeProject: fullProject,
      dataset,
      selectedVariables:
        (fullProject.selected_variables as VariableSelection | null) || null,
      selectedModel: fullProject.selected_model || null,
      modelOptions: fullProject.model_options || {},
      dataReviewStatus: fullProject.data_review_status || "pending",
      datasetVersion: fullProject.dataset_version || 0,
      analysisResult,
      aiExplanation: fullProject.ai_explanation || null,
    }));
    return fullProject;
  }

  async function refreshProjects() {
    const sequence = ++hydrationSequenceRef.current;
    setState((prev) => ({ ...prev, loadingProjects: true }));
    try {
      const [projects, storage] = await Promise.all([
        listProjects(),
        getUserStorage().catch(() => null),
      ]);
      const userId = getCurrentUserId();
      if (sequence !== hydrationSequenceRef.current) return;
      const activeId = readActiveProjectMap()[userId];
      const active =
        projects.find((p) => p.project_id === activeId) || projects[0] || null;
      setState((prev) => ({
        ...prev,
        userId,
        projects,
        storageDir: storage?.storage_dir || null,
        loadingProjects: false,
      }));
      await hydrateProject(active);
    } catch {
      if (sequence === hydrationSequenceRef.current) setState((prev) => ({ ...prev, loadingProjects: false }));
    }
  }

  useEffect(() => {
    refreshProjects();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function commitDatasetMutation(
    saved: DatasetResponse,
    options: { resetModel?: boolean; selectionOverride?: VariableSelection | null; addedColumnType?: { name: string; type: string } | null } = {},
  ) {
    const nextVersion = state.datasetVersion + 1;
    let nextSelection: VariableSelection | null = null;
    const resetModel = Boolean(options.resetModel);
    if (!resetModel) {
      const baseSelection = options.selectionOverride !== undefined ? options.selectionOverride : state.selectedVariables;
      if (baseSelection) {
        const withAddedType = options.addedColumnType ? { ...baseSelection, variable_types: { ...(baseSelection.variable_types || {}), [options.addedColumnType.name]: options.addedColumnType.type } } : baseSelection;
        nextSelection = normalizeVariableSelection(datasetColumns(saved.headers, saved.profile?.columns || []), withAddedType);
      } else if (options.addedColumnType) {
        nextSelection = typesOnlySelection({ dependent_variable: "", independent_variables: [], control_variables: [], mediator_variables: [], moderator_variables: [], group_variable: null, variable_types: { [options.addedColumnType.name]: options.addedColumnType.type } });
      }
    }
    const nextModel = resetModel ? null : state.selectedModel;
    const nextOptions = resetModel ? {} : state.modelOptions;
    const progress = progressOf(state.activeProject, saved, nextModel, nextSelection, null, null);
    let nextProject: ProjectResponse | null = state.activeProject ? { ...state.activeProject, selected_variables: nextSelection, selected_model: nextModel, model_options: nextOptions, data_review_status: "review_required", dataset_version: nextVersion, analysis_id: null, ai_explanation: null, progress } : null;
    if (state.activeProject) {
      try {
        nextProject = await updateProjectWorkspace(state.activeProject.project_id, { selected_variables: nextSelection, selected_model: nextModel, model_options: nextOptions, data_review_status: "review_required", dataset_version: nextVersion, analysis_id: null, ai_explanation: null, progress });
      } catch {
        // The dataset mutation has already succeeded. Keep the local workspace
        // consistent and allow the next workspace write to repair persistence.
      }
      clearProjectBrowserCaches(state.activeProject.project_id);
      try { await clearAgentState(state.activeProject.project_id); } catch {}
    }
    setState((prev) => ({ ...prev, activeProject: nextProject, dataset: saved, selectedVariables: nextSelection, selectedModel: nextModel, modelOptions: nextOptions, dataReviewStatus: "review_required", datasetVersion: nextVersion, analysisResult: null, aiExplanation: null, projects: nextProject ? prev.projects.map((project) => project.project_id === nextProject!.project_id ? nextProject! : project) : prev.projects }));
    return saved;
  }

  const value = useMemo<WorkspaceContextValue>(
    () => ({
      ...state,
      refreshProjects,

      async createNewProject(name: string, researchGoal?: string) {
        hydrationSequenceRef.current += 1;
        const project = await createProject({
          name,
          research_goal: researchGoal || "",
          description: researchGoal || "",
        });
        saveActiveProject(state.userId, project.project_id);
        setState((prev) => ({
          ...prev,
          projects: [
            project,
            ...prev.projects.filter((p) => p.project_id !== project.project_id),
          ],
          activeProject: project,
          loadingProjects: false,
          dataset: null,
          selectedVariables: null,
          selectedModel: null,
          modelOptions: {},
          dataReviewStatus: "pending",
          datasetVersion: 0,
          analysisResult: null,
          aiExplanation: null,
        }));
        return project;
      },

      async selectProject(projectId: string) {
        const project = state.projects.find((p) => p.project_id === projectId) || { project_id: projectId } as ProjectResponse;
        const selected = await hydrateProject(project);
        if (!selected) throw new Error("项目切换已取消，请重新选择项目。");
        saveActiveProject(state.userId, projectId);
      },

      async deleteExistingProject(projectId: string) {
        await apiDeleteProject(projectId);
        clearProjectBrowserCaches(projectId);
        const remaining = state.projects.filter(
          (p) => p.project_id !== projectId,
        );
        if (state.activeProject?.project_id === projectId) {
          const next = remaining[0] || null;
          saveActiveProject(state.userId, next?.project_id || null);
          setState((prev) => ({
            ...prev,
            projects: remaining,
            activeProject: null,
          }));
          await hydrateProject(next);
        } else {
          setState((prev) => ({ ...prev, projects: remaining }));
        }
      },

      setActiveProject(project) {
        hydrationSequenceRef.current += 1;
        if (project) saveActiveProject(state.userId, project.project_id);
        setState((prev) => ({
          ...prev,
          activeProject: project,
          dataset: null,
          selectedVariables: null,
          selectedModel: project?.selected_model || null,
          modelOptions: project?.model_options || {},
          dataReviewStatus: project?.data_review_status || "pending",
          datasetVersion: project?.dataset_version || 0,
          analysisResult: null,
          aiExplanation: null,
        }));
      },

      async attachImportedDataset(dataset, project) {
        // Use the explicit project returned by createNewProject, not the state
        // captured before its React render. Persist before reporting success.
        const owner = getCurrentUserId();
        const nextVersion = (project.dataset_version || 0) + 1;
        const nextProject = await updateProjectWorkspace(project.project_id, {
          dataset_id: dataset.dataset_id,
          selected_variables: null,
          selected_model: null,
          model_options: {},
          data_review_status: "review_required",
          dataset_version: nextVersion,
          analysis_id: null,
          ai_explanation: null,
          progress: 35,
        });
        if (owner !== getCurrentUserId()) return;
        setState((prev) => {
          const projects = prev.projects.map((item) =>
            item.project_id === nextProject.project_id ? nextProject : item,
          );
          // A slow import must not replace another project's open workspace.
          if (prev.activeProject?.project_id !== project.project_id) return { ...prev, projects };
          return {
            ...prev, projects, activeProject: nextProject, dataset,
            selectedVariables: null, selectedModel: null, modelOptions: {},
            dataReviewStatus: "review_required", datasetVersion: nextVersion,
            analysisResult: null, aiExplanation: null,
          };
        });
      },

      setDataset(dataset) {
        const nextVersion = dataset ? state.datasetVersion + 1 : 0;
        setState((prev) => {
          const nextProject = prev.activeProject
            ? {
                ...prev.activeProject,
                dataset_id: dataset?.dataset_id || null,
                selected_variables: null,
                selected_model: null,
                model_options: {},
                data_review_status: dataset ? "review_required" : "pending",
                dataset_version: nextVersion,
                analysis_id: null,
                ai_explanation: null,
                progress: dataset ? 35 : 15,
              }
            : null;
          return {
            ...prev,
            activeProject: nextProject,
            dataset,
            selectedVariables: null,
            selectedModel: null,
            modelOptions: {},
            dataReviewStatus: dataset ? "review_required" : "pending",
            datasetVersion: nextVersion,
            analysisResult: null,
            aiExplanation: null,
            projects: nextProject
              ? prev.projects.map((p) =>
                  p.project_id === nextProject.project_id ? nextProject : p,
                )
              : prev.projects,
          };
        });
        if (state.activeProject) {
          updateProjectWorkspace(state.activeProject.project_id, {
            dataset_id: dataset?.dataset_id || null,
            selected_variables: null,
            selected_model: null,
            model_options: {},
            data_review_status: dataset ? "review_required" : "pending",
            dataset_version: nextVersion,
            analysis_id: null,
            ai_explanation: null,
            progress: dataset ? 35 : 15,
          })
            .then(mergeProject)
            .catch(() => {});
        }
      },

      async replaceCurrentDataset(nextDataset) {
        const previousDatasetId = state.dataset?.dataset_id || null;
        const nextVersion = state.datasetVersion + 1;
        let nextProject: ProjectResponse | null = state.activeProject
          ? {
              ...state.activeProject,
              dataset_id: nextDataset.dataset_id,
              selected_variables: null,
              selected_model: null,
              model_options: {},
              data_review_status: "review_required",
              dataset_version: nextVersion,
              analysis_id: null,
              ai_explanation: null,
              progress: 35,
            }
          : null;

        if (state.activeProject) {
          try {
            nextProject = await updateProjectWorkspace(
              state.activeProject.project_id,
              {
                dataset_id: nextDataset.dataset_id,
                selected_variables: null,
                selected_model: null,
                model_options: {},
                data_review_status: "review_required",
                dataset_version: nextVersion,
                analysis_id: null,
                ai_explanation: null,
                progress: 35,
              },
            );
          } catch (error) {
            // Avoid leaving the newly uploaded dataset orphaned when attaching it
            // to the project fails. The original dataset remains untouched.
            try {
              await apiDeleteDataset(nextDataset.dataset_id);
            } catch {
              // Best-effort cleanup only.
            }
            throw error;
          }
        }

        if (state.activeProject) {
          clearProjectBrowserCaches(state.activeProject.project_id);
          try {
            await clearAgentState(state.activeProject.project_id);
          } catch {
            // Agent state is a cache; workspace state remains the source of truth.
          }
        }

        setState((prev) => ({
          ...prev,
          activeProject: nextProject,
          dataset: nextDataset,
          selectedVariables: null,
          selectedModel: null,
          modelOptions: {},
          dataReviewStatus: "review_required",
          datasetVersion: nextVersion,
          analysisResult: null,
          aiExplanation: null,
          projects: nextProject
            ? prev.projects.map((project) =>
                project.project_id === nextProject!.project_id
                  ? nextProject!
                  : project,
              )
            : prev.projects,
        }));

        // Only remove the old dataset after the new one is safely attached.
        let cleanupWarning: string | undefined;
        if (previousDatasetId && previousDatasetId !== nextDataset.dataset_id) {
          try {
            await apiDeleteDataset(previousDatasetId);
          } catch (error) {
            console.warn("Failed to clean up replaced dataset", error);
            cleanupWarning =
              "新数据已替换成功，但旧数据清理失败；可稍后再次删除旧数据存储。";
          }
        }
        return { cleanupWarning };
      },

      async deleteCurrentDataset() {
        if (!state.dataset) return;
        const datasetId = state.dataset.dataset_id;
        await apiDeleteDataset(datasetId);

        let nextProject: ProjectResponse | null = state.activeProject
          ? {
              ...state.activeProject,
              dataset_id: null,
              analysis_id: null,
              selected_variables: null,
              selected_model: null,
              model_options: {},
              data_review_status: "pending",
              dataset_version: state.datasetVersion + 1,
              ai_explanation: null,
              progress: 15,
            }
          : null;
        if (state.activeProject) {
          try {
            nextProject = await getProject(state.activeProject.project_id);
          } catch {
            // The local cleared version mirrors the delete endpoint response.
          }
        }

        if (state.activeProject)
          clearProjectBrowserCaches(state.activeProject.project_id);

        setState((prev) => ({
          ...prev,
          activeProject: nextProject,
          dataset: null,
          selectedVariables: null,
          selectedModel: null,
          modelOptions: {},
          dataReviewStatus: "pending",
          datasetVersion: nextProject?.dataset_version || state.datasetVersion + 1,
          analysisResult: null,
          aiExplanation: null,
          projects: nextProject
            ? prev.projects.map((project) =>
                project.project_id === nextProject!.project_id
                  ? nextProject!
                  : project,
              )
            : prev.projects,
        }));
      },

      async reparseCurrentDatasetHeader(options) {
        if (!state.dataset) return null;
        const saved = await apiReparseDatasetHeader(state.dataset.dataset_id, options);
        return commitDatasetMutation(saved, { resetModel: true });
      },

      async insertCurrentDatasetRows(index, count = 1) {
        if (!state.dataset) return null;
        const saved = await apiInsertDatasetRows(state.dataset.dataset_id, { index: index ?? null, count });
        return commitDatasetMutation(saved);
      },

      async deleteCurrentDatasetRows(indices) {
        if (!state.dataset || !indices.length) return state.dataset;
        const saved = await apiDeleteDatasetRows(state.dataset.dataset_id, indices);
        return commitDatasetMutation(saved);
      },

      async insertCurrentDatasetColumn(options) {
        if (!state.dataset) return null;
        const previousHeaders = new Set(state.dataset.headers);
        const saved = await apiInsertDatasetColumn(state.dataset.dataset_id, { name: options.name, index: options.index ?? null, default_value: options.defaultValue || "" });
        const insertedName = saved.headers.find((header) => !previousHeaders.has(header)) || options.name;
        return commitDatasetMutation(saved, { addedColumnType: { name: insertedName, type: options.variableType || "未知" } });
      },

      async deleteCurrentDatasetColumns(indices) {
        if (!state.dataset || !indices.length) return state.dataset;
        const saved = await apiDeleteDatasetColumns(state.dataset.dataset_id, indices);
        return commitDatasetMutation(saved);
      },

      updateCell(rowIndex, colIndex, cellValue) {
        setState((prev) => {
          if (!prev.dataset) return prev;
          const rows = prev.dataset.rows.map((row) => [...row]);
          while (rows.length <= rowIndex) rows.push([]);
          while (rows[rowIndex].length <= colIndex) rows[rowIndex].push("");
          rows[rowIndex][colIndex] = cellValue;
          const cells = (prev.dataset.cells || []).map((row) =>
            row.map((cell) => ({ ...cell })),
          );
          if (cells[rowIndex]?.[colIndex])
            cells[rowIndex][colIndex] = {
              ...cells[rowIndex][colIndex],
              value: cellValue,
              fixed: true,
            };
          return {
            ...prev,
            dataset: {
              ...prev.dataset,
              rows,
              cells,
              message: "数据已编辑；保存后会重新诊断并使旧分析失效。",
            },
            dataReviewStatus: "edited",
            analysisResult: null,
            aiExplanation: null,
          };
        });
      },

      addRow() {
        setState((prev) =>
          !prev.dataset
            ? prev
            : {
                ...prev,
                dataset: {
                  ...prev.dataset,
                  rows: [
                    ...prev.dataset.rows,
                    prev.dataset.headers.map(() => ""),
                  ],
                },
                dataReviewStatus: "edited",
                analysisResult: null,
                aiExplanation: null,
              },
        );
      },

      addColumn(name = "新变量") {
        setState((prev) => {
          if (!prev.dataset) return prev;
          let finalName = name;
          let idx = 1;
          while (prev.dataset.headers.includes(finalName))
            finalName = `${name}${++idx}`;
          const selectedVariables = prev.selectedVariables
            ? {
                ...prev.selectedVariables,
                variable_types: {
                  ...(prev.selectedVariables.variable_types || {}),
                  [finalName]: "未知",
                },
              }
            : null;
          return {
            ...prev,
            dataset: {
              ...prev.dataset,
              headers: [...prev.dataset.headers, finalName],
              rows: prev.dataset.rows.map((row) => [...row, ""]),
            },
            selectedVariables,
            dataReviewStatus: "edited",
            analysisResult: null,
            aiExplanation: null,
          };
        });
      },

      deleteRow(rowIndex) {
        setState((prev) => {
          if (
            !prev.dataset ||
            rowIndex < 0 ||
            rowIndex >= prev.dataset.rows.length
          )
            return prev;
          return {
            ...prev,
            dataset: {
              ...prev.dataset,
              rows: prev.dataset.rows.filter((_, i) => i !== rowIndex),
              cells: (prev.dataset.cells || []).filter(
                (_, i) => i !== rowIndex,
              ),
            },
            dataReviewStatus: "edited",
            analysisResult: null,
            aiExplanation: null,
          };
        });
      },

      deleteColumn(colIndex) {
        setState((prev) => {
          if (
            !prev.dataset ||
            prev.dataset.headers.length <= 1 ||
            colIndex < 0 ||
            colIndex >= prev.dataset.headers.length
          )
            return prev;
          const deletedName = prev.dataset.headers[colIndex];
          const nextTypes = {
            ...(prev.selectedVariables?.variable_types || {}),
          };
          delete nextTypes[deletedName];
          const selectedVariables = prev.selectedVariables
            ? {
                ...prev.selectedVariables,
                dependent_variable:
                  prev.selectedVariables.dependent_variable === deletedName
                    ? ""
                    : prev.selectedVariables.dependent_variable,
                independent_variables: (
                  prev.selectedVariables.independent_variables || []
                ).filter((v) => v !== deletedName),
                control_variables: (
                  prev.selectedVariables.control_variables || []
                ).filter((v) => v !== deletedName),
                mediator_variables: (
                  prev.selectedVariables.mediator_variables || []
                ).filter((v) => v !== deletedName),
                moderator_variables: (
                  prev.selectedVariables.moderator_variables || []
                ).filter((v) => v !== deletedName),
                group_variable:
                  prev.selectedVariables.group_variable === deletedName
                    ? null
                    : prev.selectedVariables.group_variable,
                variable_types: nextTypes,
              }
            : null;
          return {
            ...prev,
            dataset: {
              ...prev.dataset,
              headers: prev.dataset.headers.filter((_, i) => i !== colIndex),
              rows: prev.dataset.rows.map((row) =>
                row.filter((_, i) => i !== colIndex),
              ),
              cells: (prev.dataset.cells || []).map((row) =>
                row.filter((_, i) => i !== colIndex),
              ),
            },
            selectedVariables,
            dataReviewStatus: "edited",
            analysisResult: null,
            aiExplanation: null,
          };
        });
      },

      async saveDatasetCellChanges(changes) {
        if (!state.dataset || changes.length === 0) return state.dataset;
        const saved = await patchDatasetCells(state.dataset.dataset_id, changes);
        const nextVersion = state.datasetVersion + 1;
        const selectedVariables = state.selectedVariables
          ? normalizeVariableSelection(
              datasetColumns(saved.headers, saved.profile?.columns || []),
              state.selectedVariables,
            )
          : null;
        const progress = progressOf(
          state.activeProject,
          saved,
          state.selectedModel,
          selectedVariables,
          null,
          null,
        );
        setState((prev) => {
          const nextProject = prev.activeProject
            ? {
                ...prev.activeProject,
                selected_variables: selectedVariables,
                data_review_status: "review_required",
                dataset_version: nextVersion,
                analysis_id: null,
                ai_explanation: null,
                progress,
              }
            : null;
          return {
            ...prev,
            activeProject: nextProject,
            dataset: saved,
            selectedVariables,
            dataReviewStatus: "review_required",
            datasetVersion: nextVersion,
            analysisResult: null,
            aiExplanation: null,
            projects: nextProject
              ? prev.projects.map((project) =>
                  project.project_id === nextProject.project_id ? nextProject : project,
                )
              : prev.projects,
          };
        });
        if (state.activeProject) {
          updateProjectWorkspace(state.activeProject.project_id, {
            selected_variables: selectedVariables,
            data_review_status: "review_required",
            dataset_version: nextVersion,
            analysis_id: null,
            ai_explanation: null,
            progress,
          })
            .then(mergeProject)
            .catch(() => {});
        }
        return saved;
      },

      async saveDatasetEdits() {
        if (!state.dataset) return null;
        const saved = await updateDataset(state.dataset.dataset_id, {
          name: state.dataset.name,
          headers: state.dataset.headers,
          rows: state.dataset.rows,
        });
        const nextVersion = state.datasetVersion + 1;
        const selectedVariables = state.selectedVariables
          ? normalizeVariableSelection(
              datasetColumns(saved.headers, saved.profile?.columns || []),
              state.selectedVariables,
            )
          : null;
        const progress = progressOf(
          state.activeProject,
          saved,
          state.selectedModel,
          selectedVariables,
          null,
          null,
        );
        setState((prev) => {
          const nextProject = prev.activeProject
            ? {
                ...prev.activeProject,
                selected_variables: selectedVariables,
                data_review_status: "review_required",
                dataset_version: nextVersion,
                analysis_id: null,
                ai_explanation: null,
                progress,
              }
            : null;
          return {
            ...prev,
            activeProject: nextProject,
            dataset: saved,
            selectedVariables,
            dataReviewStatus: "review_required",
            datasetVersion: nextVersion,
            analysisResult: null,
            aiExplanation: null,
            projects: nextProject
              ? prev.projects.map((project) =>
                  project.project_id === nextProject.project_id
                    ? nextProject
                    : project,
                )
              : prev.projects,
          };
        });
        if (state.activeProject) {
          updateProjectWorkspace(state.activeProject.project_id, {
            selected_variables: selectedVariables,
            data_review_status: "review_required",
            dataset_version: nextVersion,
            analysis_id: null,
            ai_explanation: null,
            progress,
          })
            .then(mergeProject)
            .catch(() => {});
        }
        return saved;
      },

      setSelectedVariables(selection) {
        const progress = progressOf(
          state.activeProject,
          state.dataset,
          state.selectedModel,
          selection,
          null,
          null,
        );
        setState((prev) => {
          const nextProject = prev.activeProject
            ? {
                ...prev.activeProject,
                selected_variables: selection,
                analysis_id: null,
                ai_explanation: null,
                progress,
              }
            : null;
          return {
            ...prev,
            activeProject: nextProject,
            selectedVariables: selection,
            analysisResult: null,
            aiExplanation: null,
            projects: nextProject
              ? prev.projects.map((p) =>
                  p.project_id === nextProject.project_id ? nextProject : p,
                )
              : prev.projects,
          };
        });
        if (state.activeProject) {
          saveVariables({
            project_id: state.activeProject.project_id,
            dataset_id:
              state.dataset?.dataset_id ||
              state.activeProject.dataset_id ||
              null,
            selected_model: state.selectedModel,
            ...(selection || {
              dependent_variable: "",
              independent_variables: [],
              control_variables: [],
              mediator_variables: [],
              moderator_variables: [],
              group_variable: null,
              variable_types: {},
            }),
          })
            .then((response) => {
              if (response?.project) mergeProject(response.project);
            })
            .catch(() => {});
        }
      },

      setSelectedModel(model, options, selectionOverride) {
        const modelChanged = model !== state.selectedModel;
        const nextOptions = options || (modelChanged ? {} : state.modelOptions);
        const nextSelection =
          selectionOverride !== undefined
            ? selectionOverride
            : modelChanged && state.selectedVariables
              ? typesOnlySelection(state.selectedVariables)
              : state.selectedVariables;
        const progress = progressOf(
          state.activeProject,
          state.dataset,
          model,
          nextSelection,
          null,
          null,
        );
        setState((prev) => {
          const nextProject = prev.activeProject
            ? {
                ...prev.activeProject,
                selected_model: model,
                model_options: nextOptions,
                selected_variables: nextSelection,
                analysis_id: null,
                ai_explanation: null,
                progress,
              }
            : null;
          return {
            ...prev,
            activeProject: nextProject,
            selectedVariables: nextSelection,
            selectedModel: model,
            modelOptions: nextOptions,
            analysisResult: null,
            aiExplanation: null,
            projects: nextProject
              ? prev.projects.map((p) =>
                  p.project_id === nextProject.project_id ? nextProject : p,
                )
              : prev.projects,
          };
        });
        if (state.activeProject) {
          updateProjectWorkspace(state.activeProject.project_id, {
            selected_model: model,
            model_options: nextOptions,
            selected_variables: nextSelection,
            analysis_id: null,
            ai_explanation: null,
            progress,
          })
            .then(mergeProject)
            .catch(() => {});
        }
      },

      setModelOptions(options) {
        setState((prev) => ({
          ...prev,
          modelOptions: options,
          activeProject: prev.activeProject
            ? { ...prev.activeProject, model_options: options }
            : null,
        }));
        if (state.activeProject)
          updateProjectWorkspace(state.activeProject.project_id, {
            model_options: options,
          })
            .then(mergeProject)
            .catch(() => {});
      },

      setDataReviewStatus(status) {
        setState((prev) => ({
          ...prev,
          dataReviewStatus: status,
          activeProject: prev.activeProject
            ? { ...prev.activeProject, data_review_status: status }
            : null,
        }));
        if (state.activeProject)
          updateProjectWorkspace(state.activeProject.project_id, {
            data_review_status: status,
          })
            .then(mergeProject)
            .catch(() => {});
      },

      setAnalysisResult(result) {
        if (result && (result.dataset_id !== state.dataset?.dataset_id ||
            (result.source_snapshot?.data_fingerprint && result.source_snapshot.data_fingerprint !== state.dataset?.data_fingerprint))) return;
        const progress = progressOf(
          state.activeProject,
          state.dataset,
          state.selectedModel,
          state.selectedVariables,
          result,
          null,
        );
        setState((prev) => {
          if (prev.activeProject?.project_id !== state.activeProject?.project_id ||
              (result && result.dataset_id !== prev.dataset?.dataset_id)) return prev;
          const nextProject = prev.activeProject
            ? {
                ...prev.activeProject,
                analysis_id: result?.analysis_id || null,
                ai_explanation: null,
                progress,
              }
            : null;
          return {
            ...prev,
            activeProject: nextProject,
            analysisResult: result,
            aiExplanation: null,
            projects: nextProject
              ? prev.projects.map((p) =>
                  p.project_id === nextProject.project_id ? nextProject : p,
                )
              : prev.projects,
          };
        });
        if (state.activeProject)
          updateProjectWorkspace(state.activeProject.project_id, {
            analysis_id: result?.analysis_id || null,
            ai_explanation: null,
            progress,
          })
            .then(mergeProject)
            .catch(() => {});
      },

      setAiExplanation(explanation) {
        const progress = progressOf(
          state.activeProject,
          state.dataset,
          state.selectedModel,
          state.selectedVariables,
          state.analysisResult,
          explanation,
        );
        setState((prev) => {
          const nextProject = prev.activeProject
            ? { ...prev.activeProject, ai_explanation: explanation, progress }
            : null;
          return {
            ...prev,
            activeProject: nextProject,
            aiExplanation: explanation,
            projects: nextProject
              ? prev.projects.map((p) =>
                  p.project_id === nextProject.project_id ? nextProject : p,
                )
              : prev.projects,
          };
        });
        if (state.activeProject)
          updateProjectWorkspace(state.activeProject.project_id, {
            ai_explanation: explanation,
            progress,
          })
            .then(mergeProject)
            .catch(() => {});
      },

      resetLocalWorkspace() {
        saveActiveProject(state.userId, null);
        setState((prev) => ({
          ...emptyState,
          userId: prev.userId,
          projects: prev.projects,
          storageDir: prev.storageDir,
        }));
      },
    }),
    [state],
  );

  return (
    <WorkspaceContext.Provider value={value}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace() {
  const ctx = useContext(WorkspaceContext);
  if (!ctx)
    throw new Error("useWorkspace must be used inside WorkspaceProvider");
  return ctx;
}
