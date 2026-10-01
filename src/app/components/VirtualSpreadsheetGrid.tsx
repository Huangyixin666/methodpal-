import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { RevoGrid, Template } from "@revolist/react-datagrid";
import type { AfterEditEvent, ColumnRegular } from "@revolist/react-datagrid";
import type {
  DatasetCellChange,
  DatasetResponse,
  MethodItem,
  MethodRoleRequirement,
  VariableSelection,
} from "../lib/api";
import { getDatasetRows } from "../lib/api";
import {
  LIST_ROLE_FIELDS,
  requirementAllowsType,
  roleForColumn,
  roleValues,
  VARIABLE_TYPE_OPTIONS,
} from "../lib/variableWorkspace";
import "./VirtualSpreadsheetGrid.css";

const PAGE_SIZE = 200;
const ROW_SIZE = 34;
const MAX_HISTORY = 100;
const HISTORY_BATCH_MS = 40;

type GridRow = Record<string, any> & {
  __rowIndex: number;
  __loaded: boolean;
  __issues?: Record<string, { label: string; type: string }>;
};

type CellMutation = {
  row: number;
  col: number;
  before: string;
  after: string;
};

type EditTransaction = {
  changes: CellMutation[];
};

type HeaderTemplateProps = {
  name: string;
  prop: string;
  currentType: string;
  currentRole: string;
  selectedModel: string | null;
  rolesLocked: boolean;
  requirements: MethodRoleRequirement[];
  requiredRoleIds: Set<string>;
  roleDisabled: (requirement: MethodRoleRequirement) => boolean;
  onTypeChange: (value: string) => void;
  onRoleChange: (value: string) => void;
  autoType: string;
  missingRate: number;
};

