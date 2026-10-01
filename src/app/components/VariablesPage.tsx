import { useEffect, useMemo, useState } from "react";
import { Check, ChevronRight, HelpCircle, Settings2, SlidersHorizontal, Wand2 } from "lucide-react";
import { recommendMethods } from "../lib/api";
import { useWorkspace } from "../context/WorkspaceContext";
import type { DatasetProfileColumn, MethodItem, MethodRoleRequirement, VariableSelection, VariableType } from "../lib/api";

interface VariablesPageProps {
  onNavigate: (page: string) => void;
}

const TYPE_OPTIONS: VariableType[] = ["ID型", "连续型", "整数型", "计数型", "分类型", "二分类", "有序型", "文本型", "未知"];
const LIST_ROLES = new Set(["independent_variables", "control_variables", "mediator_variables", "moderator_variables"]);

function asType(value?: string | null): VariableType {
  if (!value) return "未知";
  return (TYPE_OPTIONS as string[]).includes(value) ? value as VariableType : "未知";
}

function defaultTypeMap(columns: DatasetProfileColumn[]): Record<string, VariableType | string> {
  return Object.fromEntries(columns.map((column) => [column.name, asType(column.type)]));
}

function emptySelection(columns: DatasetProfileColumn[], previous?: VariableSelection | null): VariableSelection {
  const valid = new Set(columns.map((column) => column.name));
  const unique = (values?: string[]) => Array.from(new Set((values || []).filter((value) => valid.has(value))));
  const dependent = previous?.dependent_variable && valid.has(previous.dependent_variable) ? previous.dependent_variable : "";
  const group = previous?.group_variable && valid.has(previous.group_variable) ? previous.group_variable : null;
  return {
    dependent_variable: dependent,
    independent_variables: unique(previous?.independent_variables).filter((value) => value !== dependent && value !== group),
    control_variables: unique(previous?.control_variables).filter((value) => value !== dependent && value !== group),
    mediator_variables: unique(previous?.mediator_variables).filter((value) => value !== dependent && value !== group),
    moderator_variables: unique(previous?.moderator_variables).filter((value) => value !== dependent && value !== group),
    group_variable: group,
    variable_types: { ...defaultTypeMap(columns), ...(previous?.variable_types || {}) },
    analysis_goal: previous?.analysis_goal || "",
  };
}

function typeBadgeClass(type: string) {
  if (["连续型", "整数型", "计数型"].includes(type)) return "bg-blue-50 text-blue-700 border-blue-100";
  if (["分类型", "二分类", "有序型"].includes(type)) return "bg-purple-50 text-purple-700 border-purple-100";
  if (type === "ID型") return "bg-slate-50 text-slate-600 border-slate-100";
  if (type === "文本型") return "bg-amber-50 text-amber-700 border-amber-100";
  return "bg-muted text-muted-foreground border-border";
}

function roleValues(selection: VariableSelection, role: string): string[] {
  if (LIST_ROLES.has(role)) return ((selection as any)[role] || []) as string[];
  const value = (selection as any)[role];
  return value ? [String(value)] : [];
}

function selectedAnywhere(selection: VariableSelection, exceptRole: string): Set<string> {
  const values = new Set<string>();
  ["dependent_variable", "independent_variables", "control_variables", "mediator_variables", "moderator_variables", "group_variable"].forEach((role) => {
    if (role === exceptRole) return;
    roleValues(selection, role).forEach((value) => values.add(value));
  });
  return values;
}

