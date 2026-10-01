import { useEffect, useMemo, useRef, useState } from "react";
import { VirtualSpreadsheetGrid } from "./VirtualSpreadsheetGrid";
import type { VirtualSpreadsheetGridHandle } from "./VirtualSpreadsheetGrid";
import {
  Upload,
  Table2,
  Plus,
  Trash2,
  Download,
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  Check,
  Info,
  Clipboard,
  FlaskConical,
  Lock,
  PlayCircle,
  SlidersHorizontal,
  Undo2,
  Redo2,
} from "lucide-react";
import {
  getSampleDataset,
  pasteDataset,
  recommendMethods,
  runAnalysis,
  uploadDataset,
} from "../lib/api";
import type {
  MethodItem,
  MethodRoleRequirement,
  ProjectResponse,
  VariableSelection,
} from "../lib/api";
import { useWorkspace } from "../context/WorkspaceContext";
import {
  assignColumnRole,
  datasetColumns,
  LIST_ROLE_FIELDS,
  normalizeVariableSelection,
  requirementAllowsType,
  roleForColumn,
  roleRequirements,
  roleValues,
  VARIABLE_TYPE_OPTIONS,
} from "../lib/variableWorkspace";

type Props = {
  onNavigate?: (page: string) => void;
  onOpenModelSelector?: () => void;
};

const SAMPLE_HEADERS = [
  "编号",
  "年龄",
  "性别",
  "家庭支持",
  "学业压力",
  "睡眠时间",
  "心理健康得分",
];
const SAMPLE_ROWS: string[][] = [
  ["001", "20", "女", "4.2", "3.8", "6.5", "72"],
  ["002", "22", "男", "3.5", "4.1", "5.8", "65"],
  ["003", "19", "女", "4.8", "2.9", "7.2", "80"],
  ["004", "21", "男", "2.1", "4.7", "", "58"],
  ["009", "19", "女", "4.1", "3.2", "42.0", "73"],
  ["011", "20", "女", "4.0", "3.6", "6.7", "150"],
];

const EMPTY_GRID = (rows = 8, cols = 7): string[][] =>
  Array.from({ length: rows }, () => Array(cols).fill(""));
const ZOOMS = [80, 100, 125];
const zoomCell = (z: number) => ({
  fontSize: `${z === 80 ? 11 : z === 100 ? 12 : 13}px`,
  padding: z === 80 ? "2px 6px" : z === 100 ? "4px 8px" : "6px 10px",
});

