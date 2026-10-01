import { useEffect, useMemo, useState } from "react";
import {
  BarChart3,
  Check,
  ChevronRight,
  Lock,
  Search,
  Settings2,
  Sparkles,
  Star,
  Wrench,
  X,
} from "lucide-react";
import { recommendMethods } from "../lib/api";
import { useWorkspace } from "../context/WorkspaceContext";
import type { MethodItem, VariableSelection } from "../lib/api";
import {
  compatibleSelectionForRequirements,
  datasetColumns,
  hasAnyAnalyticalRole,
  roleRequirements,
  typesOnlySelection,
} from "../lib/variableWorkspace";

type ContentProps = {
  onNavigate?: (page: string) => void;
  onClose?: () => void;
  variant?: "page" | "dialog";
};

type DialogProps = {
  open: boolean;
  onClose: () => void;
};

const CATEGORY_ORDER = [
  "基础分析",
  "差异检验",
  "回归模型",
  "分类模型",
  "机制模型",
  "模型对比",
];
const CATEGORY_STYLE: Record<
  string,
  { color: string; dot: string; desc: string }
> = {
  基础分析: {
    color: "bg-blue-50 text-blue-700 border-blue-100",
    dot: "bg-blue-500",
    desc: "描述分布、相关和列联关系。",
  },
  差异检验: {
    color: "bg-purple-50 text-purple-700 border-purple-100",
    dot: "bg-purple-500",
    desc: "比较不同组之间的均值差异或分类关联。",
  },
  回归模型: {
    color: "bg-emerald-50 text-emerald-700 border-emerald-100",
    dot: "bg-emerald-500",
    desc: "连续或计数结局的解释与预测模型。",
  },
  分类模型: {
    color: "bg-orange-50 text-orange-700 border-orange-100",
    dot: "bg-orange-500",
    desc: "二分类、多分类和有序分类模型。",
  },
  机制模型: {
    color: "bg-pink-50 text-pink-700 border-pink-100",
    dot: "bg-pink-500",
    desc: "中介、调节和理论路径分析。",
  },
  模型对比: {
    color: "bg-slate-50 text-slate-700 border-slate-100",
    dot: "bg-slate-500",
    desc: "统一比较多个预测模型。",
  },
};

function inputClass() {
  return "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary";
}

function stars(score: number) {
  const count = Math.max(1, Math.min(5, Math.round(score * 5)));
  return Array.from({ length: 5 }).map((_, i) => (
    <Star
      key={i}
      size={13}
      className={
        i < count
          ? "fill-yellow-400 text-yellow-400"
          : "text-muted-foreground/30"
      }
    />
  ));
}

function availabilityLabel(method: MethodItem) {
  if (method.availability === "ready") return "可直接运行";
  if (method.availability === "needs_configuration") return "需配置变量";
  if (method.availability === "unsupported") return "暂未实现";
  if (method.availability === "incompatible") return "当前数据不适用";
  return "可选择";
}

function availabilityClass(method: MethodItem) {
  if (method.availability === "ready")
    return "bg-emerald-50 text-emerald-700 border-emerald-100";
  if (method.availability === "needs_configuration")
    return "bg-amber-50 text-amber-700 border-amber-100";
  return "bg-muted text-muted-foreground border-border";
}

function emptySelection(): VariableSelection {
  return {
    dependent_variable: "",
    independent_variables: [],
    control_variables: [],
    mediator_variables: [],
    moderator_variables: [],
    group_variable: null,
    variable_types: {},
  };
}

function analyticalRoleCount(selection?: VariableSelection | null) {
  if (!selection) return 0;
  return (
    (selection.dependent_variable ? 1 : 0) +
    (selection.group_variable ? 1 : 0) +
    (selection.independent_variables?.length || 0) +
    (selection.control_variables?.length || 0) +
    (selection.mediator_variables?.length || 0) +
    (selection.moderator_variables?.length || 0)
  );
}

