import json
import re
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.agent import (
    AgentAction,
    AgentPlanStep,
    AgentRequest,
    AgentResponse,
    AgentTraceEvent,
    AgentWorkflowState,
)
from app.services.agent_tools.analysis_tools import run_selected_analysis
from app.services.agent_tools.dataset_tools import get_dataset_summary, summarize_dataset_quality
from app.services.agent_tools.method_tools import (
    choose_primary_method,
    get_method_configuration,
    build_selection_for_method,
    get_method_validation_warnings,
    recommend_methods_for_selection,
    validate_outcome_for_methods,
)
from app.services.agent_tools.variable_tools import build_default_variable_selection, recommend_variables
from app.services.data_transformer import detect_transform_intent, preview_dataset_transform
from app.services.store import AGENT_STATES, ANALYSES, PROJECTS

try:
    from app.services.chart_tools.chart_runner import recommend_charts
except Exception:
    def recommend_charts(analysis_id: Optional[str], max_charts: int = 8, dataset_id: Optional[str] = None, context: Optional[Dict[str, Any]] = None):
        return [], ["图表工具暂时不可用：chart_runner 未能导入。请确认 v0.7.0/v0.7.1 图表补丁已完整覆盖。"]

try:
    from app.services.llm_client import deepseek_enabled, generate_text
except Exception:  # Compatibility with older llm_client.py
    def deepseek_enabled() -> bool:
        return False

    def generate_text(system_prompt: str, user_prompt: str, temperature: float = 0.25, max_tokens: int = 900) -> str:
        raise RuntimeError("LLM client is not configured")


STAGE_LABELS = {
    "no_dataset": "等待上传数据",
    "data_review_pending": "等待检查数据",
    "method_pending": "等待选择模型",
    "variable_pending": "等待配置模型变量",
    "ready_to_run": "模型已就绪",
    "analysis_ready": "已有分析结果",
    "report_ready": "可生成/修改报告",
    "data_transform_pending": "等待确认数据转换",
}

ALLOWED_ACTIONS = {
    "no_dataset": ["go_upload"],
    "data_review_pending": ["go_upload", "recommend_methods", "go_models", "preview_transform", "apply_transform"],
    "method_pending": ["recommend_methods", "go_models", "autopilot"],
    "variable_pending": ["recommend_variables", "apply_variable_selection", "go_variables", "go_models"],
    "ready_to_run": ["run_primary_method", "execute", "go_models", "go_variables", "autopilot"],
    "analysis_ready": ["go_results", "go_explain", "go_report", "go_charts", "recommend_charts", "generate_chart", "recommend_variables", "recommend_methods", "preview_transform", "apply_transform"],
    "report_ready": ["go_report", "go_explain"],
    "data_transform_pending": ["preview_transform", "apply_transform", "go_upload"],
}


def _dump_model(obj: Any) -> Dict[str, Any]:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    if isinstance(obj, dict):
        return obj
    return {}


def _project_workspace(req: AgentRequest) -> Dict[str, Any]:
    project_id = str(req.context.project_id or "")
    return PROJECTS.get(project_id) or {} if project_id else {}


def _selected_variables(req: AgentRequest) -> Dict[str, Any]:
    # Project workspace is the source of truth. Context wins when the frontend
    # already carries a newer optimistic update.
    if req.context.selected_variables is not None:
        return req.context.selected_variables or {}
    return (_project_workspace(req).get("selected_variables") or {})


def _selected_model(req: AgentRequest) -> str:
    explicit = req.options.get("model") or req.options.get("model_id")
    if explicit:
        return str(explicit)
    if req.context.selected_model:
        return str(req.context.selected_model)
    return str(_project_workspace(req).get("selected_model") or "")


def _data_review_status(req: AgentRequest) -> str:
    return str(req.context.data_review_status or _project_workspace(req).get("data_review_status") or "pending")


def _dataset_version(req: AgentRequest) -> int:
    try:
        return int(req.context.dataset_version or _project_workspace(req).get("dataset_version") or 0)
    except Exception:
        return 0


def _research_goal(req: AgentRequest) -> str:
    return req.context.research_goal or req.message or ""


def _method_goal(req: AgentRequest) -> str:
    """Combine project research goal with the current user request for model routing.

    The persistent research goal may be broad, e.g. "研究更新类型受什么影响",
    while a later message may explicitly ask to switch to XGBoost/CatBoost/RF.
    Model recommendation must respect the latest message; otherwise the text can
    recommend one model and the action button can execute another stale primary
    model.
    """
    parts = []
    if req.context.research_goal:
        parts.append(str(req.context.research_goal))
    if req.message:
        parts.append(str(req.message))
    return "\n".join(parts).strip()


def _dataset_id(req: AgentRequest) -> Optional[str]:
    return req.context.dataset_id or _project_workspace(req).get("dataset_id")


def _project_key(req: AgentRequest) -> str:
    return str(req.context.project_id or "no_project")


def _mode(req: AgentRequest) -> str:
    raw = (req.mode or req.options.get("mode") or "assisted").strip().lower()
    return "autopilot" if raw in {"auto", "autopilot", "automatic"} else "assisted"


def _has_analysis(req: AgentRequest) -> bool:
    workspace = _project_workspace(req)
    return bool(req.context.analysis_result or req.context.analysis_id or workspace.get("analysis_id"))


def _message_intent(message: str) -> str:
    """Route free-form user messages even after an analysis already exists.

    v0.6.5 used the project stage as the main guardrail. That was safe, but too
    rigid: once a project reached analysis_ready, messages such as "重新清理数据"
    or "帮我判断变量类型" were incorrectly routed to the result/explanation
    branch. This lightweight intent router lets the user jump back to data
    transformation, variable configuration, or method selection without deleting
    the existing analysis result.
    """
    text = (message or "").strip().lower()
    if not text:
        return "general"

    data_keywords = [
        "清理数据", "数据清洗", "处理数据", "重新清理", "重新处理",
        "缺失", "异常值", "极端值", "离群", "outlier",
        "缩尾", "截尾", "winsor", "winsorize",
        "合并类别", "类别合并", "二值化", "二分类", "重编码", "编码",
        "生成新变量", "新建变量", "派生变量",
        "删除变量", "去掉变量", "删除列", "删列", "哑变量", "独热", "one-hot", "dummy",
    ]
    variable_keywords = [
        "变量类型", "判断变量类型", "识别变量类型", "重新判断变量",
        "变量设置", "改下变量", "修改变量", "重新设置变量",
        "变量角色", "因变量", "自变量", "控制变量", "中介变量", "调节变量",
        "推荐变量", "帮我选变量", "选变量",
    ]
    method_keywords = [
        "推荐模型", "选择模型", "模型选择", "换模型", "重新选模型",
        "重新推荐方法", "推荐方法", "分析方法", "跑模型", "运行模型",
        "xgboost", "catboost", "随机森林", "逻辑回归", "logistic", "lasso", "svm", "sgd",
    ]
    report_keywords = ["报告", "草稿", "写作", "论文"]
    explain_keywords = ["解释结果", "ai解释", "解读结果", "解释一下结果"]

    if any(k in text for k in data_keywords):
        return "data_transform"
    if any(k in text for k in variable_keywords):
        return "variable_setup"
    if any(k in text for k in method_keywords):
        return "method_setup"
    if any(k in text for k in explain_keywords):
        return "explain_result"
    if any(k in text for k in report_keywords):
        return "report"
    return "general"


def _detect_stage(req: AgentRequest, dataset: Dict[str, Any], selection: Dict[str, Any]) -> str:
    if not dataset.get("found"):
        return "no_dataset"
    if _has_analysis(req):
        return "analysis_ready"

    selected_model = _selected_model(req)
    if not selected_model:
        if _data_review_status(req) in {"pending", "review_required", "edited"}:
            return "data_review_pending"
        return "method_pending"

    method_state = get_method_configuration(selected_model, selection, _dataset_id(req) or "", _method_goal(req))
    if method_state.get("enabled"):
        return "ready_to_run"
    return "variable_pending"