export function UploadPage({ onNavigate, onOpenModelSelector }: Props) {
  const {
    activeProject,
    dataset,
    selectedVariables,
    selectedModel,
    modelOptions,
    dataReviewStatus,
    attachImportedDataset,
    replaceCurrentDataset,
    deleteCurrentDataset,
    reparseCurrentDatasetHeader,
    insertCurrentDatasetRows,
    deleteCurrentDatasetRows,
    insertCurrentDatasetColumn,
    deleteCurrentDatasetColumns,
    saveDatasetCellChanges,
    setSelectedVariables,
    setModelOptions,
    setAnalysisResult,
    setDataReviewStatus,
    createNewProject,
  } = useWorkspace();

  const [tab, setTab] = useState<"file" | "grid">("file");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [projectName, setProjectName] = useState("");
  const [projectNameError, setProjectNameError] = useState("");
  const [gridHeaders, setGridHeaders] = useState<string[]>(SAMPLE_HEADERS);
  const [gridData, setGridData] = useState<string[][]>(EMPTY_GRID());
  const [rawPastedMatrix, setRawPastedMatrix] = useState<string[][]>([]);
  const [zoom, setZoom] = useState(100);
  const [method, setMethod] = useState<MethodItem | null>(null);
  const [checkingMethod, setCheckingMethod] = useState(false);
  const [running, setRunning] = useState(false);
  const [fileHeaderMode, setFileHeaderMode] = useState<"row" | "none">("row");
  const [fileHeaderRow, setFileHeaderRow] = useState(1);
  const [pasteHeaderMode, setPasteHeaderMode] = useState<"row" | "none">("row");
  const [pasteHeaderRow, setPasteHeaderRow] = useState(1);
  const [pendingCellCount, setPendingCellCount] = useState(0);
  const [loadedRowCount, setLoadedRowCount] = useState(0);
  const [canUndo, setCanUndo] = useState(false);
  const [canRedo, setCanRedo] = useState(false);
  const [replaceDialogOpen, setReplaceDialogOpen] = useState(false);
  const [headerDialogOpen, setHeaderDialogOpen] = useState(false);
  const [headerDraftMode, setHeaderDraftMode] = useState<"row" | "none">("row");
  const [headerDraftRow, setHeaderDraftRow] = useState(1);
  const [columnDialogOpen, setColumnDialogOpen] = useState(false);
  const [newColumnName, setNewColumnName] = useState("新变量");
  const [newColumnType, setNewColumnType] = useState("未知");
  const [newColumnDefault, setNewColumnDefault] = useState("");
  const [activeCell, setActiveCell] = useState<{ row: number; col: number } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const projectNameInputRef = useRef<HTMLInputElement>(null);
  const importInFlightRef = useRef(false);
  const replaceFileInputRef = useRef<HTMLInputElement>(null);
  const pendingCountRef = useRef(0);
  const spreadsheetRef = useRef<VirtualSpreadsheetGridHandle>(null);

  const cs = zoomCell(zoom);
  const columns = useMemo(
    () =>
      dataset
        ? datasetColumns(dataset.headers, dataset.profile?.columns || [])
        : [],
    [dataset],
  );
  const selection = useMemo(
    () => normalizeVariableSelection(columns, selectedVariables),
    [columns, selectedVariables],
  );
  const requirements = useMemo(
    () =>
      roleRequirements(
        method?.requirement?.required_roles || [],
        method?.requirement?.optional_roles || [],
      ),
    [method],
  );
  const requiredRoleIds = useMemo(
    () =>
      new Set(
        (method?.requirement?.required_roles || []).map((item) => item.role),
      ),
    [method],
  );
  const lastProjectIdRef = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    const projectId = activeProject?.project_id || null;

    if (lastProjectIdRef.current === undefined) {
      lastProjectIdRef.current = projectId;
      return;
    }

    if (lastProjectIdRef.current !== projectId) {
      if (!importInFlightRef.current) setMessage("");
      setProjectName("");
      setProjectNameError("");
    }

    lastProjectIdRef.current = projectId;
  }, [activeProject?.project_id]);

  useEffect(() => {
    if (rawPastedMatrix.length)
      applyPastedMatrix(rawPastedMatrix, pasteHeaderMode, pasteHeaderRow);
    // Reinterpreting the original clipboard matrix is intentional when header settings change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pasteHeaderMode, pasteHeaderRow]);

  // `edited` used to be persisted as a workspace status even though unsaved cell
  // edits live only inside the spreadsheet component. After undo/refresh/grid
  // remount this could leave a stale `edited` flag with zero pending changes and
  // incorrectly block analysis. Pending edits are now tracked exclusively by
  // `pendingCellCount`; normalize legacy stale projects on load.
  useEffect(() => {
    if (dataset && dataReviewStatus === "edited" && pendingCellCount === 0) {
      setDataReviewStatus("review_required");
    }
  }, [dataset?.dataset_id, dataReviewStatus, pendingCellCount, setDataReviewStatus]);

  useEffect(() => {
    if (!dataset || !selectedModel) {
      setMethod(null);
      setCheckingMethod(false);
      return;
    }
    let cancelled = false;
    setCheckingMethod(true);
    recommendMethods({
      dataset_id: dataset.dataset_id,
      research_goal: activeProject?.research_goal || "",
      selected_model: selectedModel,
      ...selection,
    })
      .then((response) => {
        if (cancelled) return;
        setMethod(
          (response.recommended || []).find(
            (item) => item.method_id === selectedModel,
          ) || null,
        );
      })
      .catch((error: any) => {
        if (!cancelled) setMessage(error.message || "模型要求校验失败");
      })
      .finally(() => {
        if (!cancelled) setCheckingMethod(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    dataset?.dataset_id,
    activeProject?.research_goal,
    selectedModel,
    selection,
  ]);

  async function ensureProject(): Promise<ProjectResponse | null> {
    if (activeProject) return activeProject;
    if (!projectName.trim()) {
      const warning = "请先填写项目名称，再点击导入；已输入的表格数据会保留。";
      setMessage(warning);
      setProjectNameError(warning);
      projectNameInputRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
      projectNameInputRef.current?.focus({ preventScroll: true });
      return null;
    }
    return createNewProject(projectName.trim());
  }

  async function handleFile(file?: File) {
    if (!file || importInFlightRef.current) return;
    importInFlightRef.current = true;
    setBusy(true);
    try {
      const project = await ensureProject();
      if (!project) return;
      setMessage(`正在上传 ${file.name} 并请求后端检查...`);
      const data = await uploadDataset(file, {
        header_mode: fileHeaderMode,
        header_row: fileHeaderRow,
      });
      await attachImportedDataset(data, project);
      setMessage(data.message || "数据已上传。可在下方查看和编辑数据。");
    } catch (e: any) {
      setMessage(e.message || "上传失败");
    } finally {
      importInFlightRef.current = false;
      setBusy(false);
    }
  }

  async function handleReplacementFile(file?: File) {
    if (!file || !dataset) return;
    setBusy(true);
    setMessage(`正在上传 ${file.name}。新数据成功挂载后才会删除旧数据...`);
    try {
      const data = await uploadDataset(file, {
        header_mode: fileHeaderMode,
        header_row: fileHeaderRow,
      });
      const replaceResult = await replaceCurrentDataset(data);
      setReplaceDialogOpen(false);
      setPendingCellCount(0);
      pendingCountRef.current = 0;
      setCanUndo(false);
      setCanRedo(false);
      setMessage(
        replaceResult.cleanupWarning ||
          data.message ||
          "数据已安全替换。旧数据、旧模型配置和旧分析结果已清理。",
      );
    } catch (e: any) {
      setMessage(e.message || "替换数据失败；原数据仍保持不变。");
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteDataset() {
    if (!dataset) return;
    const pendingNote = pendingCellCount > 0
      ? `\n当前还有 ${pendingCellCount} 处未保存修改，这些修改也会被丢弃。`
      : "";
    const confirmed = window.confirm(
      `确定删除当前数据“${dataset.name}”吗？${pendingNote}\n\n与该数据关联的模型配置、变量角色和分析结果也会被清除。此操作不可撤销。`,
    );
    if (!confirmed) return;
    setBusy(true);
    setMessage("正在删除当前数据及关联分析状态...");
    try {
      await deleteCurrentDataset();
      setPendingCellCount(0);
      pendingCountRef.current = 0;
      setCanUndo(false);
      setCanRedo(false);
      setMessage("当前数据已删除，项目已回到待上传状态。");
    } catch (e: any) {
      setMessage(e.message || "删除数据失败");
    } finally {
      setBusy(false);
    }
  }

  function handlePendingChange(count: number) {
    pendingCountRef.current = count;
    setPendingCellCount(count);
  }

  async function loadSample() {
    if (importInFlightRef.current) return;
    importInFlightRef.current = true;
    setBusy(true);
    try {
      const project = await ensureProject();
      if (!project) return;
      setMessage("正在请求后端示例数据...");
      const data = await getSampleDataset();
      await attachImportedDataset(data, project);
      setMessage(data.message || "示例数据已载入。可在下方查看和编辑数据。");
    } catch (e: any) {
      setMessage(e.message || "载入示例失败");
    } finally {
      importInFlightRef.current = false;
      setBusy(false);
    }
  }

  function applyPastedMatrix(
    matrix: string[][],
    mode: "row" | "none" = pasteHeaderMode,
    headerRow: number = pasteHeaderRow,
  ) {
    if (!matrix.length) return;
    const width = Math.max(...matrix.map((row) => row.length), 1);
    const normalized = matrix.map((row) =>
      Array.from({ length: width }, (_, index) => String(row[index] || "").trim()),
    );
    if (mode === "none") {
      setGridHeaders(Array.from({ length: width }, (_, index) => `变量${index + 1}`));
      setGridData(normalized);
      return;
    }
    const headerIndex = Math.min(Math.max(0, headerRow - 1), normalized.length - 1);
    setGridHeaders(
      normalized[headerIndex].map((value, index) => value || `变量${index + 1}`),
    );
    setGridData(normalized.slice(headerIndex + 1));
  }

  async function importGrid() {
    if (importInFlightRef.current) return;
    importInFlightRef.current = true;
    setBusy(true);
    try {
      const nonEmptyRows = gridData.filter((row) =>
        row.some((v) => String(v || "").trim() !== ""),
      );
      if (!nonEmptyRows.length) {
        setMessage("表格还没有数据，请至少填写一行数据后再导入。");
        return;
      }
      const project = await ensureProject();
      if (!project) return;
      setMessage("正在请求后端检查表格数据...");
      const data = await pasteDataset({
        name: "pasted_table",
        headers: gridHeaders,
        rows: nonEmptyRows,
      });
      await attachImportedDataset(data, project);
      setMessage(data.message || "表格数据已导入。可在下方查看和编辑数据。");
    } catch (e: any) {
      setMessage(e.message || "导入失败");
    } finally {
      importInFlightRef.current = false;
      setBusy(false);
    }
  }

  async function saveEdits() {
    setBusy(true);
    setMessage("正在批量保存单元格并基于全量数据重新诊断...");
    try {
      const saved = await spreadsheetRef.current?.savePending();
      if (saved) {
        setPendingCellCount(0);
        setMessage(
          saved.message ||
            "已保存编辑并重新诊断。字段类型和仍存在的变量角色会保留。",
        );
      }
    } catch (e: any) {
      setMessage(e.message || "保存失败");
    } finally {
      setBusy(false);
    }
  }

  function ensureStructureReady() {
    if (pendingCellCount > 0) {
      setMessage("请先保存当前单元格修改，再新增/删除行列或重新设置表头。结构变化会重新建立虚拟表格缓存和编辑历史。");
      return false;
    }
    return true;
  }

  function resetGridEditState() {
    setPendingCellCount(0);
    pendingCountRef.current = 0;
    setCanUndo(false);
    setCanRedo(false);
    setActiveCell(null);
  }

  function openHeaderSettings() {
    if (!dataset?.can_reparse_header) {
      setMessage("当前数据没有保留可重新解析的原始上传文件。该功能仅适用于通过 CSV/Excel 文件上传的数据。");
      return;
    }
    setHeaderDraftMode(dataset.header_mode === "none" ? "none" : "row");
    setHeaderDraftRow(Math.max(1, Number(dataset.header_row || 1)));
    setHeaderDialogOpen(true);
  }

  async function applyHeaderSettings() {
    if (!dataset || !ensureStructureReady()) return;
    const confirmed = window.confirm("重新设置表头会从最初上传的原始文件重新解析数据。已经保存过的单元格修改、当前模型、变量角色和分析结果都会被重置。确定继续吗？");
    if (!confirmed) return;
    setBusy(true);
    setMessage("正在从原始文件重新解析表头...");
    try {
      const saved = await reparseCurrentDatasetHeader({ header_mode: headerDraftMode, header_row: headerDraftRow });
      if (saved) { resetGridEditState(); setHeaderDialogOpen(false); setMessage(saved.message || "表头设置已更新，模型和变量配置已重置。"); }
    } catch (e: any) { setMessage(e.message || "重新解析表头失败"); }
    finally { setBusy(false); }
  }

  async function addDatasetRow() {
    if (!dataset || !ensureStructureReady()) return;
    const insertAt = activeCell ? Math.min(activeCell.row + 1, dataset.full_row_count || dataset.profile.row_count) : dataset.full_row_count || dataset.profile.row_count;
    setBusy(true);
    try {
      const saved = await insertCurrentDatasetRows(insertAt, 1);
      if (saved) { resetGridEditState(); setMessage(activeCell ? `已在原第 ${activeCell.row + 1} 行下方新增一行。` : "已在数据末尾新增一行。"); }
    } catch (e: any) { setMessage(e.message || "新增行失败"); }
    finally { setBusy(false); }
  }

  async function removeDatasetRow() {
    if (!dataset || !ensureStructureReady()) return;
    let rowIndex = activeCell?.row ?? -1;
    if (rowIndex < 0) {
      const input = window.prompt(`请输入要删除的行号（1-${dataset.full_row_count || dataset.profile.row_count}）`);
      if (input == null) return;
      rowIndex = Number(input) - 1;
      if (!Number.isInteger(rowIndex) || rowIndex < 0 || rowIndex >= (dataset.full_row_count || dataset.profile.row_count)) {
        setMessage("请输入有效的行号。");
        return;
      }
    }
    const rowNumber = rowIndex + 1;
    if (!window.confirm(`确定删除第 ${rowNumber} 行吗？删除后旧分析结果会失效。`)) return;
    setBusy(true);
    try {
      const saved = await deleteCurrentDatasetRows([rowIndex]);
      if (saved) { resetGridEditState(); setMessage(`已删除第 ${rowNumber} 行。`); }
    } catch (e: any) { setMessage(e.message || "删除行失败"); }
    finally { setBusy(false); }
  }

  function openAddColumnDialog() {
    if (!dataset || !ensureStructureReady()) return;
    setNewColumnName(`新变量${dataset.headers.length + 1}`);
    setNewColumnType("未知");
    setNewColumnDefault("");
    setColumnDialogOpen(true);
  }

  async function addDatasetColumn() {
    if (!dataset || !newColumnName.trim() || !ensureStructureReady()) return;
    const insertAt = activeCell ? activeCell.col + 1 : dataset.headers.length;
    setBusy(true);
    try {
      const saved = await insertCurrentDatasetColumn({ name: newColumnName.trim(), variableType: newColumnType, index: insertAt, defaultValue: newColumnDefault });
      if (saved) {
        const insertedName = saved.headers.find((header) => !dataset.headers.includes(header)) || newColumnName.trim();
        resetGridEditState();
        setColumnDialogOpen(false);
        setMessage(`已新增变量“${insertedName}”，可直接在新列中粘贴数据。`);
      }
    } catch (e: any) { setMessage(e.message || "新增列失败"); }
    finally { setBusy(false); }
  }

  async function removeDatasetColumn() {
    if (!dataset || !ensureStructureReady()) return;
    if (dataset.headers.length <= 1) { setMessage("至少需要保留 1 列。"); return; }
    let colIndex = activeCell?.col ?? -1;
    if (colIndex < 0) {
      const input = window.prompt(`请输入要删除的列号（1-${dataset.headers.length}）`);
      if (input == null) return;
      colIndex = Number(input) - 1;
      if (!Number.isInteger(colIndex) || colIndex < 0 || colIndex >= dataset.headers.length) {
        setMessage("请输入有效的列号。");
        return;
      }
    }
    const columnName = dataset.headers[colIndex];
    const currentRole = roleForColumn(selection, requirements, columnName);
    const roleWarning = currentRole ? "\n该列当前承担变量角色，删除后该角色会自动取消。" : "";
    if (!window.confirm(`确定删除变量“${columnName}”吗？${roleWarning}\n旧分析结果会失效。`)) return;
    setBusy(true);
    try {
      const saved = await deleteCurrentDatasetColumns([colIndex]);
      if (saved) { resetGridEditState(); setMessage(`已删除变量“${columnName}”。`); }
    } catch (e: any) { setMessage(e.message || "删除列失败"); }
    finally { setBusy(false); }
  }

  function exportCSV() {
    if (!dataset) return;
    if (dataset.is_preview) {
      setMessage(
        `当前前端只持有前 ${dataset.preview_row_count || dataset.rows.length} 行预览。为避免误导，暂不从预览导出全量 CSV；请使用原始文件或后续全量导出接口。`,
      );
      return;
    }
    const lines = [
      dataset.headers.join(","),
      ...dataset.rows.map((row) =>
        row.map((v) => `"${String(v || "").replace(/"/g, '""')}"`).join(","),
      ),
    ];
    const blob = new Blob([lines.join("\n")], {
      type: "text/csv;charset=utf-8;",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${dataset.name || "methodpal_data"}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function clearGrid() {
    setRawPastedMatrix([]);
    setGridData(EMPTY_GRID(8, gridHeaders.length));
  }

  function loadGridSample() {
    setRawPastedMatrix([]);
    setGridHeaders(SAMPLE_HEADERS);
    setGridData(SAMPLE_ROWS.map((r) => [...r]));
  }

  function deleteGridRow(rowIndex: number) {
    setGridData((prev) => prev.filter((_, i) => i !== rowIndex));
  }

  function deleteGridColumn(colIndex: number) {
    if (gridHeaders.length <= 1) {
      setMessage("至少需要保留 1 列。");
      return;
    }
    setGridHeaders((prev) => prev.filter((_, i) => i !== colIndex));
    setGridData((prev) =>
      prev.map((row) => row.filter((_, i) => i !== colIndex)),
    );
  }

  function deleteLastGridRow() {
    setGridData((prev) => prev.slice(0, Math.max(0, prev.length - 1)));
  }

  function deleteLastGridColumn() {
    if (gridHeaders.length <= 1) {
      setMessage("至少需要保留 1 列。");
      return;
    }
    deleteGridColumn(gridHeaders.length - 1);
  }

  function commitSelection(next: VariableSelection) {
    setSelectedVariables(normalizeVariableSelection(columns, next));
  }

  function updateVariableType(columnName: string, nextType: string) {
    let next = normalizeVariableSelection(columns, {
      ...selection,
      variable_types: {
        ...(selection.variable_types || {}),
        [columnName]: nextType,
      },
    });
    const currentRole = roleForColumn(next, requirements, columnName);
    const currentRequirement = requirements.find(
      (item) => item.role === currentRole,
    );
    if (
      currentRequirement &&
      !requirementAllowsType(currentRequirement, nextType)
    ) {
      next = assignColumnRole(next, "", columnName);
      setMessage(
        `“${columnName}”已改为${nextType}；由于不符合${currentRequirement.label}的类型要求，原变量角色已取消。`,
      );
    } else {
      setMessage(`已把“${columnName}”的字段类型设为${nextType}。`);
    }
    commitSelection(next);
  }

  function updateColumnRole(columnName: string, role: string) {
    if (!selectedModel || !method) {
      setMessage("请先选择分析模型，之后表头中的变量角色才会解锁。");
      return;
    }
    if (role) {
      const requirement = requirements.find((item) => item.role === role);
      const currentType = String(
        selection.variable_types?.[columnName] || "未知",
      );
      if (!requirement || !requirementAllowsType(requirement, currentType)) {
        setMessage(
          `“${columnName}”当前类型为${currentType}，不符合该变量角色的要求。`,
        );
        return;
      }
      const currentValues = roleValues(selection, role);
      if (
        LIST_ROLE_FIELDS.has(role) &&
        requirement.max_count != null &&
        currentValues.length >= requirement.max_count &&
        !currentValues.includes(columnName)
      ) {
        setMessage(
          `${requirement.label}最多选择 ${requirement.max_count} 个变量。`,
        );
        return;
      }
    }
    commitSelection(assignColumnRole(selection, role, columnName));
    setMessage(
      role
        ? `已在表头中更新“${columnName}”的变量角色。`
        : `已取消“${columnName}”的变量角色。`,
    );
  }

  function cleanedModelOptions() {
    const out: Record<string, any> = { ...(modelOptions || {}) };
    [
      "test_size",
      "learning_rate",
      "subsample",
      "colsample_bytree",
      "alpha",
    ].forEach((key) => {
      if (out[key] !== undefined && out[key] !== "")
        out[key] = Number(out[key]);
    });
    [
      "cv_folds",
      "random_state",
      "polynomial_degree",
      "n_estimators",
      "max_depth",
      "min_samples_leaf",
      "max_iter",
    ].forEach((key) => {
      if (out[key] !== undefined && out[key] !== "")
        out[key] = Number(out[key]);
    });
    return out;
  }

  async function handleRunAnalysis() {
    if (!dataset || !selectedModel || !method) return;
    if (pendingCellCount > 0) {
      setMessage(
        "数据单元格还有未保存的修改。请先保存编辑并重新诊断，再运行分析。",
      );
      return;
    }
    if (!method.enabled) {
      setMessage(
        method.validation_messages?.join("；") ||
          method.reason ||
          "请先在表头中补齐模型需要的变量角色。",
      );
      return;
    }
    setRunning(true);
    setMessage("正在使用当前表头中的字段类型和变量角色执行分析...");
    try {
      const finalOptions = cleanedModelOptions();
      setModelOptions(finalOptions);
      const result = await runAnalysis({
        project_id: activeProject?.project_id,
        dataset_id: dataset.dataset_id,
        model: selectedModel,
        dependent_variable: selection.dependent_variable || null,
        independent_variables: selection.independent_variables || [],
        control_variables: selection.control_variables || [],
        mediator_variables: selection.mediator_variables || [],
        moderator_variables: selection.moderator_variables || [],
        group_variable: selection.group_variable || null,
        variable_types: selection.variable_types || {},
        options: finalOptions,
      });
      setAnalysisResult(result);
      onNavigate?.("results");
    } catch (error: any) {
      setMessage(error.message || "分析失败");
    } finally {
      setRunning(false);
    }
  }



  return (
    <div className="p-6 space-y-5 max-w-6xl">
      {message && (
        <div role="status" aria-live="polite" className="rounded-xl border border-border bg-secondary/40 px-4 py-2 text-xs text-muted-foreground">
          {busy ? "⏳ " : "ℹ "}
          {message}
        </div>
      )}

      {!activeProject && (
        <div className="bg-card rounded-2xl border border-border p-4 flex flex-wrap items-center gap-3">
          <span className="text-sm text-muted-foreground">
            还没有当前项目，请先输入项目名：
          </span>
          <input
            ref={projectNameInputRef}
            aria-label="项目名称"
            aria-invalid={Boolean(projectNameError)}
            aria-describedby={projectNameError ? "import-project-name-error" : undefined}
            value={projectName}
            onChange={(e) => { setProjectName(e.target.value); setProjectNameError(""); }}
            disabled={busy}
            className={`h-9 rounded-lg border bg-background px-3 text-sm w-72 ${projectNameError ? "border-red-500" : "border-border"}`}
            placeholder="例如：大学生心理健康研究"
          />
          <p className="basis-full text-xs text-muted-foreground">填写名称后，导入会自动创建项目并保存数据，无需先返回工作台。</p>
          {projectNameError && <p id="import-project-name-error" role="alert" className="basis-full text-xs text-red-600">{projectNameError}</p>}
        </div>
      )}


      <input
        ref={fileInputRef}
        type="file"
        accept=".csv,.xlsx,.xls"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) handleFile(file);
          e.currentTarget.value = "";
        }}
      />
      <input
        ref={replaceFileInputRef}
        type="file"
        accept=".csv,.xlsx,.xls"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void handleReplacementFile(file);
          e.currentTarget.value = "";
        }}
      />

      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex gap-1 p-1 bg-muted rounded-xl w-fit">
          {(
            [
              ["file", "上传文件", Upload],
              ["grid", "粘贴表格", Table2],
            ] as const
          ).map(([id, label, Icon]) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm transition-all ${tab === id ? "bg-card text-foreground shadow-sm font-medium" : "text-muted-foreground hover:text-foreground"}`}
            >
              <Icon size={14} />
              {label}
            </button>
          ))}
        </div>
        {dataset && (
          <div className="flex items-center gap-2 flex-wrap">
            <button
              onClick={() => {
                setDataReviewStatus("reviewed");
                if (onOpenModelSelector) onOpenModelSelector();
                else onNavigate?.("models");
              }}
              className="flex items-center gap-1.5 text-sm border border-border bg-card px-4 py-2 rounded-xl hover:bg-muted"
            >
              <SlidersHorizontal size={14} />
              {selectedModel ? "模型与参数" : "选择分析模型"}
            </button>
            <button
              onClick={handleRunAnalysis}
              disabled={
                !method?.enabled ||
                checkingMethod ||
                running ||
                pendingCellCount > 0
              }
              title={
                pendingCellCount > 0
                  ? "请先保存数据编辑"
                  : method?.validation_messages?.join("；") ||
                    method?.reason ||
                    "选择模型并配置变量后运行"
              }
              className="flex items-center gap-1.5 text-sm bg-primary text-primary-foreground px-4 py-2 rounded-xl hover:opacity-90 disabled:opacity-45 disabled:cursor-not-allowed"
            >
              <PlayCircle size={14} />
              {running
                ? "分析中..."
                : method?.enabled
                  ? "开始分析"
                  : selectedModel
                    ? "请先配置变量"
                    : "选择模型后分析"}
            </button>
          </div>
        )}
      </div>

      {tab === "file" && !dataset && (
        <div className="bg-card rounded-2xl border border-border p-6">
          <h3 className="text-foreground mb-1">上传数据文件</h3>
          <p className="text-xs text-muted-foreground mb-4">
            支持 CSV 和 Excel
            格式。上传后会保存到您的个人工作区。
          </p>
          <div className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-border bg-muted/30 px-4 py-3">
            <span className="text-xs font-medium text-foreground">表头设置</span>
            <select
              value={fileHeaderMode}
              onChange={(event) => setFileHeaderMode(event.target.value as "row" | "none")}
              className="h-8 rounded-lg border border-border bg-card px-2 text-xs"
            >
              <option value="row">指定一行作为表头</option>
              <option value="none">没有表头，自动生成变量名</option>
            </select>
            {fileHeaderMode === "row" && (
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                第
                <input
                  type="number"
                  min={1}
                  value={fileHeaderRow}
                  onChange={(event) => setFileHeaderRow(Math.max(1, Number(event.target.value) || 1))}
                  className="h-8 w-20 rounded-lg border border-border bg-card px-2 text-foreground"
                />
                行为表头
              </label>
            )}
            <span className="text-[11px] text-muted-foreground">表头之前的说明行会被跳过。</span>
          </div>
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              handleFile(e.dataTransfer.files?.[0]);
            }}
            onClick={() => { if (!busy) fileInputRef.current?.click(); }}
            className={`border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-all ${dragging ? "border-primary bg-secondary" : "border-border hover:border-primary/50 hover:bg-secondary/30"}`}
          >
            <div className="w-14 h-14 bg-secondary rounded-xl flex items-center justify-center mx-auto mb-3">
              <Upload size={24} className="text-primary" />
            </div>
            <p className="text-sm font-medium text-foreground mb-1">
              拖拽文件到此处，或点击选择文件
            </p>
            <p className="text-xs text-muted-foreground mb-4">
              支持 .csv、.xlsx、.xls 格式
            </p>
            <div className="flex justify-center gap-3 text-xs text-muted-foreground">
              <span className="px-2.5 py-1 bg-muted rounded-md">CSV</span>
              <span className="px-2.5 py-1 bg-muted rounded-md">Excel</span>
              <button
                type="button"
                disabled={busy}
                onClick={(e) => {
                  e.stopPropagation();
                  loadSample();
                }}
                className="px-2.5 py-1 bg-muted rounded-md hover:bg-secondary disabled:opacity-50"
              >
                {busy ? "正在导入..." : "加载示例数据"}
              </button>
            </div>
          </div>
        </div>
      )}

      {tab === "grid" && !dataset && (
        <div className="bg-card rounded-2xl border border-border overflow-hidden">
          <div className="flex items-center gap-1.5 px-4 py-2.5 border-b border-border bg-muted/40 flex-wrap">
            <select
              value={pasteHeaderMode}
              onChange={(event) => setPasteHeaderMode(event.target.value as "row" | "none")}
              className="h-8 rounded-lg border border-border bg-card px-2 text-xs"
              title="粘贴时如何识别表头"
            >
              <option value="row">粘贴内容包含表头</option>
              <option value="none">粘贴内容没有表头</option>
            </select>
            {pasteHeaderMode === "row" && (
              <label className="flex items-center gap-1 text-xs text-muted-foreground">
                第
                <input
                  type="number"
                  min={1}
                  value={pasteHeaderRow}
                  onChange={(event) => setPasteHeaderRow(Math.max(1, Number(event.target.value) || 1))}
                  className="h-8 w-16 rounded-lg border border-border bg-card px-2 text-foreground"
                />
                行作为表头
              </label>
            )}
            <button
              onClick={() => fileInputRef.current?.click()}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-border bg-card hover:bg-muted"
            >
              <Upload size={12} /> 上传文件
            </button>
            <button
              onClick={() =>
                setGridData((prev) => [
                  ...prev,
                  Array(gridHeaders.length).fill(""),
                ])
              }
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-border bg-card hover:bg-muted"
            >
              <Plus size={12} /> 添加行
            </button>
            <button
              onClick={() => {
                setGridHeaders((p) => [...p, `变量${p.length + 1}`]);
                setGridData((p) => p.map((r) => [...r, ""]));
              }}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-border bg-card hover:bg-muted"
            >
              <Plus size={12} /> 添加列
            </button>
            <button
              onClick={deleteLastGridRow}
              disabled={gridData.length === 0}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-border bg-card hover:bg-muted disabled:opacity-50"
            >
              <Trash2 size={12} /> 删除末行
            </button>
            <button
              onClick={deleteLastGridColumn}
              disabled={gridHeaders.length <= 1}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-border bg-card hover:bg-muted disabled:opacity-50"
            >
              <Trash2 size={12} /> 删除末列
            </button>
            <button
              onClick={clearGrid}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-border bg-card hover:bg-muted text-muted-foreground"
            >
              <Trash2 size={12} /> 清空表格
            </button>
            <button
              onClick={loadGridSample}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-border bg-card hover:bg-muted"
            >
              <FlaskConical size={12} /> 示例数据
            </button>
            <div className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
              <Clipboard size={11} /> 从 Excel 复制后 Ctrl+V 粘贴
            </div>
          </div>
          <div
            className="overflow-auto max-h-[460px]"
            onPaste={(e) => {
              e.preventDefault();
              const text = e.clipboardData.getData("text/plain").trim();
              if (!text) return;
              const rows = text.split(/\r?\n/).map((row) => row.split("\t"));
              setRawPastedMatrix(rows);
              applyPastedMatrix(rows);
            }}
            tabIndex={0}
            style={{ outline: "none" }}
          >
            <table
              className="border-collapse select-none"
              style={{ fontSize: cs.fontSize }}
            >
              <thead className="sticky top-0 z-10">
                <tr>
                  <th
                    className="bg-muted border-b border-r border-border text-muted-foreground font-normal sticky left-0 z-20"
                    style={{ padding: cs.padding, minWidth: 44, width: 44 }}
                  >
                    #
                  </th>
                  {gridHeaders.map((h, ci) => (
                    <th
                      key={ci}
                      className="bg-muted border-b border-r border-border text-left font-medium text-foreground"
                      style={{ padding: cs.padding, minWidth: 124 }}
                    >
                      <div className="flex items-center gap-1.5">
                        <input
                          value={h}
                          onChange={(e) =>
                            setGridHeaders((prev) =>
                              prev.map((v, i) =>
                                i === ci ? e.target.value : v,
                              ),
                            )
                          }
                          className="bg-transparent w-full focus:outline-none font-medium text-foreground"
                        />
                        <button
                          title="删除这一列"
                          onClick={() => deleteGridColumn(ci)}
                          disabled={gridHeaders.length <= 1}
                          className="p-1 rounded hover:bg-red-50 text-muted-foreground hover:text-red-600 disabled:opacity-30"
                        >
                          <Trash2 size={11} />
                        </button>
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {gridData.map((row, ri) => (
                  <tr
                    key={ri}
                    className="hover:bg-secondary/30 transition-colors"
                  >
                    <td
                      className="bg-muted/60 border-b border-r border-border text-center text-muted-foreground sticky left-0"
                      style={{ padding: cs.padding }}
                    >
                      <div className="flex items-center justify-center gap-1">
                        <span>{ri + 1}</span>
                        <button
                          title="删除这一行"
                          onClick={() => deleteGridRow(ri)}
                          className="p-0.5 rounded hover:bg-red-50 hover:text-red-600"
                        >
                          <Trash2 size={10} />
                        </button>
                      </div>
                    </td>
                    {gridHeaders.map((_, ci) => (
                      <td
                        key={ci}
                        className="border-b border-r border-border/60"
                        style={{ minWidth: 96 }}
                      >
                        <input
                          value={row[ci] || ""}
                          onChange={(e) =>
                            setGridData((prev) =>
                              prev.map((r, rr) =>
                                rr === ri
                                  ? gridHeaders.map((_, cc) =>
                                      cc === ci ? e.target.value : r[cc] || "",
                                    )
                                  : r,
                              ),
                            )
                          }
                          className="w-full bg-transparent focus:outline-none text-foreground"
                          style={{ padding: cs.padding, fontSize: cs.fontSize }}
                        />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between px-4 py-2.5 border-t border-border bg-muted/20">
            <span className="text-xs text-muted-foreground">
              {gridData.length} 行 × {gridHeaders.length} 列 ·
              {pasteHeaderMode === "none" ? "已自动生成变量名" : `第 ${pasteHeaderRow} 行作为变量名`}
            </span>
            <button
              onClick={importGrid}
              disabled={busy || gridHeaders.length === 0}
              className="flex items-center gap-1.5 text-xs bg-primary text-primary-foreground px-4 py-2 rounded-lg hover:opacity-90 disabled:opacity-50"
            >
              <Check size={13} /> {busy ? "后端检查中..." : "检查并导入数据"}
            </button>
          </div>
        </div>
      )}

      {headerDialogOpen && dataset && (
        <div className="fixed inset-0 z-50 bg-black/35 grid place-items-center p-4">
          <div className="w-full max-w-lg rounded-2xl border border-border bg-card shadow-xl p-5 space-y-4">
            <div>
              <h3 className="text-base font-semibold text-foreground">重新设置表头</h3>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">将从最初上传的“{dataset.name}”重新解析。表头之前的说明行可以跳过，也可以选择无表头模式自动生成变量名。重新解析会丢弃后来保存的数据编辑，并重置模型、变量角色和分析结果。</p>
            </div>
            <div className="flex items-center gap-3 flex-wrap">
              <select value={headerDraftMode} onChange={(event) => setHeaderDraftMode(event.target.value as "row" | "none")} className="h-9 rounded-lg border border-border bg-background px-3 text-sm">
                <option value="row">指定一行作为表头</option><option value="none">没有表头，自动生成变量名</option>
              </select>
              {headerDraftMode === "row" && <label className="flex items-center gap-1 text-sm text-muted-foreground">第<input type="number" min={1} value={headerDraftRow} onChange={(event) => setHeaderDraftRow(Math.max(1, Number(event.target.value) || 1))} className="h-9 w-20 rounded-lg border border-border bg-background px-2 text-foreground" />行为表头</label>}
            </div>
            <div className="flex justify-end gap-2"><button onClick={() => setHeaderDialogOpen(false)} disabled={busy} className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-muted disabled:opacity-50">取消</button><button onClick={() => void applyHeaderSettings()} disabled={busy} className="rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground hover:opacity-90 disabled:opacity-50">{busy ? "重新解析中..." : "应用表头设置"}</button></div>
          </div>
        </div>
      )}

      {columnDialogOpen && dataset && (
        <div className="fixed inset-0 z-50 bg-black/35 grid place-items-center p-4">
          <div className="w-full max-w-lg rounded-2xl border border-border bg-card shadow-xl p-5 space-y-4">
            <div><h3 className="text-base font-semibold text-foreground">新增变量列</h3><p className="mt-1 text-xs leading-5 text-muted-foreground">{activeCell ? `新列会插入到“${dataset.headers[activeCell.col]}”右侧。` : "未选择单元格时，新列会添加到表格末尾。"} 创建后可以直接从 Excel 复制整列并粘贴。</p></div>
            <label className="block space-y-1.5 text-sm"><span className="text-xs text-muted-foreground">变量名称</span><input value={newColumnName} onChange={(event) => setNewColumnName(event.target.value)} className="h-9 w-full rounded-lg border border-border bg-background px-3" /></label>
            <div className="grid grid-cols-2 gap-3">
              <label className="space-y-1.5 text-sm"><span className="text-xs text-muted-foreground">字段类型</span><select value={newColumnType} onChange={(event) => setNewColumnType(event.target.value)} className="h-9 w-full rounded-lg border border-border bg-background px-3">{VARIABLE_TYPE_OPTIONS.map((type) => <option key={type} value={type}>{type}</option>)}</select></label>
              <label className="space-y-1.5 text-sm"><span className="text-xs text-muted-foreground">默认值（可留空）</span><input value={newColumnDefault} onChange={(event) => setNewColumnDefault(event.target.value)} className="h-9 w-full rounded-lg border border-border bg-background px-3" /></label>
            </div>
            <div className="flex justify-end gap-2"><button onClick={() => setColumnDialogOpen(false)} disabled={busy} className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-muted disabled:opacity-50">取消</button><button onClick={() => void addDatasetColumn()} disabled={busy || !newColumnName.trim()} className="rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground hover:opacity-90 disabled:opacity-50">{busy ? "新增中..." : "新增列"}</button></div>
          </div>
        </div>
      )}

      {replaceDialogOpen && dataset && (
        <div className="fixed inset-0 z-50 bg-black/35 grid place-items-center p-4">
          <div className="w-full max-w-lg rounded-2xl border border-border bg-card shadow-xl p-5 space-y-4">
            <div>
              <h3 className="text-base font-semibold text-foreground">替换当前数据</h3>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                新文件会先上传并成功绑定到当前项目，之后才删除“{dataset.name}”。更换后现有模型、变量角色和分析结果会被清空；字段表头将按下面的设置重新解析。
              </p>
            </div>
            {pendingCellCount > 0 && (
              <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                当前还有 {pendingCellCount} 处未保存编辑。替换数据后这些修改不会保留。
              </div>
            )}
            <div className="flex items-center gap-2 flex-wrap">
              <select
                value={fileHeaderMode}
                onChange={(event) =>
                  setFileHeaderMode(event.target.value as "row" | "none")
                }
                className="h-9 rounded-lg border border-border bg-background px-3 text-sm"
              >
                <option value="row">文件包含表头</option>
                <option value="none">文件没有表头</option>
              </select>
              {fileHeaderMode === "row" && (
                <label className="flex items-center gap-1 text-sm text-muted-foreground">
                  第
                  <input
                    type="number"
                    min={1}
                    value={fileHeaderRow}
                    onChange={(event) =>
                      setFileHeaderRow(
                        Math.max(1, Number(event.target.value) || 1),
                      )
                    }
                    className="h-9 w-16 rounded-lg border border-border bg-background px-2 text-foreground"
                  />
                  行作为表头
                </label>
              )}
            </div>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setReplaceDialogOpen(false)}
                disabled={busy}
                className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-muted disabled:opacity-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => replaceFileInputRef.current?.click()}
                disabled={busy}
                className="rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground hover:opacity-90 disabled:opacity-50"
              >
                {busy ? "正在替换..." : "选择新文件并替换"}
              </button>
            </div>
          </div>
        </div>
      )}

      {dataset && (
        <div className="space-y-4">
          <div className="rounded-xl border border-blue-200 bg-blue-50 px-4 py-2 text-xs text-blue-800">
            虚拟表格显示全量 {dataset.full_row_count || dataset.profile.row_count} 行；浏览器只渲染可见区域并按 200 行分页加载。可框选区域后批量复制、粘贴或填充，分析始终使用后端全量数据。
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3 flex-wrap">
              <div className="flex items-center gap-2 bg-emerald-50 border border-emerald-200 rounded-xl px-3 py-2">
                <CheckCircle2 size={14} className="text-emerald-600" />
                <span className="text-xs text-emerald-700 font-medium">
                  已载入 · {dataset.profile.row_count} 行 ×{" "}
                  {dataset.profile.column_count} 列
                </span>
              </div>
              {dataset.profile.issue_count > 0 ? (
                <div className="flex items-center gap-2 bg-amber-50 border border-amber-200 rounded-xl px-3 py-2">
                  <AlertTriangle size={14} className="text-amber-600" />
                  <span className="text-xs text-amber-700 font-medium">
                    发现 {dataset.profile.issue_count} 处问题
                  </span>
                </div>
              ) : (
                <div className="flex items-center gap-2 bg-emerald-50 border border-emerald-200 rounded-xl px-3 py-2">
                  <CheckCircle2 size={14} className="text-emerald-600" />
                  <span className="text-xs text-emerald-700 font-medium">
                    未发现明显问题
                  </span>
                </div>
              )}
            </div>
            <div className="flex gap-2 flex-wrap">
              <button
                onClick={() => spreadsheetRef.current?.undo()}
                disabled={!canUndo || busy}
                title="撤销最近一次未保存编辑（Ctrl/Cmd+Z）"
                className="flex items-center gap-1.5 text-xs border border-border px-3 py-2 rounded-lg hover:bg-muted disabled:opacity-40"
              >
                <Undo2 size={12} /> 撤销
              </button>
              <button
                onClick={() => spreadsheetRef.current?.redo()}
                disabled={!canRedo || busy}
                title="重做（Ctrl/Cmd+Y 或 Ctrl/Cmd+Shift+Z）"
                className="flex items-center gap-1.5 text-xs border border-border px-3 py-2 rounded-lg hover:bg-muted disabled:opacity-40"
              >
                <Redo2 size={12} /> 重做
              </button>
              <button onClick={() => void addDatasetRow()} disabled={busy || pendingCellCount > 0} title={activeCell ? `在第 ${activeCell.row + 1} 行下方新增` : "在数据末尾新增一行"} className="flex items-center gap-1.5 text-xs border border-border px-3 py-2 rounded-lg hover:bg-muted disabled:opacity-40"><Plus size={12} /> 新增行</button>
              <button onClick={() => void removeDatasetRow()} disabled={busy || pendingCellCount > 0} title={activeCell ? `删除第 ${activeCell.row + 1} 行` : "未选中单元格时可输入行号删除"} className="flex items-center gap-1.5 text-xs border border-border px-3 py-2 rounded-lg hover:bg-muted disabled:opacity-40"><Trash2 size={12} /> 删除行</button>
              <button onClick={openAddColumnDialog} disabled={busy || pendingCellCount > 0} title={activeCell ? "在当前列右侧新增变量" : "在表格末尾新增变量"} className="flex items-center gap-1.5 text-xs border border-border px-3 py-2 rounded-lg hover:bg-muted disabled:opacity-40"><Plus size={12} /> 新增列</button>
              <button onClick={() => void removeDatasetColumn()} disabled={busy || pendingCellCount > 0 || dataset.headers.length <= 1} title={activeCell ? `删除变量 ${dataset.headers[activeCell.col]}` : "未选中单元格时可输入列号删除"} className="flex items-center gap-1.5 text-xs border border-border px-3 py-2 rounded-lg hover:bg-muted disabled:opacity-40"><Trash2 size={12} /> 删除列</button>
              {dataset.can_reparse_header && <button onClick={openHeaderSettings} disabled={busy || pendingCellCount > 0} className="flex items-center gap-1.5 text-xs border border-border px-3 py-2 rounded-lg hover:bg-muted disabled:opacity-40" title="从原始上传文件重新选择表头行"><Table2 size={12} /> 表头设置</button>}
              <button
                onClick={saveEdits}
                disabled={busy || pendingCellCount === 0}
                className="flex items-center gap-1.5 text-xs bg-primary text-primary-foreground px-3 py-2 rounded-lg hover:opacity-90 disabled:opacity-50"
              >
                <Check size={12} /> {pendingCellCount > 0 ? `保存 ${pendingCellCount} 处修改` : "暂无待保存修改"}
              </button>
              <button
                onClick={exportCSV}
                className="flex items-center gap-1.5 text-xs border border-border px-3 py-2 rounded-lg hover:bg-muted"
              >
                <Download size={12} /> 导出
              </button>
              <button
                onClick={() => setReplaceDialogOpen(true)}
                disabled={busy}
                className="flex items-center gap-1.5 text-xs text-muted-foreground border border-border px-3 py-2 rounded-lg hover:bg-muted disabled:opacity-50"
              >
                <RefreshCw size={12} /> 替换数据
              </button>
              <button
                onClick={() => void handleDeleteDataset()}
                disabled={busy}
                className="flex items-center gap-1.5 text-xs text-red-600 border border-red-200 px-3 py-2 rounded-lg hover:bg-red-50 disabled:opacity-50"
              >
                <Trash2 size={12} /> 删除数据
              </button>
            </div>
          </div>

          <div
            className={`rounded-2xl border p-4 ${selectedModel ? "bg-card border-border" : "bg-muted/30 border-dashed border-border"}`}
          >
            <div className="flex items-start justify-between gap-4 flex-wrap">
              <div className="flex items-start gap-3">
                <div
                  className={`w-9 h-9 rounded-xl grid place-items-center ${selectedModel ? "bg-secondary text-primary" : "bg-muted text-muted-foreground"}`}
                >
                  {selectedModel ? (
                    <SlidersHorizontal size={17} />
                  ) : (
                    <Lock size={17} />
                  )}
                </div>
                <div>
                  <div className="text-sm font-medium text-foreground">
                    {method?.name || selectedModel || "变量角色尚未解锁"}
                  </div>
                  <p className="text-xs text-muted-foreground mt-1 max-w-3xl">
                    {selectedModel
                      ? method?.requirement?.summary ||
                        "正在读取所选模型的变量要求..."
                      : "字段类型可以直接在每列表头修改；因变量、自变量、控制变量等角色要等选择模型后才会解锁。"}
                  </p>
                  {method?.validation_messages?.length ? (
                    <div className="mt-2 text-xs text-amber-700">
                      {method.validation_messages.join("；")}
                    </div>
                  ) : null}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <div
                  className={`text-xs px-2.5 py-1 rounded-full border ${method?.enabled ? "bg-emerald-50 text-emerald-700 border-emerald-100" : selectedModel ? "bg-amber-50 text-amber-700 border-amber-100" : "bg-muted text-muted-foreground border-border"}`}
                >
                  {checkingMethod
                    ? "正在校验"
                    : method?.enabled
                      ? "变量配置完成"
                      : selectedModel
                        ? "等待表头配置"
                        : "角色已锁定"}
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setDataReviewStatus("reviewed");
                    if (onOpenModelSelector) onOpenModelSelector();
                    else onNavigate?.("models");
                  }}
                  className="rounded-lg border border-border bg-card px-3 py-1.5 text-xs text-foreground hover:bg-muted"
                >
                  {selectedModel ? "更换模型 / 参数" : "选择模型"}
                </button>
              </div>
            </div>
          </div>

          <div className="bg-card rounded-2xl border border-border overflow-hidden">
            <div className="flex items-center justify-between px-5 py-3 border-b border-border gap-3 flex-wrap">
              <span className="text-sm font-medium text-foreground">
                虚拟数据编辑器 · {dataset.name} · 全量 {(dataset.full_row_count || dataset.profile.row_count).toLocaleString()} 行
              </span>
              <span className="text-xs text-muted-foreground flex items-center gap-1">
                <Info size={11} /> 框选区域后可复制/粘贴/填充；Ctrl/Cmd+Z 撤销，Ctrl/Cmd+Y 或 Ctrl/Cmd+Shift+Z 重做
              </span>
            </div>
            <VirtualSpreadsheetGrid
              ref={spreadsheetRef}
              dataset={dataset}
              selectedModel={selectedModel}
              method={method}
              selection={selection}
              requirements={requirements}
              requiredRoleIds={requiredRoleIds}
              onTypeChange={updateVariableType}
              onRoleChange={updateColumnRole}
              onSaveChanges={saveDatasetCellChanges}
              onPendingChange={handlePendingChange}
              onHistoryChange={({ canUndo: nextCanUndo, canRedo: nextCanRedo }) => {
                setCanUndo(nextCanUndo);
                setCanRedo(nextCanRedo);
              }}
              onLoadedChange={setLoadedRowCount}
              onActiveCellChange={setActiveCell}
              onMessage={setMessage}
            />
          </div>

          <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
            <div className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded border bg-amber-100 border-amber-200" />
              缺失值
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded border bg-red-100 border-red-200" />
              异常值
            </div>
            <span>
              · 字段类型和变量角色保存在表头；已缓存 {loadedRowCount.toLocaleString()} 行。{activeCell ? `当前定位：第 ${activeCell.row + 1} 行 · ${dataset.headers[activeCell.col]}` : "点击任意单元格后可在其所在行/列附近进行结构编辑"}。撤销/重做针对本次未保存单元格编辑，行列结构操作保存后立即生效
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