function ModelSelectionContent({
  onNavigate,
  onClose,
  variant = "page",
}: ContentProps) {
  const {
    activeProject,
    dataset,
    selectedVariables,
    selectedModel,
    modelOptions,
    setSelectedModel,
  } = useWorkspace();
  const [methods, setMethods] = useState<MethodItem[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [draftModel, setDraftModel] = useState<string | null>(selectedModel);
  const [draftOptions, setDraftOptions] = useState<Record<string, any>>({
    ...(modelOptions || {}),
  });

  useEffect(() => {
    setDraftModel(selectedModel);
    setDraftOptions({ ...(modelOptions || {}) });
  }, [selectedModel, modelOptions]);

  const recommendationSelection = useMemo(
    () =>
      draftModel && draftModel !== selectedModel
        ? typesOnlySelection(selectedVariables)
        : selectedVariables || emptySelection(),
    [draftModel, selectedModel, selectedVariables],
  );

  useEffect(() => {
    if (!dataset) {
      setMethods([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    recommendMethods({
      dataset_id: dataset.dataset_id,
      research_goal: activeProject?.research_goal || "",
      selected_model: draftModel,
      ...recommendationSelection,
    })
      .then((response) => {
        if (!cancelled) setMethods(response.recommended || []);
      })
      .catch((e: any) => {
        if (!cancelled) setError(e.message || "模型目录加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    dataset?.dataset_id,
    activeProject?.research_goal,
    draftModel,
    recommendationSelection,
  ]);

  const currentMethod = methods.find((m) => m.method_id === draftModel) || null;
  const options = useMemo(
    () => ({
      ...(currentMethod?.default_options || {}),
      ...(draftOptions || {}),
    }),
    [currentMethod, draftOptions],
  );

  const grouped = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    const filtered = methods.filter(
      (m) =>
        !keyword ||
        [
          m.name,
          m.method_id,
          m.scene,
          m.category,
          m.requirement?.summary,
          ...(m.tags || []),
        ]
          .join(" ")
          .toLowerCase()
          .includes(keyword),
    );
    return CATEGORY_ORDER.map((category) => ({
      category,
      methods: filtered.filter((m) => m.category === category),
    })).filter((group) => group.methods.length > 0);
  }, [methods, search]);

  const recommended = useMemo(
    () => methods.filter((m) => m.selectable).slice(0, 5),
    [methods],
  );

  function choose(method: MethodItem) {
    if (!method.selectable) return;
    setError("");
    setDraftModel(method.method_id);
    setDraftOptions(
      method.method_id === selectedModel
        ? { ...(modelOptions || {}) }
        : { ...(method.default_options || {}) },
    );
  }

  function setOption(key: string, value: any) {
    setDraftOptions({ ...options, [key]: value });
  }

  function cleanedOptions() {
    const out: Record<string, any> = { ...options };
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

  function confirmSelection() {
    if (!currentMethod || !draftModel || !currentMethod.selectable) return;

    const changingModel = draftModel !== selectedModel;
    let nextSelection = selectedVariables;
    if (changingModel) {
      const requirements = roleRequirements(
        currentMethod.requirement?.required_roles || [],
        currentMethod.requirement?.optional_roles || [],
      );
      nextSelection = compatibleSelectionForRequirements(
        datasetColumns(dataset.headers, dataset.profile?.columns || []),
        selectedVariables,
        requirements,
      );

      if (hasAnyAnalyticalRole(selectedVariables)) {
        const beforeCount = analyticalRoleCount(selectedVariables);
        const preservedCount = analyticalRoleCount(nextSelection);
        const confirmed = window.confirm(
          `确定把模型更换为“${currentMethod.name}”吗？\n\n` +
            `系统会保留字段类型，并尝试保留与新模型兼容的变量角色。` +
            `当前 ${beforeCount} 个角色中预计可保留 ${preservedCount} 个；其余角色会被清除。`,
        );
        if (!confirmed) return;
      }
    }

    setSelectedModel(
      draftModel,
      cleanedOptions(),
      changingModel ? nextSelection : undefined,
    );
    if (onClose) onClose();
    else onNavigate?.("upload");
  }

  if (!activeProject || !dataset) {
    return (
      <div className="p-6 max-w-4xl">
        <div className="bg-card rounded-2xl border border-border p-8 text-center">
          <h3 className="text-foreground mb-2">请先准备数据工作区</h3>
          <p className="text-sm text-muted-foreground mb-4">
            模型选择只依赖数据字段类型，不再要求预先填写因变量和自变量。
          </p>
          <button
            onClick={() => {
              if (onClose) onClose();
              else onNavigate?.(!activeProject ? "home" : "upload");
            }}
            className="px-4 py-2 rounded-xl bg-primary text-primary-foreground text-sm"
          >
            返回数据工作区
          </button>
        </div>
      </div>
    );
  }

  const isTreeBoost = Boolean(
    draftModel &&
    [
      "random_forest_regression",
      "random_forest_classification",
      "xgboost_regression",
      "xgboost_classification",
      "catboost_regression",
      "catboost_classification",
    ].includes(draftModel),
  );
  const isBoost = Boolean(
    draftModel &&
    [
      "xgboost_regression",
      "xgboost_classification",
      "catboost_regression",
      "catboost_classification",
    ].includes(draftModel),
  );
  const isSvm = Boolean(
    draftModel && ["svm_regression", "svm_classification"].includes(draftModel),
  );
  const isSgd = Boolean(
    draftModel && ["sgd_regression", "sgd_classification"].includes(draftModel),
  );
  const isRegressionFamily = currentMethod?.family === "regression";

  return (
    <div
      className={
        variant === "dialog"
          ? "p-5 space-y-5 w-full"
          : "p-6 space-y-6 max-w-7xl"
      }
    >
      <div className="grid lg:grid-cols-[1fr_360px] gap-4">
        <div className="bg-card rounded-2xl border border-border p-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h3 className="text-foreground mb-1">选择分析模型</h3>
              <p className="text-sm text-muted-foreground">
                模型目录、要求和可用性均来自后端统一
                schema。确认后窗口会自动关闭，并在当前数据工作区解锁对应的变量角色。
              </p>
            </div>
            <button
              onClick={confirmSelection}
              disabled={!currentMethod || !currentMethod.selectable}
              className="flex items-center gap-2 px-4 py-2 rounded-xl bg-primary text-primary-foreground text-sm disabled:opacity-50"
            >
              <Wrench size={16} />
              {currentMethod ? "使用此模型" : "请先选择模型"}
            </button>
          </div>
          {error && (
            <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 whitespace-pre-wrap">
              {error}
            </div>
          )}
          {loading && (
            <div className="mt-3 text-xs text-muted-foreground">
              正在根据当前数据重新评估模型...
            </div>
          )}
          <div className="grid md:grid-cols-5 gap-3 mt-4">
            {recommended.map((method) => (
              <button
                key={method.method_id}
                disabled={!method.selectable}
                onClick={() => choose(method)}
                className={`text-left rounded-xl border p-4 transition-all ${draftModel === method.method_id ? "border-primary shadow-md" : "border-border hover:border-primary/40 hover:shadow-sm"} ${method.selectable ? "bg-card" : "bg-muted/50 opacity-60 cursor-not-allowed"}`}
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-medium text-foreground">
                    {method.name}
                  </span>
                  {!method.selectable && (
                    <Lock size={14} className="text-muted-foreground" />
                  )}
                </div>
                <div className="flex gap-0.5 mb-2">{stars(method.score)}</div>
                <p className="text-xs text-muted-foreground line-clamp-2">
                  {method.reason}
                </p>
              </button>
            ))}
          </div>
        </div>

        <div className="bg-card rounded-2xl border border-border p-5">
          <h3 className="text-foreground mb-3">当前工作区</h3>
          <div className="space-y-2 text-xs">
            <div className="rounded-lg bg-muted/50 p-3">
              <span className="text-muted-foreground">已选模型</span>
              <div className="font-medium text-foreground mt-1">
                {currentMethod?.name || "尚未选择"}
              </div>
              {draftModel && draftModel !== selectedModel && (
                <div className="text-[10px] text-amber-700 mt-1">
                  当前为预览，点击“使用此模型”后才会写入项目
                </div>
              )}
            </div>
            <div className="rounded-lg bg-muted/50 p-3">
              <span className="text-muted-foreground">变量配置</span>
              <div className="font-medium text-foreground mt-1">
                {currentMethod
                  ? availabilityLabel(currentMethod)
                  : "选择模型后生成要求"}
              </div>
            </div>
            <div className="rounded-lg bg-muted/50 p-3">
              <span className="text-muted-foreground">数据规模</span>
              <div className="font-medium text-foreground mt-1">
                {dataset.profile?.row_count ||
                  dataset.full_row_count ||
                  dataset.rows?.length ||
                  0}{" "}
                行 ×{" "}
                {dataset.profile?.column_count || dataset.headers?.length || 0}{" "}
                列
              </div>
            </div>
          </div>
          <button
            onClick={() => {
              if (onClose) onClose();
              else onNavigate?.("upload");
            }}
            className="mt-3 w-full rounded-xl border border-border px-3 py-2 text-sm hover:bg-muted"
          >
            {variant === "dialog" ? "取消并关闭" : "返回数据工作区"}
          </button>
        </div>
      </div>

      <div className="grid lg:grid-cols-[1fr_360px] gap-4">
        <div className="space-y-4">
          <div className="bg-card rounded-2xl border border-border p-4">
            <div className="relative">
              <Search
                size={16}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"
              />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜索模型，比如 XGBoost / 多分类 / 非线性 / 回归"
                className="w-full rounded-xl border border-border bg-background pl-9 pr-3 py-2 text-sm outline-none focus:border-primary"
              />
            </div>
          </div>

          {grouped.map(({ category, methods: categoryMethods }) => {
            const style =
              CATEGORY_STYLE[category] || CATEGORY_STYLE["基础分析"];
            return (
              <div
                key={category}
                className="bg-card rounded-2xl border border-border overflow-hidden"
              >
                <div className="px-5 py-4 border-b border-border flex items-start gap-3">
                  <span
                    className={`mt-1 w-2.5 h-2.5 rounded-full ${style.dot}`}
                  />
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className="text-foreground">{category}</h3>
                      <span
                        className={`text-xs px-2 py-0.5 rounded-full border ${style.color}`}
                      >
                        {categoryMethods.length} 个
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground mt-1">
                      {style.desc}
                    </p>
                  </div>
                </div>
                <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-3 p-4">
                  {categoryMethods.map((method) => {
                    const active = draftModel === method.method_id;
                    return (
                      <button
                        key={method.method_id}
                        disabled={!method.selectable}
                        onClick={() => choose(method)}
                        className={`text-left rounded-xl border p-4 transition-all ${active ? "border-primary bg-primary/5 shadow-sm" : "border-border bg-card hover:border-primary/40 hover:shadow-sm"} ${method.selectable ? "" : "opacity-55 cursor-not-allowed bg-muted/40"}`}
                      >
                        <div className="flex items-start justify-between gap-2 mb-2">
                          <div>
                            <div className="font-medium text-sm text-foreground">
                              {method.name}
                            </div>
                            <div className="text-[11px] text-muted-foreground mt-0.5">
                              {method.method_id}
                            </div>
                          </div>
                          {active ? (
                            <Check size={16} className="text-primary" />
                          ) : method.selectable ? (
                            <ChevronRight
                              size={16}
                              className="text-muted-foreground"
                            />
                          ) : (
                            <Lock size={16} className="text-muted-foreground" />
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground mb-2 line-clamp-2">
                          {method.scene}
                        </p>
                        <div className="flex flex-wrap gap-1 mb-2">
                          {(method.tags || []).map((tag) => (
                            <span
                              key={tag}
                              className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground"
                            >
                              {tag}
                            </span>
                          ))}
                        </div>
                        <div className="flex items-center justify-between gap-2 mb-2">
                          <div className="flex gap-0.5">
                            {stars(method.score)}
                          </div>
                          <span
                            className={`text-[10px] px-1.5 py-0.5 rounded-full border ${availabilityClass(method)}`}
                          >
                            {availabilityLabel(method)}
                          </span>
                        </div>
                        <p className="text-xs text-muted-foreground">
                          {method.reason}
                        </p>
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>

        <div className="space-y-4">
          <div className="bg-card rounded-2xl border border-border p-5 sticky top-4">
            <div className="flex items-center gap-2 mb-3">
              <Settings2 size={18} className="text-primary" />
              <h3 className="text-foreground">模型要求与参数</h3>
            </div>
            {!currentMethod ? (
              <div className="rounded-xl border border-dashed border-border p-5 text-sm text-muted-foreground">
                从左侧选择一个模型并查看要求。确认后窗口会关闭，变量角色仍在数据表头配置。
              </div>
            ) : (
              <>
                <div className="rounded-xl border border-border p-3 mb-4">
                  <div className="text-sm font-medium text-foreground">
                    {currentMethod.name}
                  </div>
                  <p className="text-xs text-muted-foreground mt-1">
                    {currentMethod.requirement?.summary}
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    输出：{(currentMethod.outputs || []).join("；")}
                  </p>
                  {currentMethod.validation_messages?.length > 0 && (
                    <div className="mt-2 text-xs text-amber-700 space-y-1">
                      {currentMethod.validation_messages.map((message) => (
                        <div key={message}>• {message}</div>
                      ))}
                    </div>
                  )}
                </div>

                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-2">
                    <label className="text-xs text-muted-foreground">
                      测试集比例
                      <input
                        type="number"
                        step="0.05"
                        min="0.1"
                        max="0.5"
                        value={options.test_size ?? 0.25}
                        onChange={(e) => setOption("test_size", e.target.value)}
                        className={inputClass()}
                      />
                    </label>
                    <label className="text-xs text-muted-foreground">
                      交叉验证折数
                      <input
                        type="number"
                        min="2"
                        max="10"
                        value={options.cv_folds ?? 5}
                        onChange={(e) => setOption("cv_folds", e.target.value)}
                        className={inputClass()}
                      />
                    </label>
                  </div>
                  <label className="text-xs text-muted-foreground">
                    随机种子
                    <input
                      type="number"
                      value={options.random_state ?? 42}
                      onChange={(e) =>
                        setOption("random_state", e.target.value)
                      }
                      className={inputClass()}
                    />
                  </label>

                  {isRegressionFamily && (
                    <div className="rounded-xl bg-muted/40 p-3 space-y-2">
                      <div className="flex items-center gap-2 text-sm text-foreground">
                        <Sparkles size={15} /> 显式非线性特征
                      </div>
                      <label className="text-xs text-muted-foreground">
                        多项式最高阶
                        <select
                          value={options.polynomial_degree ?? 1}
                          onChange={(e) =>
                            setOption("polynomial_degree", e.target.value)
                          }
                          className={inputClass()}
                        >
                          <option value={1}>1：不添加</option>
                          <option value={2}>2：加入二次项</option>
                          <option value={3}>3：加入二次 + 三次项</option>
                        </select>
                      </label>
                      <label className="flex items-center gap-2 text-xs text-muted-foreground">
                        <input
                          type="checkbox"
                          checked={Boolean(options.add_log_terms)}
                          onChange={(e) =>
                            setOption("add_log_terms", e.target.checked)
                          }
                        />{" "}
                        加入 log1p(x) 项
                      </label>
                      <label className="flex items-center gap-2 text-xs text-muted-foreground">
                        <input
                          type="checkbox"
                          checked={Boolean(options.add_exp_terms)}
                          onChange={(e) =>
                            setOption("add_exp_terms", e.target.checked)
                          }
                        />{" "}
                        加入 exp(z-score) 项
                      </label>
                    </div>
                  )}

                  {isTreeBoost && (
                    <div className="rounded-xl bg-muted/40 p-3 space-y-2">
                      <div className="flex items-center gap-2 text-sm text-foreground">
                        <BarChart3 size={15} /> 树模型 / Boosting 参数
                      </div>
                      <div className="grid grid-cols-2 gap-2">
                        <label className="text-xs text-muted-foreground">
                          树数量
                          <input
                            type="number"
                            min="20"
                            max="3000"
                            value={options.n_estimators ?? 300}
                            onChange={(e) =>
                              setOption("n_estimators", e.target.value)
                            }
                            className={inputClass()}
                          />
                        </label>
                        <label className="text-xs text-muted-foreground">
                          最大深度
                          <input
                            type="number"
                            min="1"
                            max="20"
                            value={options.max_depth ?? 4}
                            onChange={(e) =>
                              setOption("max_depth", e.target.value)
                            }
                            className={inputClass()}
                          />
                        </label>
                      </div>
                      {isBoost && (
                        <div className="grid grid-cols-2 gap-2">
                          <label className="text-xs text-muted-foreground">
                            学习率
                            <input
                              type="number"
                              step="0.01"
                              min="0.001"
                              max="1"
                              value={options.learning_rate ?? 0.05}
                              onChange={(e) =>
                                setOption("learning_rate", e.target.value)
                              }
                              className={inputClass()}
                            />
                          </label>
                          <label className="text-xs text-muted-foreground">
                            Subsample
                            <input
                              type="number"
                              step="0.05"
                              min="0.3"
                              max="1"
                              value={options.subsample ?? 0.9}
                              onChange={(e) =>
                                setOption("subsample", e.target.value)
                              }
                              className={inputClass()}
                            />
                          </label>
                        </div>
                      )}
                    </div>
                  )}
                  {isSvm && (
                    <div className="rounded-xl bg-muted/40 p-3">
                      <label className="text-xs text-muted-foreground">
                        核函数
                        <select
                          value={options.kernel ?? "rbf"}
                          onChange={(e) => setOption("kernel", e.target.value)}
                          className={inputClass()}
                        >
                          <option value="rbf">RBF</option>
                          <option value="linear">Linear</option>
                          <option value="poly">Polynomial</option>
                          <option value="sigmoid">Sigmoid</option>
                        </select>
                      </label>
                    </div>
                  )}
                  {isSgd && (
                    <div className="rounded-xl bg-muted/40 p-3 space-y-2">
                      <div className="text-sm text-foreground">SGD 参数</div>
                      <div className="grid grid-cols-2 gap-2">
                        <label className="text-xs text-muted-foreground">
                          Penalty
                          <select
                            value={options.penalty ?? "elasticnet"}
                            onChange={(e) =>
                              setOption("penalty", e.target.value)
                            }
                            className={inputClass()}
                          >
                            <option value="elasticnet">ElasticNet</option>
                            <option value="l2">L2</option>
                            <option value="l1">L1</option>
                          </select>
                        </label>
                        <label className="text-xs text-muted-foreground">
                          Alpha
                          <input
                            type="number"
                            step="0.0001"
                            value={options.alpha ?? 0.0001}
                            onChange={(e) => setOption("alpha", e.target.value)}
                            className={inputClass()}
                          />
                        </label>
                      </div>
                    </div>
                  )}
                </div>

                <button
                  onClick={confirmSelection}
                  disabled={!currentMethod.selectable}
                  className="mt-4 w-full flex items-center justify-center gap-2 rounded-xl bg-primary text-primary-foreground px-4 py-2 text-sm disabled:opacity-50"
                >
                  <Wrench size={16} /> 使用此模型
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export function ModelSelectionPage({
  onNavigate,
}: {
  onNavigate: (page: string) => void;
}) {
  return <ModelSelectionContent onNavigate={onNavigate} variant="page" />;
}

export function ModelSelectionDialog({ open, onClose }: DialogProps) {
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="absolute inset-0 z-50 flex items-center justify-center bg-slate-950/30 p-4 backdrop-blur-[1px]"
      role="dialog"
      aria-modal="true"
      aria-label="选择分析模型"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="flex h-[calc(100%_-_1rem)] w-full max-w-[1480px] flex-col overflow-hidden rounded-2xl border border-border bg-background shadow-2xl"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-card px-5">
          <div>
            <div className="text-sm font-semibold text-foreground">
              选择分析模型
            </div>
            <div className="text-[11px] text-muted-foreground">
              浏览模型不会修改项目；点击“使用此模型”后才会保存并关闭窗口。
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="grid h-8 w-8 place-items-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label="关闭模型选择"
            title="关闭"
          >
            <X size={17} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <ModelSelectionContent onClose={onClose} variant="dialog" />
        </div>
      </div>
    </div>
  );
}