def _state(stage: str, req: AgentRequest, mode: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
    obj = AgentWorkflowState(
        stage=stage,
        stage_label=STAGE_LABELS.get(stage, stage),
        mode=mode or _mode(req),
        project_id=req.context.project_id,
        dataset_id=_dataset_id(req),
        analysis_id=req.context.analysis_id or _project_workspace(req).get("analysis_id"),
        allowed_actions=ALLOWED_ACTIONS.get(stage, []),
        next_step=kwargs.pop("next_step", ""),
        selected_variables=kwargs.pop("selected_variables", _selected_variables(req) or None),
        selected_model=kwargs.pop("selected_model", _selected_model(req) or None),
        primary_method=kwargs.pop("primary_method", None),
        method_availability=kwargs.pop("method_availability", None),
        missing_roles=kwargs.pop("missing_roles", []),
        data_review_status=kwargs.pop("data_review_status", _data_review_status(req)),
        dataset_version=kwargs.pop("dataset_version", _dataset_version(req)),
        auto_applied_variables=bool(kwargs.pop("auto_applied_variables", False)),
    )
    data = _dump_model(obj)
    data.update(kwargs)
    try:
        AGENT_STATES[_project_key(req)] = data
    except Exception:
        pass
    return data


def _action_go_upload() -> AgentAction:
    return AgentAction(
        id="go_upload",
        label="去数据工作区",
        type="navigate",
        payload={"page": "upload"},
        requires_confirmation=False,
    )


def _action_go_variables(label: str = "去数据工作区配置模型变量") -> AgentAction:
    return AgentAction(
        id="go_variables",
        label=label,
        type="navigate",
        payload={"page": "upload"},
        requires_confirmation=False,
    )


def _action_go_models(label: str = "去模型选择页查看全部方法") -> AgentAction:
    return AgentAction(
        id="go_models",
        label=label,
        type="navigate",
        payload={"page": "models"},
        requires_confirmation=False,
    )


def _action_go_results() -> AgentAction:
    return AgentAction(
        id="go_results",
        label="查看分析结果",
        type="navigate",
        payload={"page": "results"},
        requires_confirmation=False,
    )


def _action_go_charts(label: str = "查看图表分析") -> AgentAction:
    return AgentAction(
        id="go_charts",
        label=label,
        type="navigate",
        payload={"page": "charts"},
        requires_confirmation=False,
    )


def _action_go_explain() -> AgentAction:
    return AgentAction(
        id="go_explain",
        label="生成 / 查看 AI 解释",
        type="navigate",
        payload={"page": "explain"},
        requires_confirmation=False,
    )


def _action_go_report() -> AgentAction:
    return AgentAction(
        id="go_report",
        label="生成报告草稿",
        type="navigate",
        payload={"page": "report"},
        requires_confirmation=False,
    )


def _dataset_short_line(dataset: Dict[str, Any]) -> str:
    if not dataset.get("found"):
        return "当前项目还没有可用数据。"
    return "数据集：{}，{} 行 × {} 列。".format(
        dataset.get("name") or "未命名数据",
        dataset.get("row_count", "—"),
        dataset.get("column_count", "—"),
    )


def _format_warnings(warnings: List[str]) -> str:
    if not warnings:
        return ""
    return "\n\n**注意：**\n" + "\n".join(["- " + w for w in warnings[:4]])


def _selection_line(selection: Dict[str, Any]) -> str:
    dep = selection.get("dependent_variable") or "未设置"
    ivs = "、".join(selection.get("independent_variables") or []) or "未设置"
    ctrls = "、".join(selection.get("control_variables") or []) or "无"
    return "因变量：{}\n自变量：{}\n控制变量：{}".format(dep, ivs, ctrls)


def _goal_variable_warnings(dataset: Dict[str, Any], goal: str, selection: Dict[str, Any]) -> List[str]:
    warnings: List[str] = []
    if not dataset.get("found") or not goal:
        return warnings
    dep = selection.get("dependent_variable") or ""
    columns = [str(c.get("name") or "") for c in dataset.get("columns") or []]

    # A practical consistency check: if the research goal explicitly mentions a
    # result-like column but the current dependent variable is different, warn.
    result_tokens = ["得分", "评分", "结果", "结局", "风险", "满意度", "绩效", "心理健康", "抑郁", "焦虑"]
    mentioned = []
    for name in columns:
        if not name or name == dep:
            continue
        if name in goal or any(tok in name and tok in goal for tok in result_tokens):
            mentioned.append(name)
    if mentioned and dep and dep not in mentioned:
        warnings.append(
            "研究目标里似乎更关注「{}」，但当前因变量是「{}」。如果你的问题是解释/预测前者，建议重新确认因变量。".format(
                mentioned[0], dep
            )
        )
    return warnings


def _fallback_variable_message(dataset: Dict[str, Any], selection: Dict[str, Any], warnings: List[str], model_name: str = "当前模型") -> str:
    return (
        "已根据 **{}** 的 requirement 生成一套可审查的变量配置。\n\n".format(model_name)
        + _dataset_short_line(dataset)
        + "\n\n"
        + _selection_line(selection)
        + "\n\n是否应用这套变量设置？应用后系统会重新校验模型是否可运行。"
        + _format_warnings(warnings)
    )


def _fallback_method_message(dataset: Dict[str, Any], selection: Dict[str, Any], primary_name: str, warnings: List[str]) -> str:
    return (
        "当前已经完成变量设置。下一步只需要确认分析方法。\n\n"
        + _dataset_short_line(dataset)
        + "\n\n"
        + _selection_line(selection)
        + "\n\n"
        + "建议主模型：**{}**。\n\n是否运行这个主模型？如果你想人工比较，也可以先去模型选择页。".format(primary_name or "描述统计")
        + _format_warnings(warnings)
    )


def _fallback_result_message() -> str:
    return (
        "当前项目已经有分析结果。\n\n"
        "如果你想解释当前结果，可以先查看结果或进入 AI 解释页。"
        "如果你对结果不满意，也可以继续告诉我：重新清理数据、重新判断变量类型、修改因变量/自变量，或重新推荐模型。"
    )


def _llm_polish_guarded(system_prompt: str, payload: Dict[str, Any], fallback: str, max_tokens: int = 520) -> str:
    """Use LLM only as a short wording helper. Workflow decisions stay rule-based."""
    if not deepseek_enabled():
        return fallback
    try:
        text = generate_text(
            system_prompt=system_prompt,
            user_prompt=json.dumps(payload, ensure_ascii=False, indent=2),
            temperature=0.15,
            max_tokens=max_tokens,
        )
        text = (text or "").strip()
        if not text:
            return fallback
        # Guardrail: do not allow a free-form long report in the side panel.
        if len(text) > 900:
            return fallback
        if text.count("步骤") >= 3 or text.count("后端工具") >= 4:
            return fallback
        return text
    except Exception:
        return fallback



ACTION_CATALOG: List[Dict[str, Any]] = [
    {
        "action_id": "upload_data",
        "intent": "upload_data",
        "summary": "用户想上传、导入、粘贴或更换数据集。",
        "requires_dataset": False,
    },
    {
        "action_id": "data_summary",
        "intent": "data_summary",
        "summary": "用户想了解当前数据规模、字段、缺失值、异常值或数据质量概览。",
        "requires_dataset": True,
    },
    {
        "action_id": "explain_variable_types",
        "intent": "variable_setup",
        "summary": "用户询问连续型、整数型、分类型、有序型、二分类、计数型等变量类型差异，或让 AI 判断字段类型。",
        "requires_dataset": True,
    },
    {
        "action_id": "update_variable_types",
        "intent": "variable_setup",
        "summary": "用户明确要求把一个或多个字段改成指定变量类型，例如把距离、人口密度、月租金改为连续型/整数型，或纠正页面里的变量类型。",
        "requires_dataset": True,
    },
    {
        "action_id": "recommend_variables",
        "intent": "variable_setup",
        "summary": "用户想让 AI 推荐因变量、自变量、控制变量、中介/调节/分组变量，或重新选择变量。",
        "requires_dataset": True,
    },
    {
        "action_id": "update_variable_roles",
        "intent": "variable_setup",
        "summary": "用户明确指定变量角色，例如某列为因变量、某列为控制变量、某些列不用、其余列为自变量。",
        "requires_dataset": True,
    },
    {
        "action_id": "preview_data_transform",
        "intent": "data_transform",
        "summary": "用户想清洗、重编码、合并类别、二值化、生成新变量、删除极端值、缩尾、填补缺失值、删除列或生成哑变量。",
        "requires_dataset": True,
    },
    {
        "action_id": "recommend_methods",
        "intent": "method_setup",
        "summary": "用户想选择统计/机器学习模型，比较 Logistic、XGBoost、CatBoost、随机森林、回归、分类、多分类等方法。",
        "requires_dataset": True,
    },
    {
        "action_id": "run_analysis",
        "intent": "method_setup",
        "summary": "用户想运行模型、自动完成剩余流程、重新分析或执行当前推荐模型。",
        "requires_dataset": True,
    },
    {
        "action_id": "critique_analysis",
        "intent": "context_qa",
        "summary": "用户想判断当前分析结果是否有意义、是否可靠、是否过拟合、是否能支撑研究问题或论文结论。不会修改数据或重新运行模型。",
        "requires_dataset": True,
    },
    {
        "action_id": "answer_context_question",
        "intent": "context_qa",
        "summary": "用户提出当前项目相关的普通问答，例如询问变量、模型、结果、方法合理性、下一步建议。不会修改数据或重新运行模型。",
        "requires_dataset": True,
    },
    {
        "action_id": "explain_results",
        "intent": "context_qa",
        "summary": "用户想解释、总结、追问或讨论已经生成的分析结果。不会自动改数据或运行新模型。",
        "requires_dataset": True,
    },
    {
        "action_id": "recommend_charts",
        "intent": "charting",
        "summary": "用户想知道当前结果适合画哪些图，或让 AI 推荐可视化图表。",
        "requires_dataset": True,
    },
    {
        "action_id": "generate_chart",
        "intent": "charting",
        "summary": "用户明确要求绘制某一种图，例如相关性矩阵、特征重要性图、混淆矩阵、模型对比图、系数图或 OR 值图。",
        "requires_dataset": True,
    },
    {
        "action_id": "run_model_and_generate_chart",
        "intent": "charting",
        "summary": "用户想要的图依赖某类模型结果，但当前结果不兼容，需要先运行合适模型再生成图表，例如先运行 XGBoost 再生成特征重要性图。",
        "requires_dataset": True,
    },
    {
        "action_id": "generate_report",
        "intent": "report",
        "summary": "用户想生成、修改或查看论文式报告草稿。",
        "requires_dataset": True,
    },
    {
        "action_id": "unsupported",
        "intent": "unsupported",
        "summary": "用户请求不属于 MethodPal 当前可执行的数据分析、数据清洗、变量设置、模型选择、结果解释或报告生成能力。",
        "requires_dataset": False,
    },
]

VALID_ACTION_IDS = set([str(item["action_id"]) for item in ACTION_CATALOG])
ACTION_TO_INTENT = {str(item["action_id"]): str(item["intent"]) for item in ACTION_CATALOG}


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(raw[start : end + 1])
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def _rule_route_user_request(message: str) -> Dict[str, Any]:
    """Small deterministic fallback for when LLM routing is unavailable.

    This is intentionally only a backup. Normal routing should be handled by
    `_llm_route_user_request`, which sees the action catalog and chooses one
    semantic action instead of relying on exact keyword hits.
    """
    intent = _message_intent(message)
    variable_role_markers = ["因变量", "自变量", "控制变量", "中介变量", "调节变量", "分组变量", "啥都不是", "不作为变量", "其他都是自变量", "其余都是自变量"]
    chart_markers = ["画图", "作图", "绘图", "图表", "可视化", "相关性矩阵", "相关矩阵", "热力图", "特征重要性", "变量重要性", "贡献度", "混淆矩阵", "roc", "pr曲线", "模型对比图", "系数图", "or值", "森林图", "残差图"]
    critique_markers = ["有没有意义", "是否有意义", "有意义吗", "靠谱吗", "可靠", "过拟合", "能不能支撑", "是否合理", "结果怎么样", "怎么理解", "怎么看", "评价一下"]
    if any(k.lower() in message.lower() for k in chart_markers):
        action_id = "generate_chart"
        routed_intent = "charting"
    elif any(k in message for k in critique_markers):
        action_id = "critique_analysis"
        routed_intent = "context_qa"
    else:
        mapping = {
            "data_transform": "preview_data_transform",
            "variable_setup": "update_variable_roles" if any(k in message for k in variable_role_markers) else "recommend_variables",
            "method_setup": "recommend_methods",
            "explain_result": "explain_results",
            "report": "generate_report",
        }
        action_id = mapping.get(intent, "unsupported" if intent == "unsupported" else "answer_context_question")
        routed_intent = ACTION_TO_INTENT.get(action_id, intent if intent != "general" else "context_qa")
    return {
        "action_id": action_id,
        "intent": routed_intent,
        "confidence": 0.45,
        "reason": "fallback_rule_router",
    }


def _llm_route_user_request(req: AgentRequest, dataset: Dict[str, Any], selection: Dict[str, Any]) -> Dict[str, Any]:
    """Use the LLM as an action router, not as a free-form executor.

    The model receives the current project state and the list of actions the
    backend can actually perform. It must choose one action id. The backend still
    decides whether to execute, preview, ask for clarification, or refuse.
    """
    message = (req.message or "").strip()
    if not message:
        return {"action_id": "data_summary", "intent": "data_summary", "confidence": 0.4, "reason": "empty_message"}

    if not deepseek_enabled():
        return _rule_route_user_request(message)

    stage_hint = _detect_stage(req, dataset, selection)

    payload = {
        "user_message": message,
        "current_stage_hint": stage_hint,
        "has_dataset": bool(dataset.get("found")),
        "has_analysis_result": bool(_has_analysis(req)),
        "dataset_columns": [c.get("name") for c in (dataset.get("columns") or [])[:80]],
        "selected_variables": selection,
        "available_actions": ACTION_CATALOG,
        "output_schema": {
            "action_id": "one of available_actions.action_id",
            "confidence": "0 to 1",
            "reason": "brief Chinese reason",
            "needs_clarification": "true/false",
        },
    }
    system_prompt = (
        "你是 MethodPal 的意图路由器。你的任务不是回答用户，而是把用户请求映射到一个后端可执行 action。"
        "必须只从 available_actions 中选择一个 action_id。"
        "如果用户请求无法由这些 action 支持，选择 unsupported。"
        "不要因为项目已有分析结果就强行选择查看结果模板；用户可能想回退到数据清洗、变量设置或模型选择，也可能只是追问当前结果是否有意义。"
        "只输出 JSON，不要 Markdown，不要解释长文。"
    )
    try:
        raw = generate_text(
            system_prompt=system_prompt,
            user_prompt=json.dumps(payload, ensure_ascii=False, indent=2),
            temperature=0.0,
            max_tokens=260,
        )
        obj = _extract_json_object(raw)
        action_id = str(obj.get("action_id") or "").strip()
        if action_id not in VALID_ACTION_IDS:
            return _rule_route_user_request(message)
        confidence = obj.get("confidence", 0.7)
        try:
            confidence = float(confidence)
        except Exception:
            confidence = 0.7
        return {
            "action_id": action_id,
            "intent": ACTION_TO_INTENT.get(action_id, "unsupported"),
            "confidence": max(0.0, min(1.0, confidence)),
            "reason": str(obj.get("reason") or "llm_action_router"),
            "needs_clarification": bool(obj.get("needs_clarification", False)),
            "router_backend": "llm",
        }
    except Exception as exc:
        route = _rule_route_user_request(message)
        route["reason"] = "llm_router_failed: {}".format(exc)
        route["router_backend"] = "fallback"
        return route


def _route_user_request(req: AgentRequest, dataset: Dict[str, Any], selection: Dict[str, Any]) -> Dict[str, Any]:
    route = _llm_route_user_request(req, dataset, selection)
    # Hard guard: if there is no dataset, most actions should route to upload.
    action_id = str(route.get("action_id") or "")
    if not dataset.get("found") and action_id not in {"upload_data", "unsupported"}:
        return {
            "action_id": "upload_data",
            "intent": "upload_data",
            "confidence": route.get("confidence", 0.6),
            "reason": "当前没有数据，先进入上传数据阶段。",
            "router_backend": route.get("router_backend", "guard"),
        }
    return route


def _unsupported_response(req: AgentRequest, dataset: Dict[str, Any], route: Dict[str, Any]) -> AgentResponse:
    stage = _detect_stage(req, dataset, _selected_variables(req))
    state = _state(stage, req, next_step="unsupported_request", selected_variables=_selected_variables(req), router=route)
    actions: List[AgentAction] = []
    if dataset.get("found"):
        actions = [_action_go_variables("去数据工作区表头"), _action_go_models("去模型选择页"), _action_go_results()]
    else:
        actions = [_action_go_upload()]
    return AgentResponse(
        ok=False,
        action="unsupported",
        mode=_mode(req),
        message=(
            "这个请求暂时不在 MethodPal 当前可执行能力范围内。\n\n"
            "我现在可以做的是：读取数据摘要、判断变量类型/角色、预览并应用数据清洗转换、推荐模型、运行分析、解释结果和生成报告草稿。\n\n"
            "你可以把需求改写成这些动作之一，例如：\n"
            "- 帮我判断变量类型\n"
            "- 把某列重编码为 0/1\n"
            "- 推荐适合这个因变量的模型\n"
            "- 重新运行模型并解释结果"
        ),
        dataset_summary=dataset,
        warnings=[str(route.get("reason") or "unsupported")],
        workflow_state=state,
        allowed_actions=state.get("allowed_actions", []),
        actions=actions,
        trace=[AgentTraceEvent(step=1, name="llm_action_router", status="unsupported", detail=str(route.get("reason") or "unsupported"))],
    )


def _data_summary_response(req: AgentRequest, dataset: Dict[str, Any]) -> AgentResponse:
    if not dataset.get("found"):
        state = _state("no_dataset", req, next_step="upload_dataset")
        return AgentResponse(
            ok=False,
            action="data_summary",
            mode=_mode(req),
            message="当前项目还没有数据。请先上传 CSV/XLSX 或粘贴表格，我再读取数据摘要。",
            dataset_summary=dataset,
            workflow_state=state,
            allowed_actions=state.get("allowed_actions", []),
            actions=[_action_go_upload()],
        )
    warnings = summarize_dataset_quality(dataset)
    col_names = [str(c.get("name") or "") for c in (dataset.get("columns") or [])[:12]]
    more = " 等" if len(dataset.get("columns") or []) > 12 else ""
    state = _state(_detect_stage(req, dataset, _selected_variables(req)), req, next_step="choose_next_action", selected_variables=_selected_variables(req))
    msg = (
        "我已读取当前数据摘要。\n\n"
        + _dataset_short_line(dataset)
        + "\n\n主要字段：{}{}。\n\n".format("、".join(col_names), more)
        + "下一步你可以让我判断变量类型、清理/重编码数据、推荐模型，或解释已有结果。"
        + _format_warnings(warnings)
    )
    return AgentResponse(
        action="data_summary",
        mode=_mode(req),
        message=msg,
        dataset_summary=dataset,
        warnings=warnings,
        workflow_state=state,
        allowed_actions=state.get("allowed_actions", []),
        actions=[_action_go_variables("去数据工作区表头"), _action_go_models("去模型选择页")],
    )



def _current_analysis_result(req: AgentRequest) -> Dict[str, Any]:
    """Return the current analysis result from request context or persisted store."""
    result = req.context.analysis_result or {}
    if isinstance(result, dict) and result:
        return result
    analysis_id = req.context.analysis_id
    if analysis_id:
        try:
            stored = ANALYSES.get(str(analysis_id), {})
            if isinstance(stored, dict):
                return stored
        except Exception:
            return {}
    return {}


def _compact_for_llm(obj: Any, max_chars: int = 9000) -> Any:
    """Make large analysis payloads safe for prompt use without losing key facts."""
    try:
        raw = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        raw = str(obj)
    if len(raw) <= max_chars:
        try:
            return json.loads(raw)
        except Exception:
            return raw
    raw = raw[:max_chars] + "\n...<已截断，仅用于当前上下文问答>"
    return raw


def _fallback_context_answer(req: AgentRequest, dataset: Dict[str, Any], analysis: Dict[str, Any], warnings: List[str]) -> str:
    if not analysis:
        return (
            "我可以回答当前项目相关问题，但现在没有拿到可用的分析结果内容。\n\n"
            "如果你想判断结果是否有意义，请先运行模型或进入分析结果页确认结果已生成；"
            "如果你想重新调整分析，我也可以帮你改变量、重新推荐模型或清理数据。"
        )
    model_name = analysis.get("model_name") or analysis.get("method_name") or analysis.get("model") or "当前模型"
    return (
        "可以。我会把这个当作**结果质量/方法合理性问题**来回答，而不是继续套用固定 workflow 模板。\n\n"
        "从当前上下文看，已经有分析结果，模型为：**{}**。\n\n"
        "一般判断标准是：\n"
        "- 如果你的目标是**预测**，重点看测试集指标、交叉验证稳定性、混淆矩阵/AUC/F1，以及是否过拟合。\n"
        "- 如果你的目标是**解释影响因素**，机器学习变量重要性只能说明预测贡献，不等于因果效应；最好补充 Logistic/LASSO Logistic 或更简化的解释模型。\n"
        "- 如果指标异常高，例如训练集接近 1 或测试 AUC 很高，需要检查数据泄漏、类别编码、重复样本和变量是否包含目标信息。\n\n"
        "你可以继续问我：这个结果能不能写进论文、哪些指标可疑、需要补哪个稳健性模型。"
    ).format(model_name) + _format_warnings(warnings)

def _context_question_response(req: AgentRequest, dataset: Dict[str, Any], route: Optional[Dict[str, Any]] = None) -> AgentResponse:
    """Answer project-context questions without executing tools or changing state.

    This is the missing 'free Q&A' layer: workflow stages should guard writes and
    executions, but they should not block ordinary discussion about variables,
    methods, or results.
    """
    selection = _selected_variables(req)
    warnings = summarize_dataset_quality(dataset)
    analysis = _current_analysis_result(req)
    stage = _detect_stage(req, dataset, selection)
    action_id = str((route or {}).get("action_id") or "answer_context_question")

    fallback = _fallback_context_answer(req, dataset, analysis, warnings)
    if deepseek_enabled():
        try:
            payload = {
                "user_question": req.message or "",
                "action_id": action_id,
                "dataset_summary": _compact_for_llm(dataset, 3500),
                "selected_variables": selection,
                "analysis_result": _compact_for_llm(analysis, 9500),
                "data_quality_warnings": warnings,
                "router_reason": (route or {}).get("reason"),
                "instructions": [
                    "Answer the user's question directly in Chinese.",
                    "Do not execute or pretend to execute data cleaning, variable changes, model runs, or report generation.",
                    "Only use metrics and facts available in analysis_result. If a metric is absent, say it is not available.",
                    "Distinguish predictive value, explanatory value, and causal claims.",
                    "If evaluating analysis quality, mention overfitting, leakage, sample size, model suitability, and next-step checks when relevant.",
                    "Keep the answer concise but useful; no generic fixed template.",
                ],
            }
            msg = generate_text(
                system_prompt=(
                    "你是 MethodPal 的项目上下文问答助手。你可以基于当前数据、变量设置和真实分析结果回答问题，"
                    "但不能擅自修改数据、变量或运行模型。用户问结果是否有意义时，要区分预测意义、解释意义和因果意义。"
                    "不要使用固定 workflow 模板；直接回答用户问题。"
                ),
                user_prompt=json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                temperature=0.18,
                max_tokens=950,
            ).strip()
            if not msg:
                msg = fallback
        except Exception:
            msg = fallback
    else:
        msg = fallback

    state = _state(stage, req, next_step="context_qa", selected_variables=selection, router=route or {})
    actions: List[AgentAction] = []
    if analysis:
        actions.extend([_action_go_results(), _action_go_explain()])
    if dataset.get("found"):
        actions.extend([
            _action_go_models("重新推荐/选择模型"),
            _action_go_variables("修改变量设置"),
        ])
    else:
        actions.append(_action_go_upload())
    return AgentResponse(
        action=action_id,
        mode=_mode(req),
        message=msg,
        dataset_summary=dataset,
        warnings=warnings,
        workflow_state=state,
        allowed_actions=state.get("allowed_actions", []),
        actions=actions,
        trace=[AgentTraceEvent(step=1, name="context_qa", detail=str((route or {}).get("reason") or action_id))],
    )



VALID_VARIABLE_TYPES = {"ID型", "连续型", "整数型", "计数型", "分类型", "二分类", "有序型", "文本型", "未知"}


def _column_names(dataset: Dict[str, Any]) -> List[str]:
    return [str(c.get("name") or "") for c in (dataset.get("columns") or []) if c.get("name")]


def _current_variable_types(dataset: Dict[str, Any], selection: Dict[str, Any]) -> Dict[str, str]:
    types: Dict[str, str] = {}
    for col in dataset.get("columns") or []:
        name = str(col.get("name") or "")
        if name:
            types[name] = str(col.get("type") or "未知")
    manual = selection.get("variable_types") or {}
    for k, v in manual.items():
        if k:
            types[str(k)] = str(v or "未知")
    return types


def _normalize_variable_type(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    aliases = {
        "id": "ID型", "ID": "ID型", "id型": "ID型", "编号": "ID型",
        "continuous": "连续型", "numeric": "连续型", "float": "连续型", "连续": "连续型", "数值型": "连续型", "数值": "连续型",
        "integer": "整数型", "int": "整数型", "整数": "整数型",
        "count": "计数型", "计数": "计数型", "数量": "计数型",
        "categorical": "分类型", "category": "分类型", "分类": "分类型", "类别": "分类型", "名义型": "分类型",
        "binary": "二分类", "二元": "二分类", "二值": "二分类", "0/1": "二分类",
        "ordinal": "有序型", "有序": "有序型", "等级": "有序型",
        "text": "文本型", "string": "文本型", "文本": "文本型",
        "unknown": "未知", "未知": "未知",
    }
    if text in VALID_VARIABLE_TYPES:
        return text
    low = text.lower()
    return aliases.get(text) or aliases.get(low)


def _infer_requested_type(message: str) -> Optional[str]:
    text = message or ""
    # Prefer explicit variable-type requests from the user. If no explicit type is
    # found, later heuristics may propose best-practice types.
    ordered = [
        ("连续型", ["连续型", "连续", "数值型", "数值变量"]),
        ("整数型", ["整数型", "整数"]),
        ("计数型", ["计数型", "计数", "数量"]),
        ("有序型", ["有序型", "有序", "等级型", "等级变量"]),
        ("二分类", ["二分类", "二值", "0/1", "是否"]),
        ("分类型", ["分类型", "分类变量", "类别型", "名义型"]),
        ("文本型", ["文本型", "文本"]),
        ("ID型", ["ID型", "id型", "编号"]),
    ]
    for vtype, keys in ordered:
        if any(k in text for k in keys):
            return vtype
    return None


def _mentioned_columns(message: str, dataset: Dict[str, Any]) -> List[str]:
    text = message or ""
    cols = _column_names(dataset)
    hits: List[str] = []
    for col in cols:
        if col and col in text:
            hits.append(col)
    if hits:
        return hits

    # Lightweight fuzzy fallback for common urban/built-environment variables.
    fuzzy_rules = [
        (["学校"], ["学校"]),
        (["医院"], ["医院"]),
        (["绿地", "公园"], ["绿地", "公园"]),
        (["人口密度"], ["人口密度"]),
        (["月租金", "租金"], ["月租金", "租金"]),
        (["房价"], ["房价"]),
        (["建筑面积", "面积"], ["建筑面积", "面积"]),
        (["容积率"], ["容积率"]),
    ]
    for msg_keys, col_keys in fuzzy_rules:
        if any(k in text for k in msg_keys):
            for col in cols:
                if col not in hits and any(k in col for k in col_keys):
                    hits.append(col)
    return hits


def _rule_variable_type_patch(req: AgentRequest, dataset: Dict[str, Any], selection: Dict[str, Any]) -> Dict[str, Any]:
    message = req.message or ""
    requested = _infer_requested_type(message)
    cols = _mentioned_columns(message, dataset)
    updates: Dict[str, str] = {}
    reasons: Dict[str, str] = {}

    if requested and cols:
        for c in cols:
            updates[c] = requested
            reasons[c] = "用户在对话中明确要求将该字段设为{}。".format(requested)

    # If the user asks for type judgement rather than a direct assignment, propose
    # best-practice fixes for common continuous quantities that are often auto-
    # detected as integer because values happen to be whole numbers.
    if not updates:
        current = _current_variable_types(dataset, selection)
        for c in _column_names(dataset):
            name = c.lower()
            best: Optional[str] = None
            if any(k in c for k in ["距离", "人口密度", "月租金", "租金", "房价", "建筑面积", "面积", "容积率", "坡度", "高程", "覆盖率", "比例", "占比"]):
                best = "连续型"
            elif any(k in c for k in ["户数", "楼栋数", "层数", "次数", "数量", "个数"]):
                best = "计数型"
            elif any(k in c for k in ["等级", "危房等级", "满意度"]):
                best = "有序型"
            elif any(k in c for k in ["城市", "类型", "结构", "性质", "社区"]):
                best = "分类型"
            if best and current.get(c) != best and (c in message or any(k in message for k in ["变量类型", "判断", "识别", "帮我选", "变量怎么选"])):
                updates[c] = best
                reasons[c] = "按变量语义更适合设为{}，当前/自动识别类型可能不够合适。".format(best)

    return {"updates": updates, "reasons": reasons, "backend": "rule"}


def _llm_variable_type_patch(req: AgentRequest, dataset: Dict[str, Any], selection: Dict[str, Any]) -> Dict[str, Any]:
    fallback = _rule_variable_type_patch(req, dataset, selection)
    if not deepseek_enabled() or not dataset.get("found"):
        return fallback

    payload = {
        "user_message": req.message or "",
        "allowed_variable_types": sorted(list(VALID_VARIABLE_TYPES)),
        "current_variable_types": _current_variable_types(dataset, selection),
        "dataset_columns": [
            {
                "name": c.get("name"),
                "detected_type": c.get("type"),
                "unique_count": c.get("unique_count"),
                "numeric_rate": c.get("numeric_rate"),
                "top_values": c.get("top_values"),
            }
            for c in (dataset.get("columns") or [])[:120]
        ],
        "output_schema": {
            "updates": {"column_name": "one allowed variable type"},
            "reasons": {"column_name": "brief reason in Chinese"},
            "note": "brief Chinese note",
        },
    }
    system_prompt = (
        "你是 MethodPal 的变量类型修改路由器。根据用户消息和真实字段列表，输出需要修改的 variable_types。"
        "只能使用 allowed_variable_types 中的类型，只能引用 dataset_columns 中存在的列名。"
        "如果用户明确要求把某些列改成某个类型，原则上按用户要求输出，但可以在 reason/note 中提示是否更推荐其他类型。"
        "如果用户只是问类型差异或泛泛咨询，输出你认为应该修正的列；如果没有明确可修改列，updates 为空。"
        "特别注意：距离、面积、房价、租金、人口密度、容积率、比例/占比通常更适合连续型；户数、楼栋数、层数通常更适合计数型；等级通常是有序型；城市/结构/类型通常是分类型。"
        "只输出 JSON，不要 Markdown。"
    )
    try:
        raw = generate_text(
            system_prompt=system_prompt,
            user_prompt=json.dumps(payload, ensure_ascii=False, indent=2),
            temperature=0.0,
            max_tokens=700,
        )
        obj = _extract_json_object(raw)
        updates_raw = obj.get("updates") or {}
        reasons_raw = obj.get("reasons") or {}
        if not isinstance(updates_raw, dict):
            return fallback
        valid_cols = set(_column_names(dataset))
        updates: Dict[str, str] = {}
        reasons: Dict[str, str] = {}
        for col, typ in updates_raw.items():
            col = str(col or "").strip()
            norm = _normalize_variable_type(typ)
            if col in valid_cols and norm in VALID_VARIABLE_TYPES:
                updates[col] = norm or "未知"
                reasons[col] = str(reasons_raw.get(col) or "AI 根据用户需求和字段语义建议修改。")
        if not updates and fallback.get("updates"):
            return fallback
        return {
            "updates": updates,
            "reasons": reasons,
            "note": str(obj.get("note") or ""),
            "backend": "llm",
        }
    except Exception:
        return fallback


def _variable_type_update_response(req: AgentRequest, dataset: Dict[str, Any], route: Optional[Dict[str, Any]] = None) -> AgentResponse:
    warnings = summarize_dataset_quality(dataset)
    selection = dict(_selected_variables(req) or {})
    current_types = _current_variable_types(dataset, selection)
    patch = _llm_variable_type_patch(req, dataset, selection)
    updates: Dict[str, str] = dict(patch.get("updates") or {})
    reasons: Dict[str, str] = dict(patch.get("reasons") or {})

    if not updates:
        state = _state("variable_pending", req, next_step="clarify_variable_type_update", selected_variables=selection, router=route or {})
        return AgentResponse(
            ok=False,
            action="update_variable_types",
            mode=_mode(req),
            message=(
                "我可以帮你修改变量类型，但这次没有识别到明确的列名和目标类型。\n\n"
                "你可以这样说：\n"
                "- 把与医院的距离、与重点学校的距离、人口密度、月租金设为连续型\n"
                "- 把涉及户数、建筑楼栋数设为计数型\n"
                "- 把危房等级设为有序型\n\n"
                "说明：距离、人口密度、租金、房价这类即使显示为整数，通常也更适合按**连续型**处理。"
            ),
            dataset_summary=dataset,
            warnings=warnings,
            workflow_state=state,
            allowed_actions=state.get("allowed_actions", []),
            actions=[_action_go_variables("去数据工作区表头手动修改")],
            trace=[AgentTraceEvent(step=1, name="variable_type_router", status="needs_clarification", detail=str((route or {}).get("reason") or "no_updates"))],
        )

    next_types = dict(current_types)
    next_types.update(updates)
    next_selection = dict(selection)
    next_selection["variable_types"] = next_types

    lines = []
    for col, typ in updates.items():
        old = current_types.get(col, "未知")
        reason = reasons.get(col, "")
        lines.append("- {}：{} → {}{}".format(col, old, typ, "（{}）".format(reason) if reason else ""))

    note = str(patch.get("note") or "").strip()
    state = _state("variable_pending", req, next_step="apply_variable_type_updates", selected_variables=next_selection, variable_type_updates=updates, router=route or {})
    msg = (
        "我识别到你想修改变量类型。下面是拟应用的修改：\n\n"
        + "\n".join(lines[:30])
        + ("\n\n" + note if note else "")
        + "\n\n点击下方按钮后，我会把这些类型写入当前项目的变量设置；这不会改动原始数据，只会影响后续模型选择和分析编码。"
    )
    return AgentResponse(
        action="update_variable_types",
        mode=_mode(req),
        message=msg + _format_warnings(warnings),
        dataset_summary=dataset,
        warnings=warnings,
        workflow_state=state,
        allowed_actions=state.get("allowed_actions", []),
        actions=[
            AgentAction(
                id="apply_variable_types",
                label="应用这些变量类型",
                type="apply_variable_types",
                payload={"variable_types": next_types, "updates": updates, "selected_variables": next_selection},
                requires_confirmation=True,
            ),
            _action_go_variables("去数据工作区表头查看"),
        ],
        trace=[AgentTraceEvent(step=1, name="variable_type_router", detail="{}: {}".format(patch.get("backend") or "router", str((route or {}).get("reason") or "")))],
    )

def _variable_type_help_response(req: AgentRequest, dataset: Dict[str, Any], route: Optional[Dict[str, Any]] = None) -> AgentResponse:
    warnings = summarize_dataset_quality(dataset)
    suggestions = recommend_variables(dataset, _research_goal(req)) if dataset.get("found") else []
    selection = build_default_variable_selection(suggestions) if suggestions else _selected_variables(req)
    state = _state("variable_pending", req, next_step="review_variable_types", selected_variables=selection, router=route or {})
    msg = (
        "可以，我先按变量类型帮你判断，不会因为已有分析结果就锁到结果解释。\n\n"
        "**变量类型怎么区分：**\n"
        "- **连续型**：理论上可以取很多数值，常见于距离、面积、房价、租金、人口密度、容积率、坡度等；即使表格里是整数，也更适合按连续型处理。\n"
        "- **整数型 / 计数型**：真实含义是“数量/次数/个数”，例如户数、楼栋数、楼层数、房间数。\n"
        "- **分类型**：没有天然大小顺序的类别，例如城市、结构类型、房屋类型、建筑性质。\n"
        "- **有序型**：类别之间有明确顺序，例如危房等级、满意度等级、低/中/高、Ⅰ/Ⅱ/Ⅲ级。\n"
        "- **二分类**：只有两个有效类别，例如是否更新、是否达标、0/1。\n\n"
        "按你举的例子，**与重点学校的距离、与医院的距离、与公园绿地的距离、人口密度、月租金** 更建议设为 **连续型**，而不是整数型。"
    )
    return AgentResponse(
        action="explain_variable_types",
        mode=_mode(req),
        message=msg + _format_warnings(warnings),
        dataset_summary=dataset,
        variable_suggestions=suggestions,
        warnings=warnings,
        workflow_state=state,
        allowed_actions=state.get("allowed_actions", []),
        actions=[
            AgentAction(
                id="apply_variable_selection",
                label="应用推荐变量设置",
                type="apply_variables",
                payload=selection,
                requires_confirmation=True,
                disabled=not bool(selection.get("dependent_variable")),
                disabled_reason=None if selection.get("dependent_variable") else "没有识别到明确因变量",
            ),
            _action_go_variables("去数据工作区表头查看/修改"),
        ],
        trace=[AgentTraceEvent(step=1, name="llm_action_router", detail=str((route or {}).get("reason") or "explain_variable_types"))],
    )



def _dedupe_keep_order(values: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for v in values or []:
        s = str(v or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _resolve_columns_from_text(fragment: str, dataset: Dict[str, Any]) -> List[str]:
    """Resolve a short Chinese phrase into existing dataset columns.

    This intentionally does not invent columns. It first uses exact substring
    matches, then a conservative token split for phrases like "项目序号和项目名称".
    """
    frag = str(fragment or "").strip()
    cols = _column_names(dataset)
    hits: List[str] = []
    for col in cols:
        if col and col in frag:
            hits.append(col)
    if hits:
        return _dedupe_keep_order(hits)

    parts = [p.strip() for p in re.split(r"[、,，/；;和与及\s]+", frag) if p.strip()]
    for part in parts:
        for col in cols:
            if col in hits:
                continue
            if part and (part == col or part in col or col in part):
                hits.append(col)
    return _dedupe_keep_order(hits)


def _rule_variable_selection_patch(req: AgentRequest, dataset: Dict[str, Any], selection: Dict[str, Any]) -> Dict[str, Any]:
    """Conservative parser for explicit variable-role instructions.

    It handles common project messages such as:
    "是否更新为因变量，城市为控制变量，项目序号和项目名称啥都不是，其他都是自变量".
    """
    message = req.message or ""
    cols = _column_names(dataset)
    dep: Optional[str] = None
    controls: List[str] = []
    independents: List[str] = []
    excluded: List[str] = []

    def before_role(role_pattern: str) -> List[str]:
        hits: List[str] = []
        for m in re.finditer(role_pattern, message):
            left = message[: m.start()]
            # keep only the current clause before the role marker
            frag = re.split(r"[。；;\n]", left)[-1]
            frag = re.split(r"，|,", frag)[-1]
            hits.extend(_resolve_columns_from_text(frag, dataset))
        return _dedupe_keep_order(hits)

    dep_hits = before_role(r"(?:为|是|作为|设为|设置为|当作|用作)\s*因变量")
    if dep_hits:
        dep = dep_hits[-1]

    controls = before_role(r"(?:为|是|作为|设为|设置为|当作|用作)\s*控制变量")
    independents = before_role(r"(?:为|是|作为|设为|设置为|当作|用作)\s*自变量")

    # Exclusion clauses: X和Y啥都不是 / X和Y不要 / X和Y不作为变量
    for pat in [
        r"([^，,。；;\n]+?)(?:啥都不是|都不是|不作为变量|不用|不要|排除|剔除|不是变量)",
        r"(?:排除|剔除|不要|不用)([^，,。；;\n]+)",
    ]:
        for m in re.finditer(pat, message):
            excluded.extend(_resolve_columns_from_text(m.group(1), dataset))
    excluded = _dedupe_keep_order(excluded)

    all_other_independent = any(k in message for k in ["其他都是自变量", "其余都是自变量", "剩下都是自变量", "其他变量都是自变量", "其余变量都是自变量"])
    if all_other_independent:
        blocked = set([x for x in [dep] if x] + controls + excluded)
        independents = [c for c in cols if c not in blocked]

    # If user says A为因变量, B为控制变量 only, keep current independents unless
    # an "other variables" phrase is present.
    result: Dict[str, Any] = {
        "dependent_variable": dep,
        "control_variables": controls if controls else None,
        "independent_variables": independents if independents else None,
        "excluded_variables": excluded,
        "note": "rule_variable_selection_patch",
        "backend": "rule",
    }
    if not dep and not controls and not independents and not excluded:
        result["empty"] = True
    return result


def _llm_variable_selection_patch(req: AgentRequest, dataset: Dict[str, Any], selection: Dict[str, Any]) -> Dict[str, Any]:
    fallback = _rule_variable_selection_patch(req, dataset, selection)
    if not deepseek_enabled() or not dataset.get("found"):
        return fallback

    payload = {
        "user_message": req.message or "",
        "dataset_columns": _column_names(dataset),
        "current_selected_variables": selection,
        "instructions": [
            "Only use exact column names from dataset_columns.",
            "If the user says '其余/其他/剩下都是自变量', set independent_variables to all remaining columns excluding dependent/control/excluded variables.",
            "If the user says a column is '啥都不是/不用/不要/排除/不是变量', place it in excluded_variables and remove it from all roles.",
            "Do not treat variable-role instructions as data transformation.",
        ],
        "output_schema": {
            "dependent_variable": "column name or null",
            "independent_variables": ["column names, or null if user did not specify"],
            "control_variables": ["column names, or null if user did not specify"],
            "mediator_variables": ["column names, optional"],
            "moderator_variables": ["column names, optional"],
            "group_variables": ["column names, optional"],
            "excluded_variables": ["columns that should not be used"],
            "note": "brief Chinese note",
            "confidence": "0 to 1",
        },
    }
    system_prompt = (
        "你是 MethodPal 的变量角色解析器。你的任务是把用户自然语言转换成变量选择设置，不要回答长文。"
        "你只能从 dataset_columns 中选择真实存在的列。"
        "如果用户要求某列为因变量/自变量/控制变量，按要求输出。"
        "如果用户说“其他/其余/剩下都是自变量”，把除因变量、控制变量、excluded_variables 以外的所有列放入 independent_variables。"
        "如果用户说某些列“啥都不是/不用/不要/排除”，放入 excluded_variables。"
        "只输出 JSON，不要 Markdown。"
    )
    try:
        raw = generate_text(
            system_prompt=system_prompt,
            user_prompt=json.dumps(payload, ensure_ascii=False, indent=2),
            temperature=0.0,
            max_tokens=900,
        )
        obj = _extract_json_object(raw)
        if not obj:
            return fallback
        cols = set(_column_names(dataset))
        def clean_list(v: Any) -> List[str]:
            if v is None:
                return []
            if isinstance(v, str):
                v = [v]
            out: List[str] = []
            for x in v or []:
                s = str(x or "").strip()
                if s in cols:
                    out.append(s)
            return _dedupe_keep_order(out)
        dep = str(obj.get("dependent_variable") or "").strip()
        if dep not in cols:
            dep = fallback.get("dependent_variable") or ""
        patch = {
            "dependent_variable": dep or None,
            "independent_variables": clean_list(obj.get("independent_variables")) or fallback.get("independent_variables"),
            "control_variables": clean_list(obj.get("control_variables")) or fallback.get("control_variables"),
            "mediator_variables": clean_list(obj.get("mediator_variables")),
            "moderator_variables": clean_list(obj.get("moderator_variables")),
            "group_variables": clean_list(obj.get("group_variables")),
            "excluded_variables": clean_list(obj.get("excluded_variables")) or fallback.get("excluded_variables") or [],
            "note": str(obj.get("note") or fallback.get("note") or "").strip(),
            "confidence": obj.get("confidence", 0.75),
            "backend": "llm",
        }
        if not patch.get("dependent_variable") and not patch.get("independent_variables") and not patch.get("control_variables") and not patch.get("excluded_variables"):
            return fallback
        return patch
    except Exception:
        return fallback


def _normalize_variable_selection_patch(dataset: Dict[str, Any], current: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    cols = _column_names(dataset)
    colset = set(cols)
    next_selection = dict(current or {})

    dep = patch.get("dependent_variable")
    if dep in colset:
        next_selection["dependent_variable"] = dep
    else:
        dep = next_selection.get("dependent_variable")

    excluded = set([x for x in (patch.get("excluded_variables") or []) if x in colset])
    if dep:
        excluded.discard(dep)

    def clean_role(values: Any) -> List[str]:
        if values is None:
            return []
        out: List[str] = []
        for v in values or []:
            s = str(v or "").strip()
            if s in colset and s != dep and s not in excluded:
                out.append(s)
        return _dedupe_keep_order(out)

    if patch.get("independent_variables") is not None:
        next_selection["independent_variables"] = clean_role(patch.get("independent_variables"))
    else:
        next_selection["independent_variables"] = clean_role(next_selection.get("independent_variables") or [])

    if patch.get("control_variables") is not None:
        next_selection["control_variables"] = clean_role(patch.get("control_variables"))
    else:
        next_selection["control_variables"] = clean_role(next_selection.get("control_variables") or [])

    for key in ["mediator_variables", "moderator_variables", "group_variables"]:
        if patch.get(key) is not None:
            next_selection[key] = clean_role(patch.get(key))
        else:
            next_selection[key] = clean_role(next_selection.get(key) or [])

    # ensure no overlap between independent and control; control wins
    controls = set(next_selection.get("control_variables") or [])
    next_selection["independent_variables"] = [x for x in next_selection.get("independent_variables") or [] if x not in controls]
    next_selection["excluded_variables"] = sorted(excluded)
    return next_selection


def _variable_selection_update_response(req: AgentRequest, dataset: Dict[str, Any], route: Optional[Dict[str, Any]] = None) -> AgentResponse:
    selection = _selected_variables(req)
    warnings = summarize_dataset_quality(dataset)
    patch = _llm_variable_selection_patch(req, dataset, selection)
    if patch.get("empty"):
        return recommend_variable_setup(req)
    next_selection = _normalize_variable_selection_patch(dataset, selection, patch)
    dep = next_selection.get("dependent_variable") or "未设置"
    ivs = next_selection.get("independent_variables") or []
    ctrls = next_selection.get("control_variables") or []
    excluded = next_selection.get("excluded_variables") or []
    note = str(patch.get("note") or "").strip()

    state = _state("variable_pending", req, next_step="apply_variable_selection", selected_variables=next_selection, router=route or {})
    msg = (
        "我理解你是想**重新指定变量角色**，不是做数据转换。\n\n"
        "拟应用的变量设置：\n"
        "- 因变量：{}\n"
        "- 控制变量：{}\n"
        "- 自变量：{} 个{}\n"
        "- 不参与分析：{}\n"
    ).format(
        dep,
        "、".join(ctrls) if ctrls else "无",
        len(ivs),
        "（{}）".format("、".join(ivs[:12]) + (" 等" if len(ivs) > 12 else "")) if ivs else "",
        "、".join(excluded) if excluded else "无",
    )
    if note:
        msg += "\n" + note + "\n"
    msg += "\n点击下方按钮后，我会把这套变量角色写入当前项目；这不会改动原始数据。"
    return AgentResponse(
        action="update_variable_roles",
        mode=_mode(req),
        message=msg + _format_warnings(warnings),
        dataset_summary=dataset,
        warnings=warnings,
        workflow_state=state,
        allowed_actions=state.get("allowed_actions", []),
        actions=[
            AgentAction(
                id="apply_variable_selection",
                label="应用这套变量设置",
                type="apply_variables",
                payload=next_selection,
                requires_confirmation=True,
                disabled=False,
                disabled_reason=None,
            ),
            _action_go_variables("去数据工作区表头查看"),
            _action_go_models("继续推荐模型"),
        ],
        trace=[AgentTraceEvent(step=1, name="variable_selection_router", detail="{}: {}".format(patch.get("backend") or "router", str((route or {}).get("reason") or "")))],
    )

def recommend_variable_setup(req: AgentRequest) -> AgentResponse:
    dataset_id = _dataset_id(req) or ""
    dataset = get_dataset_summary(dataset_id)
    warnings = summarize_dataset_quality(dataset)

    if not dataset.get("found"):
        state = _state("no_dataset", req, next_step="upload_dataset")
        return AgentResponse(
            ok=False,
            action="recommend_variables",
            mode=_mode(req),
            message="当前项目还没有数据。请先在数据工作区上传 CSV/XLSX。",
            dataset_summary=dataset,
            warnings=warnings,
            workflow_state=state,
            allowed_actions=state.get("allowed_actions", []),
            actions=[_action_go_upload()],
        )

    selected_model = _selected_model(req)
    if not selected_model:
        state = _state("method_pending", req, next_step="select_model_before_variables", selected_variables=_selected_variables(req))
        return AgentResponse(
            ok=False,
            action="recommend_variables",
            mode=_mode(req),
            message="现在采用模型优先流程：请先选择分析模型，我再只推荐该模型 requirement 中需要的变量角色。",
            dataset_summary=dataset,
            warnings=warnings,
            workflow_state=state,
            allowed_actions=state.get("allowed_actions", []),
            actions=[
                AgentAction(id="recommend_methods", label="先推荐模型", type="agent", payload={"action": "recommend_methods"}),
                _action_go_models(),
            ],
        )

    current = _selected_variables(req)
    proposed, suggestions = build_selection_for_method(selected_model, current, dataset_id, _research_goal(req))
    methods = recommend_methods_for_selection(proposed, dataset_id, _method_goal(req))
    method_item = next((m for m in methods if m.method_id == selected_model), None)
    model_name = method_item.name if method_item else selected_model
    method_state = get_method_configuration(selected_model, proposed, dataset_id, _method_goal(req))
    warnings = warnings + _goal_variable_warnings(dataset, _research_goal(req), proposed) + list(method_state.get("validation_messages") or [])

    fallback = _fallback_variable_message(dataset, proposed, warnings, model_name)
    msg = _llm_polish_guarded(
        system_prompt=(
            "你是 MethodPal 的模型变量配置助手。模型已经选定。只解释该模型 requirement 所需的变量角色并等待用户确认。"
            "不要重新推荐其他模型，不要说已经执行分析，不要编造字段。用中文，控制在 180 字以内。"
        ),
        payload={
            "stage": "variable_pending",
            "selected_model": selected_model,
            "method_requirement": method_state.get("requirement") or {},
            "research_goal": _research_goal(req),
            "dataset_summary": dataset,
            "proposed_selection": proposed,
            "warnings": warnings,
        },
        fallback=fallback,
    )

    stage = "ready_to_run" if method_state.get("enabled") else "variable_pending"
    state = _state(
        stage,
        req,
        next_step="apply_model_variable_selection",
        selected_variables=proposed,
        selected_model=selected_model,
        primary_method=selected_model,
        method_availability=method_state.get("availability"),
        missing_roles=method_state.get("missing_roles") or [],
    )
    actions: List[AgentAction] = [
        AgentAction(
            id="apply_variable_selection",
            label="应用这套模型变量配置",
            type="apply_variables",
            payload=proposed,
            requires_confirmation=True,
            disabled=False,
        ),
        _action_go_variables("去数据工作区检查变量"),
    ]
    if method_state.get("enabled"):
        actions.append(AgentAction(
            id="run_selected_model",
            label="运行{}".format(model_name),
            type="run_analysis",
            payload={"model": selected_model, "selected_variables": proposed},
            requires_confirmation=True,
        ))

    return AgentResponse(
        action="recommend_variables",
        mode=_mode(req),
        message=msg,
        dataset_summary=dataset,
        variable_suggestions=suggestions,
        method_suggestions=methods,
        warnings=warnings,
        workflow_state=state,
        allowed_actions=state.get("allowed_actions", []),
        actions=actions,
    )


def recommend_method_setup(req: AgentRequest) -> AgentResponse:
    dataset = get_dataset_summary(_dataset_id(req))
    warnings = summarize_dataset_quality(dataset)

    if not dataset.get("found"):
        state = _state("no_dataset", req, next_step="upload_dataset")
        return AgentResponse(
            ok=False,
            action="recommend_methods",
            mode=_mode(req),
            message="当前没有数据，无法推荐模型。请先上传数据。",
            dataset_summary=dataset,
            warnings=warnings,
            workflow_state=state,
            allowed_actions=state.get("allowed_actions", []),
            actions=[_action_go_upload()],
        )

    selection = _selected_variables(req)
    methods = recommend_methods_for_selection(selection, _dataset_id(req) or "", _method_goal(req))
    selected_model = _selected_model(req)
    primary = selected_model or choose_primary_method(methods, _method_goal(req))
    primary_item = next((m for m in methods if m.method_id == primary), None)
    primary_name = primary_item.name if primary_item else (primary or "描述统计")
    method_state = get_method_configuration(primary, selection, _dataset_id(req) or "", _method_goal(req)) if primary else {}
    ready = bool(method_state.get("enabled"))
    availability = str(method_state.get("availability") or (getattr(primary_item, "availability", "") if primary_item else ""))
    missing_roles = list(method_state.get("missing_roles") or [])

    if not selected_model:
        stage = "method_pending"
        next_step = "select_recommended_model"
        message = (
            "我已经基于数据字段类型和研究目标推荐主模型：**{}**。"
            "现在可以先确认模型；确认后，数据工作区只会显示该模型真正需要的变量角色。"
        ).format(primary_name)
    elif ready:
        stage = "ready_to_run"
        next_step = "run_selected_model"
        message = "当前已选模型 **{}**，变量 requirement 校验通过，可以运行分析。".format(primary_name)
    else:
        stage = "variable_pending"
        next_step = "configure_model_variables"
        reason = str(method_state.get("reason") or "仍需补充模型所需的变量角色。")
        message = "当前已选模型 **{}**。{} 请回到数据工作区完成变量配置。".format(primary_name, reason)

    state = _state(
        stage,
        req,
        next_step=next_step,
        selected_variables=selection,
        selected_model=primary or None,
        primary_method=primary or None,
        method_availability=availability or None,
        missing_roles=missing_roles,
    )

    actions: List[AgentAction] = []
    if primary and not selected_model:
        actions.append(AgentAction(
            id="select_recommended_model",
            label="选择{}".format(primary_name),
            type="select_model",
            payload={"model": primary, "model_options": method_state.get("default_options") or {}, "availability": availability, "enabled": ready},
            requires_confirmation=True,
            disabled=not bool(getattr(primary_item, "selectable", True)),
            disabled_reason=None if getattr(primary_item, "selectable", True) else (getattr(primary_item, "reason", None) or "当前数据不适用"),
        ))
    if selected_model and not ready:
        actions.append(_action_go_variables())
    if selected_model and ready:
        actions.append(AgentAction(
            id="run_primary_method",
            label="运行{}".format(primary_name),
            type="run_analysis",
            payload={"model": primary, "selected_variables": selection},
            requires_confirmation=True,
        ))
    actions.append(_action_go_models())

    return AgentResponse(
        action="recommend_methods",
        mode=_mode(req),
        message=message + _format_warnings(warnings),
        dataset_summary=dataset,
        method_suggestions=methods,
        warnings=warnings,
        workflow_state=state,
        allowed_actions=state.get("allowed_actions", []),
        actions=actions,
    )


def _transform_clarify_response(req: AgentRequest, dataset: Dict[str, Any]) -> AgentResponse:
    warnings = summarize_dataset_quality(dataset)
    state = _state(
        "data_transform_pending",
        req,
        next_step="describe_data_transform_rule",
        selected_variables=_selected_variables(req),
    )
    message = (
        "我理解你想**重新清理或处理数据**，但目前还缺少可执行规则，所以我不会直接改数据。\n\n"
        "你可以这样描述：\n"
        "- 把更新类型中除 0 以外的类别合并为 1，生成新变量 是否更新\n"
        "- 去掉建筑面积上下 1% 的极端值\n"
        "- 对房价、租金做上下 1% 缩尾处理\n"
        "- 用中位数填补建筑年龄的缺失值\n"
        "- 为城市生成哑变量\n\n"
        "你给出具体规则后，我会先生成预览，再等你确认应用。"
    )
    return AgentResponse(
        action="plan",
        mode=_mode(req),
        message=message + _format_warnings(warnings),
        dataset_summary=dataset,
        warnings=warnings,
        workflow_state=state,
        allowed_actions=state.get("allowed_actions", []),
        actions=[_action_go_upload(), _action_go_variables()],
    )


def _transform_plan_response(req: AgentRequest, dataset: Dict[str, Any], operation: Dict[str, Any]) -> AgentResponse:
    warnings = summarize_dataset_quality(dataset)
    try:
        preview = preview_dataset_transform(_dataset_id(req) or "", operation, _selected_variables(req))
        summary = preview.get("summary") or {}
        preview_line = "预览：{} 行 → {} 行，{} 列 → {} 列。".format(
            summary.get("before_rows", dataset.get("row_count", "—")),
            summary.get("after_rows", dataset.get("row_count", "—")),
            summary.get("before_columns", dataset.get("column_count", "—")),
            summary.get("after_columns", dataset.get("column_count", "—")),
        )
        if summary.get("counts"):
            counts = summary.get("counts") or {}
            preview_line += " 转换后 0 类 {} 条，1 类 {} 条，缺失 {} 条。".format(counts.get("0", 0), counts.get("1", 0), counts.get("missing", 0))
        warnings = warnings + list(preview.get("warnings") or [])
    except Exception as exc:
        preview_line = "我识别到了数据转换需求，但预览失败：{}。".format(exc)
        warnings = warnings + [str(exc)]

    state = _state(
        "data_transform_pending",
        req,
        next_step="preview_or_apply_data_transform",
        selected_variables=_selected_variables(req),
        transform_operation=operation,
    )
    message = (
        "我识别到你想做一次**数据转换**，不会直接改原始数据。\n\n"
        "拟执行操作：{}\n\n"
        "{}\n\n"
        "建议先点「预览转换」，确认无误后再应用。应用后会生成一个新的数据版本，并重新生成数据摘要。"
    ).format(operation.get("description") or operation.get("op") or "数据转换", preview_line)
    return AgentResponse(
        action="plan",
        mode=_mode(req),
        message=message + _format_warnings(warnings),
        dataset_summary=dataset,
        warnings=warnings,
        workflow_state=state,
        allowed_actions=state.get("allowed_actions", []),
        actions=[
            AgentAction(
                id="preview_data_transform",
                label="预览转换",
                type="preview_transform",
                payload={"operation": operation},
                requires_confirmation=False,
            ),
            AgentAction(
                id="apply_data_transform",
                label="应用并重新诊断",
                type="apply_transform",
                payload={"operation": operation},
                requires_confirmation=True,
            ),
            _action_go_upload(),
        ],
        trace=[AgentTraceEvent(step=1, name="detect_data_transform", detail=operation.get("description") or str(operation))],
    )


def _chart_type_from_message(message: str) -> Optional[str]:
    text = (message or "").strip().lower()
    if not text:
        return None
    if any(k in text for k in ["相关性矩阵", "相关矩阵", "correlation", "相关热力", "热力图"]):
        return "correlation_heatmap"
    if any(k in text for k in ["特征重要性", "变量重要性", "贡献度", "feature importance", "importance"]):
        return "feature_importance_bar"
    if any(k in text for k in ["混淆矩阵", "confusion"]):
        return "confusion_matrix_heatmap"
    if any(k in text for k in ["模型对比图", "模型比较图", "模型对比", "model comparison"]):
        return "model_comparison_bar"
    if any(k in text for k in ["or值", "or 值", "优势比", "odds ratio", "森林图"]):
        return "odds_ratio_bar"
    if any(k in text for k in ["系数图", "回归系数", "coefficient"]):
        return "coefficient_bar"
    if any(k in text for k in ["预测值", "真实值", "predicted", "actual", "拟合"]):
        return "prediction_scatter"
    if any(k in text for k in ["均值图", "描述统计图", "平均值"]):
        return "descriptive_mean_bar"
    return None


def _analysis_supports_chart(analysis: Dict[str, Any], chart_type: str) -> bool:
    if not analysis:
        return False
    if chart_type == "feature_importance_bar":
        return bool(analysis.get("feature_importance"))
    if chart_type == "confusion_matrix_heatmap":
        return bool((analysis.get("prediction_metrics") or {}).get("confusion_matrix"))
    if chart_type == "model_comparison_bar":
        return bool(analysis.get("model_comparison"))
    if chart_type in {"coefficient_bar", "odds_ratio_bar"}:
        rows = analysis.get("regression_table") or []
        if chart_type == "odds_ratio_bar":
            return any(isinstance(r, dict) and r.get("odds_ratio") is not None for r in rows)
        return bool(rows)
    if chart_type == "prediction_scatter":
        return bool(analysis.get("prediction_table"))
    if chart_type == "correlation_heatmap":
        return bool(analysis.get("correlation_matrix"))
    if chart_type == "descriptive_mean_bar":
        return bool(analysis.get("descriptive_statistics"))
    return False


def _dep_type_for_selection(dataset: Dict[str, Any], selection: Dict[str, Any]) -> str:
    dep = selection.get("dependent_variable") or ""
    manual = selection.get("variable_types") or {}
    if dep in manual:
        return str(manual.get(dep) or "")
    for col in dataset.get("columns") or []:
        if col.get("name") == dep:
            return str(col.get("type") or "")
    return ""


def _model_for_chart_request(req: AgentRequest, dataset: Dict[str, Any], selection: Dict[str, Any], chart_type: str) -> str:
    text = _method_goal(req).lower()
    dep_type = _dep_type_for_selection(dataset, selection)
    is_reg = dep_type in {"连续型", "整数型", "计数型"} and chart_type not in {"confusion_matrix_heatmap"}
    if "cat" in text or "catboost" in text:
        return "catboost_regression" if is_reg else "catboost_classification"
    if "随机森林" in text or "random forest" in text or "rf" in text:
        return "random_forest_regression" if is_reg else "random_forest_classification"
    if "sgd" in text:
        return "sgd_regression" if is_reg else "sgd_classification"
    if chart_type in {"coefficient_bar", "odds_ratio_bar"}:
        return "linear_regression" if is_reg else "logistic_regression"
    if chart_type == "model_comparison_bar":
        return "model_comparison"
    if chart_type == "correlation_heatmap":
        return "correlation_analysis"
    return "xgboost_regression" if is_reg else "xgboost_classification"


def _chart_label(chart_type: str) -> str:
    mapping = {
        "correlation_heatmap": "相关矩阵热图",
        "feature_importance_bar": "特征重要性图",
        "confusion_matrix_heatmap": "混淆矩阵热图",
        "model_comparison_bar": "模型对比图",
        "coefficient_bar": "系数方向图",
        "odds_ratio_bar": "OR 值图",
        "prediction_scatter": "预测值 vs 真实值",
        "descriptive_mean_bar": "描述统计均值图",
    }
    return mapping.get(chart_type, chart_type or "图表")


def _action_generate_chart(chart_type: str, req: AgentRequest, label: Optional[str] = None, use_dataset: bool = False) -> AgentAction:
    payload: Dict[str, Any] = {"chart_type": chart_type}
    if use_dataset:
        payload["dataset_id"] = _dataset_id(req)
    else:
        payload["analysis_id"] = req.context.analysis_id
    return AgentAction(
        id="generate_chart_{}".format(chart_type),
        label=label or "生成{}".format(_chart_label(chart_type)),
        type="generate_chart",
        payload=payload,
        requires_confirmation=False,
    )


def _action_run_model_and_chart(model_id: str, chart_type: str, selection: Dict[str, Any]) -> AgentAction:
    return AgentAction(
        id="run_{}_then_chart_{}".format(model_id, chart_type),
        label="运行{}并生成{}".format(model_id, _chart_label(chart_type)),
        type="run_model_and_generate_chart",
        payload={"model": model_id, "chart_type": chart_type, "selected_variables": selection},
        requires_confirmation=True,
    )


def _chart_request_response(req: AgentRequest, dataset: Dict[str, Any], route: Optional[Dict[str, Any]] = None) -> AgentResponse:
    selection = _selected_variables(req)
    analysis = _current_analysis_result(req)
    analysis_id = str(analysis.get("analysis_id") or req.context.analysis_id or "")
    chart_type = _chart_type_from_message(req.message or "")
    warnings = summarize_dataset_quality(dataset)

    # No concrete chart type: recommend all charts available from current result + data.
    if not chart_type:
        recs, rec_warnings = recommend_charts(analysis_id or None, max_charts=6, dataset_id=_dataset_id(req), context={"selected_variables": selection})
        warnings = warnings + list(rec_warnings or [])
        actions: List[AgentAction] = []
        for rec in recs[:5]:
            use_dataset = rec.chart_type == "correlation_heatmap" and not _analysis_supports_chart(analysis, rec.chart_type)
            actions.append(_action_generate_chart(rec.chart_type, req, label="生成{}".format(rec.title), use_dataset=use_dataset))
        actions.append(_action_go_charts())
        state = _state(_detect_stage(req, dataset, selection), req, next_step="recommend_charts", selected_variables=selection, router=route or {})
        msg = "我可以基于当前数据和分析结果推荐图表。\n\n" + ("\n".join(["- **{}**：{}".format(r.title, r.description) for r in recs[:5]]) if recs else "当前结果暂时缺少可直接作图的结构化字段。")
        return AgentResponse(
            action="recommend_charts",
            mode=_mode(req),
            message=msg + _format_warnings(warnings),
            dataset_summary=dataset,
            warnings=warnings,
            workflow_state=state,
            allowed_actions=state.get("allowed_actions", []),
            actions=actions,
            trace=[AgentTraceEvent(step=1, name="chart_router", detail=str((route or {}).get("reason") or "recommend_charts"))],
        )

    # Correlation heatmap can be generated directly from the dataset even if the current model is XGBoost/CatBoost/etc.
    if chart_type == "correlation_heatmap" and _dataset_id(req):
        state = _state(_detect_stage(req, dataset, selection), req, next_step="generate_dataset_chart", selected_variables=selection, router=route or {})
        return AgentResponse(
            action="generate_chart",
            mode=_mode(req),
            message="可以。相关矩阵图不依赖当前模型，我会直接基于当前数据集中的数值变量生成。",
            dataset_summary=dataset,
            warnings=warnings,
            workflow_state=state,
            allowed_actions=state.get("allowed_actions", []),
            actions=[_action_generate_chart(chart_type, req, label="生成相关矩阵图", use_dataset=True), _action_go_charts()],
            trace=[AgentTraceEvent(step=1, name="chart_router", detail="dataset_correlation_heatmap")],
        )

    # If current analysis already has the needed structure, generate directly.
    if analysis_id and _analysis_supports_chart(analysis, chart_type):
        state = _state("analysis_ready", req, next_step="generate_chart", selected_variables=selection, router=route or {})
        return AgentResponse(
            action="generate_chart",
            mode=_mode(req),
            message="可以。当前分析结果已经包含生成「{}」所需的结构化字段。".format(_chart_label(chart_type)),
            dataset_summary=dataset,
            warnings=warnings,
            workflow_state=state,
            allowed_actions=state.get("allowed_actions", []),
            actions=[_action_generate_chart(chart_type, req, label="生成{}".format(_chart_label(chart_type)), use_dataset=False), _action_go_charts()],
            trace=[AgentTraceEvent(step=1, name="chart_router", detail="analysis_chart_supported")],
        )

    # Otherwise recommend the model needed for that chart and make the payload explicit.
    if not selection.get("dependent_variable") and chart_type not in {"correlation_heatmap", "descriptive_mean_bar"}:
        state = _state("variable_pending", req, next_step="confirm_variables_before_chart", selected_variables=selection, router=route or {})
        return AgentResponse(
            action="recommend_variables",
            mode=_mode(req),
            message="这个图需要先有明确的因变量和自变量。请先在数据工作区表头确认变量角色，然后我再帮你运行模型并生成「{}」。".format(_chart_label(chart_type)),
            dataset_summary=dataset,
            warnings=warnings,
            workflow_state=state,
            allowed_actions=state.get("allowed_actions", []),
            actions=[_action_go_variables(), AgentAction(id="recommend_variables_for_chart", label="推荐变量设置", type="agent", payload={"action": "recommend_variables"})],
        )

    model_id = _model_for_chart_request(req, dataset, selection, chart_type)
    state = _state("method_pending", req, next_step="run_model_then_chart", selected_variables=selection, primary_method=model_id, router=route or {})
    explain = "当前分析结果暂不包含生成「{}」所需的字段。".format(_chart_label(chart_type))
    if chart_type == "feature_importance_bar":
        explain += " 如果你关注特征贡献度，建议先运行 XGBoost、CatBoost 或随机森林这类树模型，再生成特征重要性图。"
    elif chart_type == "confusion_matrix_heatmap":
        explain += " 混淆矩阵需要分类模型的预测结果。"
    elif chart_type in {"coefficient_bar", "odds_ratio_bar"}:
        explain += " 系数/OR 图需要回归或 Logistic 结果。"
    return AgentResponse(
        action="run_model_and_generate_chart",
        mode=_mode(req),
        message=explain + "\n\n我可以先运行合适模型，再自动生成这张图。",
        dataset_summary=dataset,
        warnings=warnings,
        workflow_state=state,
        allowed_actions=state.get("allowed_actions", []),
        actions=[_action_run_model_and_chart(model_id, chart_type, selection), _action_go_models(), _action_go_charts()],
        trace=[AgentTraceEvent(step=1, name="chart_router", detail="need_model:{}".format(model_id))],
    )



def _is_capability_question(message: str) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return False
    # 这些是纯问答/帮助类请求，不应该进入 unsupported 或执行型 action。
    markers = [
        "你能干什么", "你能做什么", "你会干什么", "你会做什么",
        "能干什么", "能做什么", "可以做什么", "有什么功能",
        "你的功能", "能力范围", "怎么用", "使用说明", "帮助",
        "methodpal能干什么", "methodpal 可以做什么", "what can you do", "help"
    ]
    if any(m in text for m in markers):
        # 避免把“你能不能帮我画图/跑模型”误判成帮助问答。
        execution_words = ["帮我", "运行", "生成", "画", "绘制", "推荐模型", "清洗", "重编码", "删除", "应用"]
        if any(w in text for w in execution_words) and not any(h in text for h in ["功能", "能力", "怎么用", "使用说明", "help"]):
            return False
        return True
    return False


def _capability_help_response(req: AgentRequest, dataset: Dict[str, Any], selection: Dict[str, Any]) -> AgentResponse:
    stage = _detect_stage(req, dataset, selection)
    state = _state(stage, req, next_step="answer_capability_question", selected_variables=selection, router={"action_id": "capability_help", "intent": "context_qa"})
    has_dataset = bool(dataset.get("found"))
    has_analysis = bool(_has_analysis(req))
    message = (
        "我可以帮你完成 MethodPal 里的数据分析流程，但我会区分两类请求：\n\n"
        "**问答类**：解释方法、判断结果是否有意义、说明下一步该做什么，不会改数据或运行模型。\n"
        "**执行类**：上传/处理数据、设置变量、运行模型、生成图表或报告，需要通过按钮确认或直接进入对应页面。\n\n"
        "目前我能做的主要包括：\n"
        "- 读取数据摘要：样本量、变量数、缺失值、异常值和字段类型。\n"
        "- 判断和修改变量类型/角色：因变量、自变量、控制变量、中介/调节变量等。\n"
        "- 预览数据处理：重编码、二值化、合并类别、填补缺失、删除行列等。\n"
        "- 推荐并运行模型：回归、Logistic、XGBoost、CatBoost、随机森林、模型对比等。\n"
        "- 解读分析结果：区分预测贡献、统计显著性和因果解释边界。\n"
        "- 生成图表：相关矩阵、特征重要性、混淆矩阵、预测值 vs 真实值等。\n"
        "- 生成报告草稿：整合数据摘要、变量设置、模型指标、图表和 AI 解释。"
    )
    if not has_dataset:
        message += "\n\n当前项目还没有数据，第一步建议先上传 CSV/XLSX。"
    elif not selection.get("dependent_variable"):
        message += "\n\n当前项目已有数据，下一步建议先确认因变量、自变量和控制变量。"
    elif not has_analysis:
        message += "\n\n当前项目已设置变量，下一步可以推荐模型或运行分析。"
    else:
        message += "\n\n当前项目已有分析结果，你可以让我解释结果、推荐图表，或者生成报告。"
    actions: List[AgentAction] = []
    if not has_dataset:
        actions = [_action_go_upload()]
    elif not selection.get("dependent_variable"):
        actions = [_action_go_variables(), _action_go_upload()]
    elif not has_analysis:
        actions = [_action_go_models(), _action_go_variables()]
    else:
        actions = [_action_go_results(), _action_go_charts(), _action_go_report()]
    return AgentResponse(
        ok=True,
        action="capability_help",
        mode=_mode(req),
        message=message,
        dataset_summary=dataset,
        workflow_state=state,
        allowed_actions=state.get("allowed_actions", []),
        actions=actions,
        trace=[AgentTraceEvent(step=1, name="qa_router", status="answered", detail="capability_help")],
    )


QA_INTENTS = {
    "system_capability_qa",
    "method_knowledge_qa",
    "variable_concept_qa",
    "current_result_qa",
    "chart_interpretation_qa",
    "report_editing_qa",
}


def _norm_text(message: str) -> str:
    return re.sub(r"\s+", "", (message or "").strip().lower())


def _any_marker(text: str, markers: List[str]) -> bool:
    return any(str(m).lower() in text for m in markers)


def _looks_like_concept_question(text: str) -> bool:
    question_markers = [
        "什么是", "是什么", "什么意思", "是什么意思", "区别", "有什么区别",
        "为什么", "为啥", "如何理解", "怎么理解", "怎么解释", "能不能解释",
        "可不可以解释", "原理", "含义", "代表什么", "说明什么", "怎么看", "该怎么看",
        "能否", "能不能", "是否", "是不是", "吗", "?", "？",
    ]
    return _any_marker(text, question_markers)


def _looks_like_execution_request(text: str) -> bool:
    execution_markers = [
        "帮我", "请", "把", "将", "设置", "设为", "改成", "改为", "修改", "应用", "确认",
        "运行", "跑", "重跑", "重新运行", "生成", "画", "绘制", "作图", "制图", "推荐",
        "清洗", "处理", "重编码", "二值化", "删除", "去掉", "上传", "导入", "导出", "下载",
        "插入", "写报告", "生成报告", "生成解释", "解释结果",
    ]
    return _any_marker(text, execution_markers)


def _message_refers_current_context(text: str) -> bool:
    current_markers = [
        "当前", "现在", "这个", "这次", "这里", "我的", "我这个", "我们这个", "结果", "模型", "图", "图表",
        "数据", "变量", "因变量选", "自变量选", "报告", "分析",
    ]
    return _any_marker(text, current_markers)


def _rule_qa_or_action_request(message: str) -> Dict[str, Any]:
    """Deterministic first-pass classifier for QA vs executable actions.

    Rules are intentionally conservative: they catch high-confidence concept and
    context questions before the action router, while letting concrete requests
    such as “生成特征重要性图” or “把 A 设为因变量” continue to tool execution.
    """
    text = _norm_text(message)
    if not text:
        return {"intent_type": "action_request", "confidence": 0.4, "reason": "empty_message"}

    if _is_capability_question(message):
        return {"intent_type": "system_capability_qa", "confidence": 0.98, "reason": "rule: capability question"}

    concept_first = _looks_like_concept_question(text)
    execution_like = _looks_like_execution_request(text)

    # “能不能帮我画图/运行模型/生成报告” is grammatically a question but
    # semantically an action request. Keep it out of the QA branch unless the
    # user is asking for meaning/interpretation/reasoning.
    meaning_question_markers = [
        "什么是", "是什么", "什么意思", "是什么意思", "区别", "为什么", "为啥",
        "如何理解", "怎么理解", "怎么解释", "解释一下", "含义", "代表什么",
        "说明什么", "怎么看", "该怎么看", "是否有意义", "有没有意义", "合不合理", "是否合理",
    ]
    action_question_verbs = [
        "帮我画", "帮我绘制", "帮我生成", "帮我运行", "帮我跑", "帮我设置", "帮我改",
        "能不能画", "能不能绘制", "能不能生成", "能不能运行", "能不能跑",
        "可以画", "可以绘制", "可以生成", "可以运行",
    ]
    if concept_first and execution_like and _any_marker(text, action_question_verbs) and not _any_marker(text, meaning_question_markers):
        return {"intent_type": "action_request", "confidence": 0.86, "reason": "rule: question-shaped action request"}

    variable_terms = [
        "因变量", "自变量", "控制变量", "中介变量", "调节变量", "分组变量",
        "连续型", "整数型", "计数型", "分类型", "有序型", "二分类", "变量类型", "变量角色",
    ]
    chart_terms = [
        "图怎么看", "图表怎么看", "图说明什么", "图表说明什么", "特征重要性", "变量重要性",
        "贡献度", "混淆矩阵", "相关矩阵", "相关性矩阵", "热力图", "roc", "pr曲线",
        "残差图", "预测值", "真实值", "均值图", "系数图", "or值", "森林图",
    ]
    result_terms = [
        "结果", "模型", "auc", "accuracy", "准确率", "f1", "rmse", "mae", "r2", "过拟合",
        "数据泄露", "显著", "p值", "置信区间", "因果", "解释意义", "预测意义", "靠谱吗", "有意义",
    ]
    report_terms = ["报告", "这段", "写法", "怎么写", "图注", "caption", "摘要", "结论"]
    method_terms = [
        "logistic", "逻辑回归", "xgboost", "xgb", "catboost", "随机森林", "svm", "lasso",
        "ridge", "回归", "分类模型", "机器学习", "模型", "方法", "统计", "显著性",
    ]

    # Concept questions win over execution-like wording when the text asks what
    # something means, e.g. “帮我解释一下什么是因变量”.
    if concept_first:
        if _any_marker(text, variable_terms):
            return {"intent_type": "variable_concept_qa", "confidence": 0.92, "reason": "rule: variable concept question"}
        if _any_marker(text, chart_terms):
            return {"intent_type": "chart_interpretation_qa", "confidence": 0.9, "reason": "rule: chart interpretation question"}
        if _any_marker(text, result_terms) or _message_refers_current_context(text):
            return {"intent_type": "current_result_qa", "confidence": 0.86, "reason": "rule: current/result question"}
        if _any_marker(text, report_terms):
            return {"intent_type": "report_editing_qa", "confidence": 0.86, "reason": "rule: report writing question"}
        if _any_marker(text, method_terms):
            return {"intent_type": "method_knowledge_qa", "confidence": 0.84, "reason": "rule: method knowledge question"}
        return {"intent_type": "method_knowledge_qa", "confidence": 0.72, "reason": "rule: generic concept question"}

    # If it has action verbs and is not asking a concept/question, let the action
    # router handle it. This covers “生成特征重要性图”, “运行 XGBoost”, etc.
    if execution_like:
        return {"intent_type": "action_request", "confidence": 0.78, "reason": "rule: executable wording"}

    return {"intent_type": "action_request", "confidence": 0.55, "reason": "rule: default to action router"}


def _llm_qa_or_action_request(req: AgentRequest, dataset: Dict[str, Any], selection: Dict[str, Any]) -> Dict[str, Any]:
    """Classify a user utterance into QA intent types or executable action.

    This LLM classifier is deliberately separate from the action router. Its job
    is only to decide whether the user is asking a question or requesting a tool
    operation. Actual tool/action selection still goes through ACTION_CATALOG.
    """
    message = (req.message or "").strip()
    rule = _rule_qa_or_action_request(message)

    # High-confidence deterministic QA should not be reinterpreted as an action.
    if str(rule.get("intent_type")) in QA_INTENTS and float(rule.get("confidence") or 0) >= 0.88:
        rule["router_backend"] = "rule_qa_guard"
        return rule

    if not deepseek_enabled():
        rule["router_backend"] = "rule_only"
        return rule

    payload = {
        "user_message": message,
        "has_dataset": bool(dataset.get("found")),
        "has_analysis_result": bool(_has_analysis(req)),
        "selected_variables": selection,
        "dataset_columns": [c.get("name") for c in (dataset.get("columns") or [])[:60]],
        "valid_intent_types": [
            "system_capability_qa",
            "method_knowledge_qa",
            "variable_concept_qa",
            "current_result_qa",
            "chart_interpretation_qa",
            "report_editing_qa",
            "action_request",
            "unsupported",
        ],
        "decision_rules": [
            "If the user asks what a concept means, why something happens, how to interpret a metric/model/chart, or whether something is reasonable, choose a QA intent, not action_request.",
            "If the user explicitly asks to modify/apply/set variables, clean data, run a model, generate a chart, generate a report, upload/delete/export/download, choose action_request.",
            "A question can mention variables/models/charts without requesting action. Example: '什么是因变量' is variable_concept_qa, not variable_setup.",
            "A question about the current project/result/model/chart should choose current_result_qa or chart_interpretation_qa.",
            "Choose unsupported only for requests unrelated to MethodPal/data analysis.",
        ],
        "examples": [
            {"text": "什么是因变量？", "intent_type": "variable_concept_qa"},
            {"text": "自变量和控制变量有什么区别？", "intent_type": "variable_concept_qa"},
            {"text": "把更新类型设为因变量", "intent_type": "action_request"},
            {"text": "XGBoost 的特征重要性是不是显著性？", "intent_type": "method_knowledge_qa"},
            {"text": "生成特征重要性图", "intent_type": "action_request"},
            {"text": "这个结果能不能支撑论文结论？", "intent_type": "current_result_qa"},
            {"text": "这个图怎么看？", "intent_type": "chart_interpretation_qa"},
        ],
        "output_schema": {
            "intent_type": "one of valid_intent_types",
            "confidence": 0.0,
            "reason": "brief Chinese reason",
        },
    }
    try:
        raw = generate_text(
            system_prompt=(
                "你是 MethodPal 的第一层意图分类器。你的任务只是判断用户是在问问题还是要求执行工具操作。"
                "不要回答问题，不要选择具体工具。必须只返回 JSON。"
            ),
            user_prompt=json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            temperature=0.0,
            max_tokens=260,
        )
        obj = _extract_json_object(raw)
        intent_type = str(obj.get("intent_type") or "").strip()
        if intent_type not in QA_INTENTS and intent_type not in {"action_request", "unsupported"}:
            rule["router_backend"] = "llm_qa_invalid_fallback"
            return rule
        try:
            confidence = float(obj.get("confidence", 0.7))
        except Exception:
            confidence = 0.7
        return {
            "intent_type": intent_type,
            "confidence": max(0.0, min(1.0, confidence)),
            "reason": str(obj.get("reason") or "llm_qa_classifier"),
            "router_backend": "llm_qa_classifier",
        }
    except Exception as exc:
        rule["router_backend"] = "llm_qa_failed_fallback"
        rule["reason"] = "{}; llm_failed: {}".format(rule.get("reason"), exc)
        return rule


def _fallback_knowledge_answer(message: str, dataset: Dict[str, Any], selection: Dict[str, Any], analysis: Dict[str, Any], qa_intent: str) -> str:
    text = _norm_text(message)
    dep = selection.get("dependent_variable")
    indeps = selection.get("independent_variables") or []
    controls = selection.get("control_variables") or []
    current_bits = []
    if dep:
        current_bits.append("你当前设置的因变量是 **{}**。".format(dep))
    if indeps:
        current_bits.append("当前自变量包括：{}。".format("、".join([str(x) for x in indeps[:8]]) + ("等" if len(indeps) > 8 else "")))
    if controls:
        current_bits.append("当前控制变量包括：{}。".format("、".join([str(x) for x in controls[:6]]) + ("等" if len(controls) > 6 else "")))
    current_note = "\n\n" + " ".join(current_bits) if current_bits else ""

    if "因变量" in text:
        return (
            "**因变量**就是研究中你想解释、预测或比较的结果变量，也可以理解为“被影响的那个变量”。\n\n"
            "比如你研究“哪些因素影响小区是否更新”，那么“是否更新”或“更新类型”通常就是因变量；"
            "人口密度、建筑年限、租金、周边设施距离等更可能作为自变量或控制变量。"
            + current_note
        )
    if "自变量" in text:
        return (
            "**自变量**是用来解释或预测因变量的变量，也就是模型里的主要解释因素。\n\n"
            "例如研究更新行为时，建筑密度、人口密度、租金水平、与学校/医院/公园的距离等，"
            "都可能作为自变量。自变量的作用需要结合模型类型来理解：回归模型更偏统计解释，"
            "XGBoost/随机森林的重要性更偏预测贡献。"
            + current_note
        )
    if "控制变量" in text:
        return (
            "**控制变量**是为了减少混杂影响而放进模型的背景变量。它们不一定是你的核心研究重点，"
            "但会影响因变量，也可能和核心自变量有关。\n\n"
            "例如城市、年份、区域类型、建筑类型等常作为控制变量。控制变量的意义是：在比较核心自变量时，"
            "尽量让这些背景差异保持可比。"
            + current_note
        )
    if "有序" in text or "分类" in text or "连续" in text or "整数" in text or "计数" in text:
        return (
            "变量类型可以粗略这样区分：\n\n"
            "- **连续型**：可以取很多数值，差值有意义，比如距离、面积、租金、人口密度。\n"
            "- **整数型/计数型**：表示次数或数量，比如楼栋数、户数、事故次数。\n"
            "- **分类型**：类别之间没有天然顺序，比如城市、结构类型。\n"
            "- **有序型**：类别有顺序，但间距不一定相等，比如低/中/高、满意度等级。\n"
            "- **二分类**：只有两个状态，比如是否更新、是否通过。\n\n"
            "注意：一个变量看起来是整数，不一定就是整数型；如果它本质是距离、租金、比例，通常仍应按连续型理解。"
            + current_note
        )
    if "特征重要性" in text or "贡献度" in text:
        return (
            "XGBoost、随机森林、CatBoost 里的**特征重要性/贡献度**通常表示变量对模型预测的贡献大小，"
            "不是传统统计回归里的 p 值，也不等于因果效应。\n\n"
            "它适合回答“哪些变量对预测最有用”，但不适合直接写成“某变量显著导致了结果变化”。"
            "如果你的研究目标是解释影响因素，最好把机器学习重要性和 Logistic/线性回归、稳健性检验一起报告。"
            + current_note
        )
    if "auc" in text:
        return (
            "**AUC** 衡量二分类模型区分正负样本的能力，越接近 1 表示区分能力越强，0.5 接近随机猜测。\n\n"
            "但 AUC 很高不一定都是好事。如果 AUC/准确率接近 1.0，需要检查是否存在过拟合、数据泄露、"
            "目标变量被自变量间接包含、训练测试重复样本等问题。"
            + current_note
        )
    if qa_intent == "chart_interpretation_qa":
        return (
            "图表解读一般不要只读数值，还要结合研究问题说明它支持什么、不支持什么。\n\n"
            "例如特征重要性图应说明哪些变量对预测贡献更高，但要强调这不是因果效应；"
            "混淆矩阵应看对角线正确分类和非对角线主要混淆；相关矩阵应关注强相关变量对和共线性风险；"
            "预测值 vs 真实值图则看点是否接近对角线以及 MAE/RMSE 的大小。"
            + current_note
        )
    return (
        "这是一个问答型问题，不需要执行数据处理或运行模型。简单说：你可以把方法问题分成三层看："
        "变量定义、模型适用性、结果解释边界。\n\n"
        "如果你问的是概念，我会先解释概念；如果你问的是当前项目，我会结合当前变量、模型结果和图表来判断；"
        "只有你明确说“设置/运行/生成/删除/应用”时，我才会进入执行型工具流程。"
        + current_note
    )


def _qa_knowledge_response(req: AgentRequest, dataset: Dict[str, Any], selection: Dict[str, Any], qa_route: Dict[str, Any]) -> AgentResponse:
    qa_intent = str(qa_route.get("intent_type") or "method_knowledge_qa")
    analysis = _current_analysis_result(req)
    stage = _detect_stage(req, dataset, selection)
    state = _state(stage, req, next_step="answer_qa", selected_variables=selection, router=qa_route)
    fallback = _fallback_knowledge_answer(req.message or "", dataset, selection, analysis, qa_intent)

    if deepseek_enabled():
        try:
            payload = {
                "user_question": req.message or "",
                "qa_intent": qa_intent,
                "dataset_summary": _compact_for_llm(dataset, 2800),
                "selected_variables": selection,
                "analysis_result": _compact_for_llm(analysis, 7000),
                "instructions": [
                    "Answer directly in Chinese; do not execute tools and do not say the request is outside MethodPal's executable scope.",
                    "If this is a general concept question, explain the concept first, then optionally connect it to the current project if relevant context exists.",
                    "If this asks about the current result/model/chart, use only the provided context; if a metric or chart value is absent, say it is not available.",
                    "Clearly separate predictive contribution, statistical significance, and causal interpretation.",
                    "Do not create action buttons or pretend to have changed data/model/chart/report.",
                    "Keep it concise, practical, and suitable for a social-science/statistics user.",
                ],
            }
            msg = generate_text(
                system_prompt=(
                    "你是 MethodPal 的问答助手，负责回答统计方法、变量概念、当前结果和图表解释问题。"
                    "你不是工具执行器；不要修改数据、设置变量、运行模型或生成图表。"
                    "如果用户只是问概念，例如“什么是因变量”，直接解释概念，不要要求用户去上传数据。"
                ),
                user_prompt=json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                temperature=0.18,
                max_tokens=850,
            ).strip()
            if not msg:
                msg = fallback
        except Exception:
            msg = fallback
    else:
        msg = fallback

    actions: List[AgentAction] = []
    if qa_intent == "variable_concept_qa" and dataset.get("found"):
        actions.append(_action_go_variables("查看变量设置"))
    elif qa_intent == "method_knowledge_qa" and dataset.get("found"):
        actions.append(_action_go_models("查看模型选择"))
    elif qa_intent == "chart_interpretation_qa" and dataset.get("found"):
        actions.append(_action_go_charts("查看图表分析"))
    elif qa_intent == "report_editing_qa" and dataset.get("found"):
        actions.append(_action_go_report())
    elif qa_intent == "current_result_qa" and analysis:
        actions.extend([_action_go_results(), _action_go_explain()])
    elif not dataset.get("found"):
        actions.append(_action_go_upload())

    return AgentResponse(
        ok=True,
        action=qa_intent,
        mode=_mode(req),
        message=msg,
        dataset_summary=dataset,
        warnings=summarize_dataset_quality(dataset) if dataset.get("found") else [],
        workflow_state=state,
        allowed_actions=state.get("allowed_actions", []),
        actions=actions,
        trace=[AgentTraceEvent(step=1, name="qa_classifier", status="answered", detail="{}: {}".format(qa_intent, qa_route.get("reason") or ""))],
    )

def generate_plan(req: AgentRequest) -> AgentResponse:
    if _mode(req) == "autopilot" or (req.action or "").strip().lower() == "autopilot":
        return run_autopilot(req)

    dataset = get_dataset_summary(_dataset_id(req))
    selection = _selected_variables(req)

    # v0.7.7: First classify QA vs executable action. Concept questions such as
    # “什么是因变量” should be answered directly instead of being misrouted into
    # variable-setting/action flows. Only explicit operation requests continue to
    # the action router below.
    qa_route = _llm_qa_or_action_request(req, dataset, selection)
    qa_intent = str(qa_route.get("intent_type") or "action_request")
    if qa_intent == "system_capability_qa":
        return _capability_help_response(req, dataset, selection)
    if qa_intent in QA_INTENTS:
        # Current-result questions can reuse the richer context QA branch when a
        # dataset exists; general method/variable concept questions use the new QA
        # response so they still work before data upload.
        if dataset.get("found") and qa_intent == "current_result_qa":
            return _context_question_response(req, dataset, {"action_id": "answer_context_question", "intent": "context_qa", "reason": qa_route.get("reason"), "router_backend": qa_route.get("router_backend")})
        return _qa_knowledge_response(req, dataset, selection, qa_route)
    if qa_intent == "unsupported":
        return _unsupported_response(req, dataset, {"action_id": "unsupported", "intent": "unsupported", "reason": qa_route.get("reason"), "router_backend": qa_route.get("router_backend")})

    # v0.6.15: use an LLM action router first, with the action catalog as the
    # contract. Keyword rules remain only as a fallback when the LLM is disabled
    # or returns invalid JSON. This prevents analysis_ready from locking the user
    # into result explanation when they actually want to clean data, change
    # variables, or choose a different method.
    route = _route_user_request(req, dataset, selection)
    action_id = str(route.get("action_id") or "")
    intent = str(route.get("intent") or "general")

    # Only run the deterministic data-transform detector when the LLM router
    # selected a data transformation action. Otherwise phrases like “项目序号和
    # 项目名称啥都不是，其他都是自变量” may be misread as a recode request.
    transform_op = None
    if dataset.get("found") and (action_id == "preview_data_transform" or intent == "data_transform"):
        transform_op = detect_transform_intent(req.message or "", _dataset_id(req), selection)
        if transform_op:
            return _transform_plan_response(req, dataset, transform_op)
    if intent == "unsupported" or action_id == "unsupported":
        return _unsupported_response(req, dataset, route)
    if action_id == "upload_data":
        state = _state("no_dataset" if not dataset.get("found") else _detect_stage(req, dataset, selection), req, next_step="upload_or_replace_dataset", selected_variables=selection, router=route)
        return AgentResponse(
            action="upload_data",
            mode="assisted",
            message="可以。如果你想上传、替换或重新导入数据，请进入数据上传页。上传后我会重新读取数据摘要。",
            dataset_summary=dataset,
            workflow_state=state,
            allowed_actions=state.get("allowed_actions", []),
            actions=[_action_go_upload()],
            trace=[AgentTraceEvent(step=1, name="llm_action_router", detail=str(route.get("reason") or action_id))],
        )
    if action_id == "data_summary" or intent == "data_summary":
        return _data_summary_response(req, dataset)
    if dataset.get("found") and (action_id in {"recommend_charts", "generate_chart", "run_model_and_generate_chart"} or intent == "charting"):
        return _chart_request_response(req, dataset, route)
    if dataset.get("found") and (action_id in {"critique_analysis", "answer_context_question", "explain_results"} or intent == "context_qa"):
        return _context_question_response(req, dataset, route)
    if action_id == "generate_report" or intent == "report":
        state = _state(_detect_stage(req, dataset, selection), req, next_step="go_report", selected_variables=selection, router=route)
        return AgentResponse(
            action="generate_report",
            mode="assisted",
            message="可以。报告草稿生成属于项目内写作操作，请进入图表与报告页继续。",
            dataset_summary=dataset,
            workflow_state=state,
            allowed_actions=state.get("allowed_actions", []),
            actions=[_action_go_report(), _action_go_explain(), _action_go_results()],
            trace=[AgentTraceEvent(step=1, name="llm_action_router", detail=str(route.get("reason") or action_id))],
        )
    if dataset.get("found") and action_id == "update_variable_roles":
        return _variable_selection_update_response(req, dataset, route)
    if dataset.get("found") and action_id == "update_variable_types":
        return _variable_type_update_response(req, dataset, route)
    if dataset.get("found") and action_id == "explain_variable_types":
        # If the user asks about types and mentions concrete fields/type changes,
        # offer an applyable type patch instead of only explaining concepts.
        patch_probe = _rule_variable_type_patch(req, dataset, selection)
        if patch_probe.get("updates"):
            return _variable_type_update_response(req, dataset, route)
        return _variable_type_help_response(req, dataset, route)
    if dataset.get("found") and intent == "data_transform":
        return _transform_clarify_response(req, dataset)
    if dataset.get("found") and intent == "variable_setup":
        # If the user explicitly names roles, return an applyable variable
        # selection patch. Otherwise fall back to general recommendation.
        patch_probe = _rule_variable_selection_patch(req, dataset, selection)
        if not patch_probe.get("empty"):
            return _variable_selection_update_response(req, dataset, route)
        return recommend_variable_setup(req)
    if dataset.get("found") and intent == "method_setup":
        return recommend_method_setup(req)

    stage = _detect_stage(req, dataset, selection)

    if stage == "no_dataset":
        warnings = summarize_dataset_quality(dataset)
        state = _state("no_dataset", req, next_step="upload_dataset")
        return AgentResponse(
            action="plan",
            mode="assisted",
            message="当前阶段：**等待上传数据**。请先上传 CSV/XLSX。上传后我会读取数据摘要，先推荐模型，再按模型 requirement 配置变量。",
            dataset_summary=dataset,
            warnings=warnings,
            workflow_state=state,
            allowed_actions=state.get("allowed_actions", []),
            plan=[AgentPlanStep(step=1, title="上传数据", detail="上传 CSV/XLSX 或粘贴表格。", tool="upload_dataset", status="pending")],
            actions=[_action_go_upload()],
        )

    if stage == "data_review_pending":
        warnings = summarize_dataset_quality(dataset)
        state = _state("data_review_pending", req, next_step="review_data_then_choose_model", selected_variables=selection)
        return AgentResponse(
            action="plan",
            mode="assisted",
            message="当前阶段：**数据检查**。系统已经完成初步缺失值和异常值诊断。你可以先在数据工作区查看建议，也可以直接进入模型选择；模型选择不再依赖预先填写因变量。",
            dataset_summary=dataset,
            warnings=warnings,
            workflow_state=state,
            allowed_actions=state.get("allowed_actions", []),
            plan=[
                AgentPlanStep(step=1, title="检查数据质量", detail="查看缺失值、异常值和字段类型。", tool="get_dataset_profile", status="pending"),
                AgentPlanStep(step=2, title="选择模型", detail="根据研究目标先确定分析方法。", tool="recommend_methods", status="pending"),
                AgentPlanStep(step=3, title="配置模型变量", detail="只补充该模型要求的变量角色。", tool="configure_variables", status="pending"),
            ],
            actions=[_action_go_upload(), _action_go_models()],
        )

    if stage in {"method_pending", "ready_to_run"}:
        return recommend_method_setup(req)

    if stage == "variable_pending":
        selected_model = _selected_model(req)
        method_state = get_method_configuration(selected_model, selection, _dataset_id(req) or "", _method_goal(req))
        state = _state(
            "variable_pending", req, next_step="configure_model_variables", selected_variables=selection,
            selected_model=selected_model or None, method_availability=method_state.get("availability"),
            missing_roles=method_state.get("missing_roles") or [],
        )
        return AgentResponse(
            action="plan",
            mode="assisted",
            message="当前模型 **{}** 还不能运行。{}".format(selected_model, method_state.get("reason") or "请补齐模型 requirement 中的变量角色。"),
            dataset_summary=dataset,
            workflow_state=state,
            allowed_actions=state.get("allowed_actions", []),
            actions=[_action_go_variables(), _action_go_models()],
        )

    state = _state("analysis_ready", req, next_step="explain_or_report", selected_variables=selection, selected_model=_selected_model(req) or None)
    return AgentResponse(
        action="plan",
        mode="assisted",
        message=_fallback_result_message(),
        dataset_summary=dataset,
        warnings=summarize_dataset_quality(dataset),
        workflow_state=state,
        allowed_actions=state.get("allowed_actions", []),
        plan=[
            AgentPlanStep(step=1, title="查看分析结果", detail="先确认统计量、模型指标和诊断信息。", tool="get_analysis", status="pending"),
            AgentPlanStep(step=2, title="生成解释", detail="基于真实结果生成论文式解释。", tool="explain_result", status="pending"),
        ],
        actions=[_action_go_results(), _action_go_explain(), _action_go_report()],
    )


def _run_analysis_with_fallback(dataset_id: str, model_id: str, selection: Dict[str, Any], options: Dict[str, Any]) -> Tuple[Dict[str, Any], str, List[AgentTraceEvent]]:
    trace: List[AgentTraceEvent] = []
    try:
        trace.append(AgentTraceEvent(step=4, name="run_analysis", detail="运行模型：{}".format(model_id)))
        return run_selected_analysis(dataset_id=dataset_id, model_id=model_id, selected_variables=selection, options=options), model_id, trace
    except Exception as exc:
        trace.append(AgentTraceEvent(step=4, name="run_analysis", status="failed", detail="{} 失败：{}".format(model_id, exc)))
        fallback_model = "descriptive_statistics"
        trace.append(AgentTraceEvent(step=5, name="fallback_analysis", detail="改用描述统计作为安全兜底。"))
        return run_selected_analysis(dataset_id=dataset_id, model_id=fallback_model, selected_variables=selection, options=options), fallback_model, trace


def run_autopilot(req: AgentRequest) -> AgentResponse:
    dataset_id = _dataset_id(req)
    dataset = get_dataset_summary(dataset_id)
    warnings = summarize_dataset_quality(dataset)
    if not dataset_id or not dataset.get("found"):
        state = _state("no_dataset", req, mode="autopilot", next_step="upload_dataset")
        return AgentResponse(
            ok=False, action="autopilot", mode="autopilot",
            message="自动模式需要先有数据集。请先上传数据。",
            dataset_summary=dataset, warnings=warnings, workflow_state=state,
            allowed_actions=state.get("allowed_actions", []), actions=[_action_go_upload()],
        )

    trace: List[AgentTraceEvent] = [AgentTraceEvent(step=1, name="get_dataset_profile", detail=_dataset_short_line(dataset))]
    selection = _selected_variables(req)

    # Model-first: choose a model from dataset types and the research goal before
    # assigning analytical roles. The requirement then determines which roles
    # must be completed.
    methods = recommend_methods_for_selection(selection, dataset_id, _method_goal(req))
    primary = _selected_model(req) or choose_primary_method(methods, _method_goal(req))
    trace.append(AgentTraceEvent(step=2, name="select_model", detail="选择主模型：{}".format(primary or "descriptive_statistics")))
    if not primary:
        primary = "descriptive_statistics"

    variable_suggestions: List[Any] = []
    auto_applied_variables = False
    method_state = get_method_configuration(primary, selection, dataset_id, _method_goal(req))
    if not method_state.get("enabled"):
        proposed, variable_suggestions = build_selection_for_method(primary, selection, dataset_id, _research_goal(req))
        if proposed:
            selection = proposed
            auto_applied_variables = True
            trace.append(AgentTraceEvent(step=3, name="configure_variables", detail="根据所选模型 requirement 生成候选变量设置。"))
        method_state = get_method_configuration(primary, selection, dataset_id, _method_goal(req))

    if not method_state.get("enabled"):
        state = _state(
            "variable_pending", req, mode="autopilot", next_step="confirm_model_variables",
            selected_variables=selection, selected_model=primary, primary_method=primary,
            method_availability=method_state.get("availability"), missing_roles=method_state.get("missing_roles") or [],
            auto_applied_variables=auto_applied_variables,
        )
        return AgentResponse(
            ok=False, action="autopilot", mode="autopilot",
            message="自动模式已先选定模型 **{}**，但仍无法可靠补齐它的变量 requirement：{} 请在数据工作区确认后再运行。".format(primary, method_state.get("reason") or "变量配置不完整"),
            dataset_summary=dataset, variable_suggestions=variable_suggestions, method_suggestions=methods,
            warnings=warnings + list(method_state.get("validation_messages") or []),
            workflow_state=state, allowed_actions=state.get("allowed_actions", []),
            actions=[
                AgentAction(id="apply_variable_selection", label="应用候选变量设置", type="apply_variables", payload=selection, disabled=not bool(selection)),
                _action_go_variables(), _action_go_models(),
            ],
            trace=trace,
        )

    try:
        result, executed_model, run_trace = _run_analysis_with_fallback(
            dataset_id=dataset_id, model_id=primary, selection=selection,
            options=req.options.get("analysis_options") or req.context.model_options or {},
        )
        trace.extend(run_trace)
        if executed_model != primary:
            warnings.append("推荐模型「{}」执行失败，已安全降级为「{}」。".format(primary, executed_model))
    except Exception as exc:
        state = _state(
            "ready_to_run", req, mode="autopilot", next_step="manual_model_run",
            selected_variables=selection, selected_model=primary, primary_method=primary,
            method_availability="ready", auto_applied_variables=auto_applied_variables,
        )
        return AgentResponse(
            ok=False, action="autopilot", mode="autopilot",
            message="模型和变量已经准备好，但自动执行失败：{}".format(exc),
            dataset_summary=dataset, variable_suggestions=variable_suggestions, method_suggestions=methods,
            warnings=warnings, workflow_state=state, allowed_actions=state.get("allowed_actions", []),
            actions=[_action_go_models()], trace=trace,
        )

    state = _state(
        "analysis_ready", req, mode="autopilot", next_step="review_results",
        selected_variables=selection, selected_model=executed_model, primary_method=executed_model,
        method_availability="ready", auto_applied_variables=auto_applied_variables,
    )
    message = (
        "自动模式已按新顺序完成：读取数据摘要 → 选择模型 → 按模型 requirement 配置变量 → 运行分析。\n\n"
        "实际运行模型：**{}**。下一步建议先查看结果。"
    ).format(result.get("model_name") or executed_model)
    return AgentResponse(
        action="autopilot", mode="autopilot", message=message + _format_warnings(warnings),
        dataset_summary=dataset, variable_suggestions=variable_suggestions, method_suggestions=methods,
        execution_result=result, warnings=warnings, workflow_state=state,
        allowed_actions=state.get("allowed_actions", []),
        actions=[_action_go_results(), _action_go_explain(), _action_go_report()], trace=trace,
    )


def execute_agent_action(req: AgentRequest) -> AgentResponse:
    dataset_id = _dataset_id(req)
    dataset = get_dataset_summary(dataset_id)
    if not dataset_id or not dataset.get("found"):
        state = _state("no_dataset", req, next_step="upload_dataset")
        return AgentResponse(
            ok=False, action="execute", mode=_mode(req),
            message="当前没有数据集，无法执行分析。请先上传数据。",
            dataset_summary=dataset, workflow_state=state,
            allowed_actions=state.get("allowed_actions", []), actions=[_action_go_upload()],
        )

    selection = req.options.get("selected_variables" ) or _selected_variables(req)
    model_id = str(req.options.get("model") or req.options.get("model_id") or _selected_model(req) or "")
    if not model_id:
        return recommend_method_setup(req)

    methods = recommend_methods_for_selection(selection, dataset_id, _method_goal(req))
    method_state = get_method_configuration(model_id, selection, dataset_id, _method_goal(req))
    if not method_state.get("selectable", False):
        state = _state(
            "method_pending", req, next_step="choose_compatible_model", selected_variables=selection,
            selected_model=model_id, primary_method=model_id, method_availability=method_state.get("availability"),
        )
        return AgentResponse(
            ok=False, action="execute", mode=_mode(req),
            message="当前数据不能选择模型「{}」：{}".format(model_id, method_state.get("reason") or "模型不兼容"),
            dataset_summary=dataset, method_suggestions=methods,
            warnings=list(method_state.get("validation_messages") or []),
            workflow_state=state, allowed_actions=state.get("allowed_actions", []),
            actions=[_action_go_models()],
        )

    if not method_state.get("enabled", False):
        state = _state(
            "variable_pending", req, next_step="configure_model_variables", selected_variables=selection,
            selected_model=model_id, primary_method=model_id, method_availability=method_state.get("availability"),
            missing_roles=method_state.get("missing_roles") or [],
        )
        return AgentResponse(
            ok=False, action="execute", mode=_mode(req),
            message="模型「{}」已经选中，但还不能运行。{}".format(model_id, method_state.get("reason") or "请补齐模型要求的变量角色。"),
            dataset_summary=dataset, method_suggestions=methods,
            warnings=list(method_state.get("validation_messages") or []),
            workflow_state=state, allowed_actions=state.get("allowed_actions", []),
            actions=[_action_go_variables(), _action_go_models()],
        )

    validation = validate_outcome_for_methods(selection, dataset_id)
    blocked_methods = set(validation.get("disabled_methods") or [])
    validation_warnings = list(validation.get("warnings") or [])
    if model_id in blocked_methods:
        state = _state(
            "variable_pending", req, next_step="fix_target_encoding", selected_variables=selection,
            selected_model=model_id, primary_method=model_id, method_availability="needs_configuration",
        )
        return AgentResponse(
            ok=False, action="execute", mode=_mode(req),
            message="当前不能运行模型「{}」。{}".format(model_id, "；".join(validation_warnings) or "因变量不满足模型条件。"),
            method_suggestions=methods, warnings=validation_warnings,
            workflow_state=state, allowed_actions=state.get("allowed_actions", []),
            actions=[_action_go_variables(), _action_go_models()],
        )

    try:
        result = run_selected_analysis(
            dataset_id=dataset_id, model_id=model_id, selected_variables=selection,
            options=req.options.get("analysis_options") or req.context.model_options or {},
        )
    except Exception as exc:
        state = _state(
            "ready_to_run", req, next_step="fix_model_or_data", selected_variables=selection,
            selected_model=model_id, primary_method=model_id, method_availability="ready",
        )
        return AgentResponse(
            ok=False, action="execute", mode=_mode(req), message="分析执行失败：{}".format(exc),
            method_suggestions=methods, workflow_state=state,
            allowed_actions=state.get("allowed_actions", []), actions=[_action_go_models(), _action_go_variables()],
        )

    state = _state(
        "analysis_ready", req, next_step="review_results", selected_variables=selection,
        selected_model=model_id, primary_method=model_id, method_availability="ready",
    )
    return AgentResponse(
        action="execute", mode=_mode(req),
        message="已运行模型：{}。结果已生成，请先进入分析结果页查看。".format(result.get("model_name") or model_id),
        method_suggestions=methods, execution_result=result, workflow_state=state,
        allowed_actions=state.get("allowed_actions", []),
        trace=[AgentTraceEvent(step=1, name="run_analysis", detail="运行模型：{}".format(model_id))],
        actions=[_action_go_results()],
    )


def handle_agent_request(req: AgentRequest) -> AgentResponse:
    action = (req.action or "plan").strip().lower()
    if action in {"autopilot", "auto", "automatic"} or _mode(req) == "autopilot":
        return run_autopilot(req)
    if action in {"recommend_variables", "variables"}:
        return recommend_variable_setup(req)
    if action in {"recommend_methods", "methods"}:
        return recommend_method_setup(req)
    if action in {"execute", "run", "run_analysis"}:
        return execute_agent_action(req)
    return generate_plan(req)