export function VariablesPage({ onNavigate }: VariablesPageProps) {
  const {
    activeProject,
    dataset,
    selectedVariables,
    selectedModel,
    setSelectedVariables,
  } = useWorkspace();
  const [method, setMethod] = useState<MethodItem | null>(null);
  const [checking, setChecking] = useState(false);
  const columns = dataset?.profile.columns || [];
  const selection = useMemo(() => emptySelection(columns, selectedVariables), [columns, selectedVariables]);


  useEffect(() => {
    if (!dataset || !selectedModel) {
      setMethod(null);
      return;
    }
    let cancelled = false;
    setChecking(true);
    recommendMethods({
      dataset_id: dataset.dataset_id,
      research_goal: activeProject?.research_goal || "",
      selected_model: selectedModel,
      ...selection,
    }).then((response) => {
      if (!cancelled) setMethod((response.recommended || []).find((item) => item.method_id === selectedModel) || null);
    }).finally(() => {
      if (!cancelled) setChecking(false);
    });
    return () => { cancelled = true; };
  }, [dataset?.dataset_id, selectedModel, selection, activeProject?.research_goal]);

  function commit(next: VariableSelection) {
    setSelectedVariables(emptySelection(columns, next));
  }

  function updateType(name: string, type: string) {
    commit({ ...selection, variable_types: { ...(selection.variable_types || {}), [name]: type } });
  }

  function setRole(role: string, name: string) {
    const next: any = { ...selection };
    if (LIST_ROLES.has(role)) {
      const current = roleValues(selection, role);
      next[role] = current.includes(name) ? current.filter((value) => value !== name) : [...current, name];
    } else {
      next[role] = roleValues(selection, role).includes(name) ? (role === "group_variable" ? null : "") : name;
    }
    // A field has one analytical role at a time. Remove it from the others.
    ["dependent_variable", "independent_variables", "control_variables", "mediator_variables", "moderator_variables", "group_variable"].forEach((otherRole) => {
      if (otherRole === role) return;
      if (LIST_ROLES.has(otherRole)) next[otherRole] = (next[otherRole] || []).filter((value: string) => value !== name);
      else if (next[otherRole] === name) next[otherRole] = otherRole === "group_variable" ? null : "";
    });
    commit(next);
  }

  if (!activeProject || !dataset) {
    return (
      <div className="p-6 max-w-4xl">
        <div className="bg-card rounded-2xl border border-border p-8 text-center">
          <h3 className="text-foreground mb-2">请先准备数据工作区</h3>
          <p className="text-sm text-muted-foreground mb-4">上传数据后可以检查字段类型；选择模型后再配置变量角色。</p>
          <button onClick={() => onNavigate(!activeProject ? "home" : "upload")} className="px-4 py-2 rounded-xl bg-primary text-primary-foreground text-sm">返回数据工作区</button>
        </div>
      </div>
    );
  }

  const requirements: MethodRoleRequirement[] = method
    ? [...(method.requirement?.required_roles || []), ...(method.requirement?.optional_roles || [])]
    : [];

  const VariableChip = ({ column, requirement }: { column: DatasetProfileColumn; requirement: MethodRoleRequirement }) => {
    const currentType = String(selection.variable_types?.[column.name] || column.type || "未知");
    const selected = roleValues(selection, requirement.role).includes(column.name);
    const usedElsewhere = selectedAnywhere(selection, requirement.role).has(column.name);
    const typeAllowed = !requirement.allowed_types?.length || requirement.allowed_types.includes(currentType);
    const disabled = !selected && (usedElsewhere || !typeAllowed);
    return (
      <button
        onClick={() => setRole(requirement.role, column.name)}
        disabled={disabled}
        className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-xs transition-all text-left ${selected ? "bg-primary text-primary-foreground border-primary shadow-sm" : disabled ? "bg-muted/40 text-muted-foreground border-border opacity-50 cursor-not-allowed" : "bg-card text-foreground border-border hover:border-primary/40"}`}
      >
        <span className="truncate">{column.name}</span>
        <span className={`shrink-0 text-xs px-1.5 py-0.5 rounded border ${selected ? "bg-white/20 text-white border-white/20" : typeBadgeClass(currentType)}`}>{currentType}</span>
        {selected && <Check size={12} />}
      </button>
    );
  };

  return (
    <div className="p-6 space-y-5 max-w-6xl">
      <div className="bg-card rounded-2xl border border-border p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="text-foreground mb-1">数据工作区 · 字段与模型变量</h3>
            <p className="text-sm text-muted-foreground">
              字段类型属于数据本身，可以随时修正；因变量、自变量、分组变量等角色由所选模型的 requirement 动态生成，不再提前猜测。
            </p>
          </div>
          <button onClick={() => onNavigate("models")} className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-primary text-primary-foreground text-sm">
            返回模型选择 <ChevronRight size={15} />
          </button>
        </div>
      </div>

      <div className="bg-card rounded-2xl border border-border p-5">
        <div className="flex items-center gap-2 mb-4">
          <Settings2 size={16} className="text-primary" />
          <h3 className="text-foreground">字段类型检查</h3>
          <span className="text-xs text-muted-foreground">模型推荐和 requirement 校验都使用这里的类型</span>
        </div>
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-2">
          {columns.map((column) => (
            <div key={column.name} className="rounded-xl border border-border bg-muted/20 p-3">
              <div className="flex items-center justify-between gap-2 mb-2">
                <span className="text-sm font-medium text-foreground truncate">{column.name}</span>
                <span className="text-xs text-muted-foreground">缺失 {Math.round((column.missing_rate || 0) * 100)}%</span>
              </div>
              <select value={String(selection.variable_types?.[column.name] || asType(column.type))} onChange={(e) => updateType(column.name, e.target.value)} className="w-full h-8 rounded-lg border border-border bg-card px-2 text-xs outline-none focus:border-primary">
                {TYPE_OPTIONS.map((type) => <option key={type} value={type}>{type}</option>)}
              </select>
              <div className="mt-2 text-[11px] text-muted-foreground line-clamp-1">自动识别：{column.type} · 唯一值 {column.unique_count}</div>
            </div>
          ))}
        </div>
      </div>

      {!selectedModel || !method ? (
        <div className="bg-secondary rounded-2xl border border-primary/10 p-6 flex items-start gap-3">
          <Wand2 size={20} className="text-primary shrink-0 mt-0.5" />
          <div className="flex-1">
            <h3 className="text-foreground mb-1">先选择模型，再配置变量角色</h3>
            <p className="text-sm text-muted-foreground">当前只保存字段类型，不会自动指定因变量。选择模型后，这里只展示该模型真正需要的角色。</p>
          </div>
          <button onClick={() => onNavigate("models")} className="px-4 py-2 rounded-xl bg-primary text-primary-foreground text-sm">选择模型</button>
        </div>
      ) : (
        <>
          <div className="bg-card rounded-2xl border border-border p-5">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="flex items-center gap-2"><SlidersHorizontal size={17} className="text-primary" /><h3 className="text-foreground">{method.name}：变量角色配置</h3></div>
                <p className="text-sm text-muted-foreground mt-1">{method.requirement?.summary}</p>
              </div>
              <span className={`text-xs px-2.5 py-1 rounded-full border ${method.enabled ? "bg-emerald-50 text-emerald-700 border-emerald-100" : "bg-amber-50 text-amber-700 border-amber-100"}`}>{checking ? "正在校验" : method.enabled ? "配置完成" : "仍需配置"}</span>
            </div>
            {method.validation_messages?.length > 0 && <div className="mt-3 rounded-xl bg-amber-50 border border-amber-100 px-4 py-3 text-xs text-amber-800 space-y-1">{method.validation_messages.map((message) => <div key={message}>• {message}</div>)}</div>}
          </div>

          <div className="grid lg:grid-cols-2 gap-4">
            {requirements.map((requirement) => {
              const required = (method.requirement?.required_roles || []).some((item) => item.role === requirement.role);
              const selectedCount = roleValues(selection, requirement.role).length;
              return (
                <div key={requirement.role} className="bg-card rounded-2xl border border-border p-5">
                  <div className="flex items-center justify-between gap-2 mb-3">
                    <div><h3 className="text-foreground">{requirement.label}</h3><p className="text-xs text-muted-foreground mt-1">{required ? `必选，至少 ${requirement.min_count} 个` : "可选"}{requirement.allowed_types?.length ? ` · 类型：${requirement.allowed_types.join("、")}` : ""}</p></div>
                    <span className="text-xs text-muted-foreground">已选 {selectedCount}</span>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {columns.map((column) => <VariableChip key={column.name} column={column} requirement={requirement} />)}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="bg-secondary rounded-xl p-4 border border-primary/10 flex items-start gap-3">
            <HelpCircle size={18} className="text-primary shrink-0 mt-0.5" />
            <div className="text-sm text-secondary-foreground flex-1">
              <p className="font-medium mb-1">后端 requirement 校验</p>
              <p className="text-muted-foreground leading-6">{method.reason}</p>
            </div>
            <button onClick={() => onNavigate("models")} className="flex items-center gap-1.5 text-sm bg-primary text-primary-foreground px-4 py-2 rounded-xl hover:opacity-90 transition-opacity">
              {method.enabled ? "返回并运行" : "返回模型页"} <ChevronRight size={15} />
            </button>
          </div>
        </>
      )}
    </div>
  );
}