function SpreadsheetHeader(props: HeaderTemplateProps) {
  const stop = (event: React.SyntheticEvent) => event.stopPropagation();
  return (
    <div
      className={`methodpal-grid-header ${props.currentRole ? "is-assigned" : ""}`}
      onMouseDown={stop}
      onClick={stop}
    >
      <div className="methodpal-grid-header-name" title={props.name}>
        {props.name}
      </div>
      <label>
        <span>字段类型</span>
        <select
          value={props.currentType}
          onChange={(event) => props.onTypeChange(event.target.value)}
        >
          {VARIABLE_TYPE_OPTIONS.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>变量角色</span>
        <select
          value={props.currentRole}
          disabled={props.rolesLocked}
          onChange={(event) => props.onRoleChange(event.target.value)}
        >
          <option value="">
            {!props.selectedModel
              ? "选择模型后解锁"
              : props.rolesLocked
                ? "正在读取模型要求"
                : "未分配"}
          </option>
          {props.requirements.map((requirement) => (
            <option
              key={requirement.role}
              value={requirement.role}
              disabled={props.roleDisabled(requirement)}
            >
              {requirement.label}
              {props.requiredRoleIds.has(requirement.role) ? " *" : ""}
            </option>
          ))}
        </select>
      </label>
      <div className="methodpal-grid-header-meta">
        自动：{props.autoType} · 缺失 {Math.round(props.missingRate * 100)}%
      </div>
    </div>
  );
}

const headerTemplate = Template(SpreadsheetHeader as any);

export type VirtualSpreadsheetGridHandle = {
  savePending: () => Promise<DatasetResponse | null>;
  pendingCount: () => number;
  undo: () => boolean;
  redo: () => boolean;
  canUndo: () => boolean;
  canRedo: () => boolean;
  activeCell: () => { row: number; col: number } | null;
};

type Props = {
  dataset: DatasetResponse;
  selectedModel: string | null;
  method: MethodItem | null;
  selection: VariableSelection;
  requirements: MethodRoleRequirement[];
  requiredRoleIds: Set<string>;
  onTypeChange: (columnName: string, value: string) => void;
  onRoleChange: (columnName: string, value: string) => void;
  onSaveChanges: (changes: DatasetCellChange[]) => Promise<DatasetResponse | null>;
  onPendingChange?: (count: number) => void;
  onLoadedChange?: (count: number) => void;
  onHistoryChange?: (state: { canUndo: boolean; canRedo: boolean }) => void;
  onActiveCellChange?: (cell: { row: number; col: number } | null) => void;
  onMessage?: (message: string) => void;
};

function makeEmptySource(total: number): GridRow[] {
  return Array.from({ length: total }, (_, index) => ({
    __rowIndex: index,
    __loaded: false,
  }));
}

function cellKey(row: number, col: number) {
  return `${row}:${col}`;
}

export const VirtualSpreadsheetGrid = forwardRef<
  VirtualSpreadsheetGridHandle,
  Props
>(function VirtualSpreadsheetGrid(
  {
    dataset,
    selectedModel,
    method,
    selection,
    requirements,
    requiredRoleIds,
    onTypeChange,
    onRoleChange,
    onSaveChanges,
    onPendingChange,
    onLoadedChange,
    onHistoryChange,
    onActiveCellChange,
    onMessage,
  },
  ref,
) {
  const totalRows =
    dataset.full_row_count || dataset.profile?.row_count || dataset.rows.length;
  const [source, setSource] = useState<GridRow[]>(() =>
    makeEmptySource(totalRows),
  );
  const [loadedCount, setLoadedCount] = useState(0);
  const shellRef = useRef<HTMLDivElement>(null);
  const pendingRef = useRef<Map<string, DatasetCellChange>>(new Map());
  const baseValuesRef = useRef<Map<string, string>>(new Map());
  const currentValuesRef = useRef<Map<string, string>>(new Map());
  const undoStackRef = useRef<EditTransaction[]>([]);
  const redoStackRef = useRef<EditTransaction[]>([]);
  const historyBatchRef = useRef<Map<string, CellMutation>>(new Map());
  const historyBatchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loadedPagesRef = useRef<Set<number>>(new Set());
  const loadedPageCountsRef = useRef<Map<number, number>>(new Map());
  const loadingPagesRef = useRef<Set<number>>(new Set());
  const propToColumnRef = useRef<Record<string, number>>({});
  const activeCellRef = useRef<{ row: number; col: number } | null>(null);

  const emitHistoryState = useCallback(() => {
    onHistoryChange?.({
      canUndo:
        undoStackRef.current.length > 0 || historyBatchRef.current.size > 0,
      canRedo: redoStackRef.current.length > 0,
    });
  }, [onHistoryChange]);

  const clearHistory = useCallback(() => {
    if (historyBatchTimerRef.current) {
      clearTimeout(historyBatchTimerRef.current);
      historyBatchTimerRef.current = null;
    }
    historyBatchRef.current.clear();
    undoStackRef.current = [];
    redoStackRef.current = [];
    emitHistoryState();
  }, [emitHistoryState]);

  const flushHistoryBatch = useCallback(() => {
    if (historyBatchTimerRef.current) {
      clearTimeout(historyBatchTimerRef.current);
      historyBatchTimerRef.current = null;
    }
    const changes = Array.from(historyBatchRef.current.values()).filter(
      (change) => change.before !== change.after,
    );
    historyBatchRef.current.clear();
    if (!changes.length) {
      emitHistoryState();
      return;
    }
    undoStackRef.current.push({ changes });
    if (undoStackRef.current.length > MAX_HISTORY)
      undoStackRef.current.splice(
        0,
        undoStackRef.current.length - MAX_HISTORY,
      );
    redoStackRef.current = [];
    emitHistoryState();
  }, [emitHistoryState]);

  const scheduleHistoryFlush = useCallback(() => {
    if (historyBatchTimerRef.current)
      clearTimeout(historyBatchTimerRef.current);
    historyBatchTimerRef.current = setTimeout(
      flushHistoryBatch,
      HISTORY_BATCH_MS,
    );
  }, [flushHistoryBatch]);

  const updatePendingCell = useCallback(
    (row: number, col: number, value: string) => {
      const key = cellKey(row, col);
      const baseValue = baseValuesRef.current.get(key) ?? "";
      currentValuesRef.current.set(key, value);
      if (value === baseValue) pendingRef.current.delete(key);
      else pendingRef.current.set(key, { row, col, value });
      onPendingChange?.(pendingRef.current.size);
    },
    [onPendingChange],
  );

  const applyMutationValues = useCallback(
    (changes: CellMutation[], direction: "undo" | "redo") => {
      if (!changes.length) return;
      changes.forEach((change) => {
        const value = direction === "undo" ? change.before : change.after;
        updatePendingCell(change.row, change.col, value);
      });
      setSource((previous) => {
        const next = [...previous];
        changes.forEach((change) => {
          const value = direction === "undo" ? change.before : change.after;
          const prop = `c${change.col}`;
          const existing = next[change.row];
          if (existing?.__loaded)
            next[change.row] = { ...existing, [prop]: value };
        });
        return next;
      });
    },
    [updatePendingCell],
  );

  const undo = useCallback(() => {
    flushHistoryBatch();
    const transaction = undoStackRef.current.pop();
    if (!transaction) {
      emitHistoryState();
      return false;
    }
    applyMutationValues(transaction.changes, "undo");
    redoStackRef.current.push(transaction);
    emitHistoryState();
    return true;
  }, [applyMutationValues, emitHistoryState, flushHistoryBatch]);

  const redo = useCallback(() => {
    flushHistoryBatch();
    const transaction = redoStackRef.current.pop();
    if (!transaction) {
      emitHistoryState();
      return false;
    }
    applyMutationValues(transaction.changes, "redo");
    undoStackRef.current.push(transaction);
    emitHistoryState();
    return true;
  }, [applyMutationValues, emitHistoryState, flushHistoryBatch]);

  const applyRows = useCallback(
    (
      offset: number,
      rows: string[][],
      cells: Array<Array<{ issue?: { label?: string } | null }>> = [],
    ) => {
      setSource((previous) => {
        const next = [...previous];
        rows.forEach((row, localIndex) => {
          const absoluteIndex = offset + localIndex;
          if (absoluteIndex >= next.length) return;
          const model: GridRow = {
            __rowIndex: absoluteIndex,
            __loaded: true,
            __issues: {},
          };
          dataset.headers.forEach((_, columnIndex) => {
            const prop = `c${columnIndex}`;
            const key = cellKey(absoluteIndex, columnIndex);
            const serverValue = String(row[columnIndex] ?? "");
            baseValuesRef.current.set(key, serverValue);
            const pending = pendingRef.current.get(key);
            const displayValue = pending ? pending.value : serverValue;
            currentValuesRef.current.set(key, displayValue);
            model[prop] = displayValue;
            const issue = cells[localIndex]?.[columnIndex]?.issue as any;
            if (issue?.label)
              model.__issues![prop] = {
                label: issue.label,
                type: String(issue.type || "missing"),
              };
          });
          next[absoluteIndex] = model;
        });
        return next;
      });
    },
    [dataset.headers],
  );

  const loadPage = useCallback(
    async (page: number) => {
      const safePage = Math.max(0, page);
      if (safePage * PAGE_SIZE >= totalRows) return;
      if (
        loadedPagesRef.current.has(safePage) ||
        loadingPagesRef.current.has(safePage)
      )
        return;
      loadingPagesRef.current.add(safePage);
      try {
        const response = await getDatasetRows(
          dataset.dataset_id,
          safePage * PAGE_SIZE,
          PAGE_SIZE,
        );
        applyRows(response.offset, response.rows, response.cells);
        loadedPagesRef.current.add(safePage);
        loadedPageCountsRef.current.set(safePage, response.rows.length);
        const count = Math.min(
          totalRows,
          Array.from(loadedPageCountsRef.current.values()).reduce(
            (sum, value) => sum + value,
            0,
          ),
        );
        setLoadedCount(count);
        onLoadedChange?.(count);
      } catch (error: any) {
        onMessage?.(error.message || "数据分页加载失败");
      } finally {
        loadingPagesRef.current.delete(safePage);
      }
    },
    [applyRows, dataset.dataset_id, onLoadedChange, onMessage, totalRows],
  );

  useEffect(() => {
    pendingRef.current.clear();
    activeCellRef.current = null;
    onActiveCellChange?.(null);
    baseValuesRef.current.clear();
    currentValuesRef.current.clear();
    clearHistory();
    onPendingChange?.(0);
    loadedPagesRef.current.clear();
    loadedPageCountsRef.current.clear();
    loadingPagesRef.current.clear();
    setSource(makeEmptySource(totalRows));
    setLoadedCount(0);
    if (dataset.rows.length) {
      applyRows(0, dataset.rows, dataset.cells as any);
      loadedPagesRef.current.add(0);
      loadedPageCountsRef.current.set(0, dataset.rows.length);
      const count = Math.min(totalRows, dataset.rows.length);
      setLoadedCount(count);
      onLoadedChange?.(count);
    } else {
      void loadPage(0);
    }
  }, [dataset.dataset_id, dataset.message, totalRows]);

  useEffect(
    () => () => {
      if (historyBatchTimerRef.current)
        clearTimeout(historyBatchTimerRef.current);
    },
    [],
  );

  useEffect(() => {
    const shell = shellRef.current;
    if (!shell) return;
    const handleFocus = (event: Event) => {
      const detail: any = (event as CustomEvent).detail || {};
      const rowCandidate = detail.model?.__rowIndex ?? detail.rowIndex ?? detail.row ?? detail.focus?.rowIndex ?? detail.focus?.row;
      const propCandidate = detail.prop ?? detail.column?.prop ?? detail.focus?.prop ?? detail.colProp;
      const row = Number(rowCandidate);
      const col = typeof propCandidate === "string" ? propToColumnRef.current[propCandidate] : Number(detail.col ?? detail.columnIndex ?? detail.focus?.col);
      if (!Number.isFinite(row) || !Number.isFinite(col)) return;
      const next = { row, col };
      activeCellRef.current = next;
      onActiveCellChange?.(next);
    };
    shell.addEventListener("afterfocus", handleFocus as EventListener);
    return () => shell.removeEventListener("afterfocus", handleFocus as EventListener);
  }, [onActiveCellChange]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const path = event.composedPath?.() || [];
      if (!shellRef.current || !path.includes(shellRef.current)) return;
      const command = event.ctrlKey || event.metaKey;
      if (!command || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === "z") {
        event.preventDefault();
        event.stopPropagation();
        if (event.shiftKey) redo();
        else undo();
      } else if (key === "y") {
        event.preventDefault();
        event.stopPropagation();
        redo();
      }
    };
    window.addEventListener("keydown", handleKeyDown, true);
    return () => window.removeEventListener("keydown", handleKeyDown, true);
  }, [redo, undo]);

  const roleOptionDisabled = useCallback(
    (
      requirement: MethodRoleRequirement,
      columnName: string,
      currentRole: string,
    ) => {
      if (currentRole === requirement.role) return false;
      const currentType = String(
        selection.variable_types?.[columnName] || "未知",
      );
      if (!requirementAllowsType(requirement, currentType)) return true;
      if (!LIST_ROLE_FIELDS.has(requirement.role)) return false;
      if (requirement.max_count == null) return false;
      return (
        roleValues(selection, requirement.role).length >= requirement.max_count
      );
    },
    [selection],
  );

  const columns = useMemo<ColumnRegular[]>(() => {
    const mapping: Record<string, number> = {};
    const result = dataset.headers.map((name, columnIndex) => {
      const prop = `c${columnIndex}`;
      mapping[prop] = columnIndex;
      const profile = dataset.profile?.columns?.[columnIndex];
      const currentType = String(
        selection.variable_types?.[name] || profile?.type || "未知",
      );
      const currentRole = roleForColumn(selection, requirements, name);
      return {
        name,
        prop,
        size: 205,
        sortable: false,
        readonly: ({ model }: any) => !model?.__loaded,
        columnTemplate: headerTemplate,
        currentType,
        currentRole,
        selectedModel,
        rolesLocked: !selectedModel || !method,
        requirements,
        requiredRoleIds,
        autoType: profile?.type || "未知",
        missingRate: profile?.missing_rate || 0,
        roleDisabled: (requirement: MethodRoleRequirement) =>
          roleOptionDisabled(requirement, name, currentRole),
        onTypeChange: (value: string) => onTypeChange(name, value),
        onRoleChange: (value: string) => onRoleChange(name, value),
        cellProperties: ({ model }: any) => {
          const issue = model?.__issues?.[prop];
          if (!issue) return undefined;
          const isOutlier = String(issue.type).startsWith("outlier");
          return {
            title: issue.label,
            class: isOutlier
              ? "methodpal-grid-cell-outlier"
              : "methodpal-grid-cell-missing",
          };
        },
      } as any;
    });
    propToColumnRef.current = mapping;
    return result;
  }, [
    dataset.headers,
    dataset.profile?.columns,
    method,
    onRoleChange,
    onTypeChange,
    requiredRoleIds,
    requirements,
    roleOptionDisabled,
    selectedModel,
    selection,
  ]);

  const queueMutation = useCallback(
    (row: number, prop: string, value: any) => {
      const col = propToColumnRef.current[prop];
      if (col == null || row < 0 || row >= totalRows) return;
      const key = cellKey(row, col);
      const before = currentValuesRef.current.get(key) ?? "";
      const after = value == null ? "" : String(value);
      if (before === after) return;

      const existing = historyBatchRef.current.get(key);
      historyBatchRef.current.set(key, {
        row,
        col,
        before: existing?.before ?? before,
        after,
      });
      updatePendingCell(row, col, after);
      scheduleHistoryFlush();
      emitHistoryState();
    },
    [emitHistoryState, scheduleHistoryFlush, totalRows, updatePendingCell],
  );

  const handleAfterEdit = useCallback(
    (event: CustomEvent<AfterEditEvent>) => {
      const detail: any = event.detail;
      if (detail && typeof detail.rowIndex === "number") {
        queueMutation(
          detail.rowIndex,
          String(detail.prop),
          detail.val ?? detail.model?.[detail.prop],
        );
        return;
      }
      if (detail?.data) {
        Object.entries(detail.data).forEach(([rowIndex, model]: any) => {
          const oldModel = detail.models?.[rowIndex] || {};
          Object.keys(propToColumnRef.current).forEach((prop) => {
            if (String(model?.[prop] ?? "") !== String(oldModel?.[prop] ?? ""))
              queueMutation(Number(rowIndex), prop, model?.[prop]);
          });
        });
      }
    },
    [queueMutation],
  );

  const savePending = useCallback(async () => {
    flushHistoryBatch();
    const changes = Array.from(pendingRef.current.values());
    if (!changes.length) return dataset;
    const saved = await onSaveChanges(changes);
    if (saved) {
      changes.forEach((change) => {
        const key = cellKey(change.row, change.col);
        baseValuesRef.current.set(key, change.value);
        currentValuesRef.current.set(key, change.value);
      });
      pendingRef.current.clear();
      onPendingChange?.(0);
      clearHistory();
    }
    return saved;
  }, [clearHistory, dataset, flushHistoryBatch, onPendingChange, onSaveChanges]);

  useImperativeHandle(
    ref,
    () => ({
      savePending,
      pendingCount: () => pendingRef.current.size,
      undo,
      redo,
      canUndo: () =>
        undoStackRef.current.length > 0 || historyBatchRef.current.size > 0,
      canRedo: () => redoStackRef.current.length > 0,
      activeCell: () => activeCellRef.current,
    }),
    [redo, savePending, undo],
  );

  return (
    <div ref={shellRef} className="methodpal-virtual-grid-shell">
      <RevoGrid
        className="methodpal-revogrid"
        style={{ height: "570px", width: "100%" }}
        theme="default"
        columns={columns}
        source={source}
        rowHeaders
        rowSize={ROW_SIZE}
        range
        useClipboard={{ rangeFill: true }}
        resize
        readonly={false}
        onAfteredit={handleAfterEdit as any}
        onViewportscroll={(event: any) => {
          const detail = event.detail || {};
          if (!String(detail.dimension || "").toLowerCase().includes("row"))
            return;
          const firstVisible = Math.max(
            0,
            Math.floor(Number(detail.coordinate || 0) / ROW_SIZE),
          );
          const page = Math.floor(firstVisible / PAGE_SIZE);
          void loadPage(page);
          void loadPage(page + 1);
        }}
      />
      <div className="methodpal-grid-loading-status">
        已缓存 {loadedCount.toLocaleString()} / {totalRows.toLocaleString()} 行；滚动时自动加载更多
      </div>
    </div>
  );
});
