"""Stateful, resumable autonomous analysis workflow.

The workflow uses MethodPal's existing deterministic model tools, while keeping
the intake/clarification state outside any particular LangGraph version.  This
makes the same nodes runnable through LangGraph 0.0.x or the local fallback
executor on Python 3.9.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.schemas.agent import (
    AgentAction,
    AgentPlanStep,
    AgentQuestion,
    AgentQuestionOption,
    AgentResponse,
    AgentTraceEvent,
)
from app.services.agent_tools.analysis_tools import run_selected_analysis
from app.services.agent_tools.dataset_tools import get_dataset_summary, summarize_dataset_quality
from app.services.agent_tools.method_tools import (
    build_selection_for_method,
    choose_primary_method,
    get_method_configuration,
    recommend_methods_for_selection,
)
from app.services.agent_tools.variable_tools import build_default_variable_selection, recommend_variables
from app.services.langgraph_agent.autonomy_policy import (
    analysis_goal_label,
    column_names,
    get_column,
    infer_analysis_goal,
    infer_target_from_text,
    match_column,
    meaningful_research_text,
    normalize_analysis_goal,
    normalize_variable_type,
    parse_column_list,
    requires_target,
    target_type_is_ambiguous,
)
from app.services.store import AGENT_CHATS, AGENT_STATES, ANALYSES, PROJECTS, VARIABLE_CONFIGS
from app.services.artifact_versions import analysis_is_current
from app.services.store import DATASETS
from app.services.langgraph_agent.change_requests import apply_change_request, is_change_request


AUTONOMY_KIND = "methodpal_autonomous_v1"


def _dump_model(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return dict(value)


def _project_key(req: Any) -> str:
    return str(getattr(req.context, "project_id", None) or "no_project")


def _request_thread_id(req: Any) -> Optional[str]:
    return getattr(req, "thread_id", None) or (getattr(req, "options", None) or {}).get("thread_id")


def _request_answers(req: Any) -> Dict[str, Any]:
    answers: Dict[str, Any] = {}
    answers.update(getattr(req, "answers", None) or {})
    answers.update((getattr(req, "options", None) or {}).get("answers") or {})
    return answers


def _project_workspace(req: Any) -> Dict[str, Any]:
    project_id = str(getattr(req.context, "project_id", None) or "")
    return dict(PROJECTS.get(project_id) or {}) if project_id else {}


def _last_completed_workflow_state(req: Any, dataset: Dict[str, Any]) -> Dict[str, Any]:
    project_id = str(getattr(req.context, "project_id", None) or "")
    chat = AGENT_CHATS.get(project_id) or {}
    messages = chat.get("messages") if isinstance(chat, dict) else chat
    current_dataset_id = dataset.get("dataset_id") or getattr(req.context, "dataset_id", None)
    current_version = int(getattr(req.context, "dataset_version", 0) or 0)
    for message in reversed(messages or []):
        if not isinstance(message, dict):
            continue
        workflow_state = (message.get("meta") or {}).get("workflow_state") or {}
        if workflow_state.get("status") != "completed":
            continue
        if workflow_state.get("dataset_id") != current_dataset_id:
            continue
        if int(workflow_state.get("dataset_version") or 0) != current_version:
            continue
        return dict(workflow_state)
    return {}


def _saved_research_spec(req: Any, project: Dict[str, Any], dataset: Dict[str, Any]) -> Dict[str, Any]:
    context_spec = getattr(req.context, "research_spec", None)
    if isinstance(context_spec, dict) and context_spec:
        return dict(context_spec)
    project_spec = project.get("research_spec")
    if isinstance(project_spec, dict) and project_spec:
        return dict(project_spec)

    # Backward compatibility: autonomous runs completed before project-level
    # persistence kept their research spec only in the saved chat metadata.
    completed_state = _last_completed_workflow_state(req, dataset)
    spec = completed_state.get("research_spec") or {}
    return dict(spec) if isinstance(spec, dict) else {}


def _persist_project_analysis_state(
    req: Any,
    session: Dict[str, Any],
    analysis_id: Optional[str] = None,
) -> None:
    """Promote autonomous-session decisions to the durable project workspace."""
    project_id = str(session.get("project_id") or getattr(req.context, "project_id", None) or "")
    if not project_id:
        return
    project = PROJECTS.get(project_id)
    if not project:
        return
    from app.services.background_jobs import workspace_unchanged
    if not workspace_unchanged():
        session.setdefault('warnings', []).append('工作区在任务运行期间发生变化，结果保留在任务历史中，不覆盖新的配置。')
        return

    spec = dict(session.get("research_spec") or {})
    selection = dict(session.get("selected_variables") or project.get("selected_variables") or {})
    if selection and spec.get("analysis_goal"):
        selection["analysis_goal"] = spec["analysis_goal"]

    changes = {"research_spec": spec}
    if spec.get("research_question"):
        changes["research_goal"] = spec["research_question"]
    if spec.get("analysis_goal"):
        changes["analysis_goal"] = spec["analysis_goal"]
    if selection:
        changes["selected_variables"] = selection
    if session.get("selected_model"):
        changes["selected_model"] = session["selected_model"]
    changes["model_options"] = dict(session.get("model_options") or project.get("model_options") or {})
    if analysis_id:
        changes["analysis_id"] = analysis_id
        changes["ai_explanation"] = None
        changes["progress"] = max(85, int(project.get("progress") or 0))
    changes["updated_at"] = datetime.now().isoformat(timespec="seconds")
    from app.services.background_jobs import publish_workspace_changes
    if publish_workspace_changes(project_id, changes) is None:
        session.setdefault('warnings', []).append('工作区在任务运行期间发生变化，结果保留在任务历史中，不覆盖新的配置。')
        return

    variable_key = project_id or str(session.get("dataset_id") or "")
    if variable_key and selection:
        existing = dict(VARIABLE_CONFIGS.get(variable_key) or {})
        existing.update({
            "project_id": project_id,
            "dataset_id": session.get("dataset_id"),
            "selected_model": session.get("selected_model"),
            "selected_variables": selection,
            **selection,
        })
        VARIABLE_CONFIGS[variable_key] = existing


def _new_session(req: Any, dataset: Dict[str, Any]) -> Dict[str, Any]:
    project = _project_workspace(req)
    completed_state = _last_completed_workflow_state(req, dataset)
    saved_spec = _saved_research_spec(req, project, dataset)
    message_goal = None if is_change_request(getattr(req, 'message', '')) else meaningful_research_text(getattr(req, "message", ""))
    context_goal = meaningful_research_text(getattr(req.context, "research_goal", "") or "")
    project_goal = meaningful_research_text(project.get("research_goal") or "")
    research_question = message_goal or context_goal or saved_spec.get("research_question") or project_goal
    selection = dict(getattr(req.context, "selected_variables", None) or project.get("selected_variables") or {})
    target = str(selection.get("dependent_variable") or "")
    available_columns = set(column_names(dataset))
    saved_target = str(saved_spec.get("target_variable") or "")
    target = target if target in available_columns else (saved_target if saved_target in available_columns else "")
    variable_types = selection.get("variable_types") or {}
    data_review_status = str(getattr(req.context, "data_review_status", "pending") or "pending")
    confirmed_type = variable_types.get(target) if data_review_status in {"reviewed", "confirmed", "approved"} else None
    saved_goal = (
        normalize_analysis_goal(getattr(req.context, "analysis_goal", None))
        or normalize_analysis_goal(saved_spec.get("analysis_goal"))
        or normalize_analysis_goal(project.get("analysis_goal"))
        or normalize_analysis_goal(selection.get("analysis_goal"))
    )
    inferred_goal = infer_analysis_goal(message_goal or context_goal or project_goal)
    return {
        "agent_kind": AUTONOMY_KIND,
        "thread_id": _request_thread_id(req) or str(uuid4()),
        "status": "planning",
        "stage": "autonomous_intake",
        "stage_label": "自动分析：检查需求",
        "mode": "autopilot",
        "project_id": getattr(req.context, "project_id", None),
        "dataset_id": dataset.get("dataset_id") or getattr(req.context, "dataset_id", None),
        "dataset_version": int(getattr(req.context, "dataset_version", 0) or 0),
        "data_fingerprint": dataset.get('data_fingerprint'),
        "research_spec": {
            "research_question": research_question,
            "analysis_goal": inferred_goal or saved_goal,
            "target_variable": target or None,
            "target_type": confirmed_type if target else saved_spec.get("target_type"),
            "predictor_variables": [name for name in (selection.get("independent_variables") or saved_spec.get("predictor_variables") or []) if name in available_columns],
            "control_variables": [name for name in (selection.get("control_variables") or saved_spec.get("control_variables") or []) if name in available_columns],
        },
        "selected_variables": selection,
        "selected_model": getattr(req.context, "selected_model", None) or project.get("selected_model"),
        "model_options": dict(getattr(req.context, "model_options", None) or project.get("model_options") or {}),
        "analysis_id": project.get("analysis_id") or completed_state.get("analysis_id"),
        "pending_questions": [],
        "warnings": [],
        "input_errors": [],
        "trace": [],
    }


def _load_or_create_session(req: Any, dataset: Dict[str, Any]) -> Dict[str, Any]:
    stored = AGENT_STATES.get(_project_key(req)) or {}
    requested_thread = _request_thread_id(req)
    same_dataset = (
        stored.get("dataset_id") == (dataset.get("dataset_id") or getattr(req.context, "dataset_id", None))
        and stored.get('data_fingerprint') == dataset.get('data_fingerprint')
    )
    reusable = stored.get("agent_kind") == AUTONOMY_KIND and same_dataset
    if requested_thread:
        reusable = reusable and stored.get("thread_id") == requested_thread
    else:
        # A plain "autopilot" click is a new run. Paused runs are resumed with
        # their explicit thread_id, so a stale waiting session must not shadow
        # the durable project research specification.
        reusable = False
    session = dict(stored) if reusable else _new_session(req, dataset)
    if reusable:
        fields = getattr(req.context, 'model_fields_set', getattr(req.context, '__fields_set__', set()))
        for key in ('selected_model', 'model_options', 'data_review_status', 'dataset_version'):
            if key in fields:
                session[key] = getattr(req.context, key)
        if 'selected_variables' in fields and req.context.selected_variables is not None:
            session['selected_variables'] = dict(req.context.selected_variables)
            spec = session.setdefault('research_spec', {})
            sel = session['selected_variables']
            if 'dependent_variable' in sel:
                if spec.get('target_variable') != sel['dependent_variable']:
                    spec['target_type'] = None
                spec['target_variable'] = sel['dependent_variable']
            for role, spec_key in [('independent_variables', 'predictor_variables'), ('control_variables', 'control_variables')]:
                if role in sel:
                    spec[spec_key] = sel[role]
    return session


def _save_session(req: Any, session: Dict[str, Any]) -> None:
    AGENT_STATES[_project_key(req)] = session


def _trace(session: Dict[str, Any], name: str, detail: str, status: str = "done") -> None:
    from app.services.job_progress import progress
    progress(detail, {'inspect_workspace': 15, 'select_method': 30, 'run_analysis': 50, 'validate_result': 90}.get(name))
    events = session.setdefault("trace", [])
    events.append({"step": len(events) + 1, "name": name, "status": status, "detail": detail})


def _option(value: str, label: str, description: str = "") -> AgentQuestionOption:
    return AgentQuestionOption(value=value, label=label, description=description)


def _analysis_goal_question() -> AgentQuestion:
    return AgentQuestion(
        id="analysis_goal",
        kind="single_choice",
        question="你希望这次分析主要回答哪一类问题？",
        reason="分析目标会直接决定使用解释型统计模型、预测模型还是描述性方法。",
        options=[
            _option("explanation", "解释变量关系", "关注变量之间的统计关联和系数解释"),
            _option("prediction", "预测结果", "关注对新样本的预测表现"),
            _option("description", "描述数据", "关注分布、构成和数据概况"),
            _option("comparison", "比较群体差异", "关注不同组之间是否存在差异"),
        ],
    )


def _target_question(dataset: Dict[str, Any], suggestions: List[Any], reason: str = "") -> AgentQuestion:
    names = column_names(dataset)
    ranked = [s.variable for s in suggestions if getattr(s, "suggested_role", "") == "dependent"]
    ranked.extend(name for name in names if name not in ranked)
    options: List[AgentQuestionOption] = []
    for name in ranked[:10]:
        column = get_column(dataset, name) or {}
        description = "类型：{}；唯一值：{}".format(column.get("type") or "未知", column.get("unique_count") or 0)
        options.append(_option(name, name, description))
    return AgentQuestion(
        id="target_variable",
        kind="column_select",
        question="哪一个字段是你希望解释、预测或比较的结果变量？",
        reason=reason or "当前需求中无法可靠确定结果变量，自动猜测可能会改变研究问题。",
        options=options,
    )


def _target_type_question(target: str, column: Optional[Dict[str, Any]]) -> AgentQuestion:
    unique_count = int((column or {}).get("unique_count") or 0)
    current_type = str((column or {}).get("type") or "未知")
    return AgentQuestion(
        id="target_type",
        kind="single_choice",
        question="字段「{}」在研究中应当按什么类型处理？".format(target),
        reason="它当前被识别为{}、共有{}个唯一值，仅凭数据无法区分连续分数、等级量表或类别编码。".format(current_type, unique_count),
        options=[
            _option("continuous", "连续变量", "数值间距具有实际意义"),
            _option("ordinal", "有序等级", "类别有顺序，但相邻间距不一定相等"),
            _option("categorical", "无序分类", "数值只是类别编码"),
            _option("binary", "二分类", "只有两个结果类别"),
        ],
    )


def _causal_scope_question() -> AgentQuestion:
    return AgentQuestion(
        id="causal_scope",
        kind="single_choice",
        question="你的描述包含因果含义。当前是否先进行关联性探索？",
        reason="仅凭普通观测数据和自动选模不能可靠识别因果效应。",
        options=[
            _option("association", "先做关联性探索", "报告将使用关联而非因果表述"),
            _option("stop", "暂不执行", "保留当前状态，等待补充识别策略"),
        ],
    )


def _question_actions(question: AgentQuestion, thread_id: str) -> List[AgentAction]:
    actions: List[AgentAction] = []
    for item in getattr(question, "options", []) or []:
        actions.append(
            AgentAction(
                id="answer_{}_{}".format(question.id, item.value),
                label=item.label,
                type="clarification_answer",
                payload={"thread_id": thread_id, "question_id": question.id, "value": item.value},
                requires_confirmation=False,
            )
        )
    return actions


def _workflow_state(session: Dict[str, Any], stage: Optional[str] = None, **updates: Any) -> Dict[str, Any]:
    state = {
        "stage": stage or session.get("stage") or "autonomous_intake",
        "stage_label": session.get("stage_label") or "自动分析",
        "mode": "autopilot",
        "status": session.get("status") or "planning",
        "thread_id": session.get("thread_id"),
        "project_id": session.get("project_id"),
        "dataset_id": session.get("dataset_id"),
        "dataset_version": session.get("dataset_version") or 0,
        "selected_variables": session.get("selected_variables") or None,
        "selected_model": session.get("selected_model"),
        "model_options": session.get("model_options") or {},
        "research_spec": session.get("research_spec") or {},
        "questions": session.get("pending_questions") or [],
        "allowed_actions": updates.pop("allowed_actions", []),
        "next_step": updates.pop("next_step", ""),
        "auto_applied_variables": bool(session.get("selected_variables")),
    }
    state.update(updates)
    return state


def _explicit_rerun_requested(req: Any) -> bool:
    text = str(getattr(req, "message", "") or "").strip().lower()
    return any(token in text for token in ("重新运行", "重跑", "再次运行", "rerun", "run again"))


def _completed_response_if_current(
    req: Any,
    session: Dict[str, Any],
    dataset: Dict[str, Any],
) -> Optional[AgentResponse]:
    if str(getattr(req, "action", "") or "").lower() == "resume" or _request_answers(req):
        return None
    if meaningful_research_text(getattr(req, "message", "")):
        return None
    if _explicit_rerun_requested(req):
        return None
    analysis_id = str(session.get("analysis_id") or "")
    result = ANALYSES.get(analysis_id) if analysis_id else None
    if not result or not analysis_is_current(result, DATASETS.get(session.get('dataset_id')) or {},
            session.get('selected_model'), session.get('selected_variables') or {}, session.get('model_options') or {}):
        return None

    session.update({
        "status": "completed",
        "stage": "analysis_ready",
        "stage_label": "自动分析：已有有效结果",
        "pending_questions": [],
    })
    _persist_project_analysis_state(req, session, analysis_id)
    _trace(session, "reuse_completed_analysis", "当前数据版本已有有效分析结果，无需重复运行")
    _save_session(req, session)
    return AgentResponse(
        ok=True,
        action="autopilot",
        mode="autopilot",
        status="completed",
        thread_id=session.get("thread_id"),
        message="当前数据版本的基础分析已经完成，我已恢复之前保存的研究目标和分析结果，无需再次回答研究问题或重复运行模型。若需要重算，请明确输入“按当前方案重新运行”。",
        dataset_summary=dataset,
        execution_result=result,
        warnings=summarize_dataset_quality(dataset) + list(session.get("warnings") or []),
        research_spec=session.get("research_spec") or {},
        analysis_plan=session.get("analysis_plan") or {},
        workflow_state=_workflow_state(session, next_step="review_results", analysis_id=analysis_id),
        actions=[
            AgentAction(id="go_results", label="查看分析结果", type="navigate", payload={"page": "results"}, requires_confirmation=False),
            AgentAction(id="go_charts", label="查看或生成图表", type="navigate", payload={"page": "charts"}, requires_confirmation=False),
            AgentAction(id="go_report", label="生成分析报告", type="navigate", payload={"page": "report"}, requires_confirmation=False),
        ],
        trace=[AgentTraceEvent(**item) for item in session.get("trace") or []],
    )


def _waiting_response(
    req: Any,
    session: Dict[str, Any],
    dataset: Dict[str, Any],
    question: AgentQuestion,
    warnings: Optional[List[str]] = None,
) -> AgentResponse:
    session["status"] = "waiting_for_user"
    session["stage"] = "clarification_pending"
    session["stage_label"] = "自动分析：等待补充信息"
    session["pending_questions"] = [_dump_model(question)]
    _save_session(req, session)

    spec = session.get("research_spec") or {}
    understood: List[str] = []
    if spec.get("research_question"):
        understood.append("研究需求：{}".format(spec["research_question"]))
    if spec.get("analysis_goal"):
        understood.append("分析目标：{}".format(analysis_goal_label(spec["analysis_goal"])))
    if spec.get("target_variable"):
        understood.append("结果变量：{}".format(spec["target_variable"]))
    prefix = "我先检查了当前数据和研究需求。"
    if understood:
        prefix += "\n\n已确认：\n- " + "\n- ".join(understood)
    errors = session.get("input_errors") or []
    error_text = "\n\n" + "\n".join("- {}".format(item) for item in errors) if errors else ""
    message = "{}{}\n\n还需要确认一项信息：**{}**".format(prefix, error_text, question.question)
    return AgentResponse(
        ok=False,
        action="clarify",
        mode="autopilot",
        status="waiting_for_user",
        thread_id=session.get("thread_id"),
        message=message,
        dataset_summary=dataset,
        warnings=warnings or [],
        questions=[question],
        research_spec=spec,
        workflow_state=_workflow_state(session, next_step="answer_clarification"),
        actions=_question_actions(question, session.get("thread_id") or ""),
        trace=[AgentTraceEvent(**item) for item in session.get("trace") or []],
    )


def _apply_answers(req: Any, session: Dict[str, Any], dataset: Dict[str, Any]) -> None:
    answers = _request_answers(req)
    pending = session.get("pending_questions") or []
    if not answers and pending:
        free_text = None if is_change_request(getattr(req, 'message', '')) else meaningful_research_text(getattr(req, "message", ""))
        if free_text:
            answers[str(pending[0].get("id") or "research_question")] = free_text

    spec = session.setdefault("research_spec", {})
    names = column_names(dataset)
    session["input_errors"] = []
    for question_id, raw_value in answers.items():
        if question_id == "research_question":
            value = meaningful_research_text(str(raw_value or ""))
            if value:
                spec["research_question"] = value
                spec["analysis_goal"] = infer_analysis_goal(value)
            else:
                session["input_errors"].append("研究需求不能为空。")
        elif question_id == "analysis_goal":
            goal = normalize_analysis_goal(raw_value)
            if goal:
                spec["analysis_goal"] = goal
            else:
                session["input_errors"].append("无法识别分析目标，请从给出的选项中选择。")
        elif question_id == "target_variable":
            target = match_column(raw_value, names)
            if target:
                changed_target = spec.get("target_variable") != target
                spec["target_variable"] = target
                if changed_target:
                    spec["target_type"] = None
            else:
                session["input_errors"].append("没有在当前数据中找到字段「{}」。".format(raw_value))
        elif question_id == "target_type":
            variable_type = normalize_variable_type(raw_value)
            if variable_type:
                spec["target_type"] = variable_type
            else:
                session["input_errors"].append("无法识别字段类型，请从给出的选项中选择。")
        elif question_id == "predictor_variables":
            predictors = parse_column_list(raw_value, names)
            if predictors:
                spec["predictor_variables"] = predictors
            else:
                session["input_errors"].append("没有识别到有效的解释或预测字段。")
        elif question_id == "causal_scope":
            value = str(raw_value or "").strip().lower()
            if value in {"association", "关联", "先做关联性探索"}:
                spec["analysis_goal"] = "explanation"
                spec["causal_scope"] = "association_only"
                session.setdefault("warnings", []).append("已将因果问题降级为关联性探索，报告不得使用因果结论。")
            elif value in {"stop", "停止", "暂不执行"}:
                session["cancelled"] = True
            else:
                session["input_errors"].append("请确认是先做关联性探索，还是暂不执行。")
    if answers:
        session["pending_questions"] = []
        _trace(session, "merge_user_answers", "合并用户补充信息：{}".format("、".join(answers.keys())))
        _persist_project_analysis_state(req, session)


def _next_intake_question(session: Dict[str, Any], dataset: Dict[str, Any], suggestions: List[Any]) -> Optional[AgentQuestion]:
    spec = session.setdefault("research_spec", {})
    if not spec.get("research_question") and not spec.get("analysis_goal"):
        return AgentQuestion(
            id="research_question",
            kind="text",
            question="你希望用这份数据回答什么问题？",
            reason="需要先知道研究问题，才能判断目标变量、模型类型和输出。",
        )

    if not spec.get("analysis_goal"):
        return _analysis_goal_question()

    if spec.get("analysis_goal") == "causal" and not spec.get("causal_scope"):
        return _causal_scope_question()

    if requires_target(spec.get("analysis_goal")):
        names = column_names(dataset)
        target = spec.get("target_variable") or infer_target_from_text(spec.get("research_question") or "", names)
        if target:
            spec["target_variable"] = target
        if not target:
            return _target_question(dataset, suggestions)

        column = get_column(dataset, target)
        if target_type_is_ambiguous(column, spec.get("target_type")):
            return _target_type_question(target, column)
        if not spec.get("target_type") and column:
            spec["target_type"] = column.get("type") or "未知"

    return None


def prepare_autonomous_intake(req: Any) -> Dict[str, Any]:
    dataset = get_dataset_summary(getattr(req.context, "dataset_id", None))
    warnings = summarize_dataset_quality(dataset)
    session = _load_or_create_session(req, dataset)
    _trace(session, "inspect_workspace", "检查项目、数据版本和现有变量配置")
    message = (getattr(req, 'answers', {}) or {}).get('change_request') or getattr(req, 'message', '')
    changes, change_error = apply_change_request(session, message, column_names(dataset))
    if changes:
        _trace(session, 'apply_workspace_changes', '；'.join(changes))
    if change_error:
        return {'route': 'wait', 'session': session, 'dataset': dataset,
                'response': _waiting_response(req, session, dataset, AgentQuestion(
                    id='change_request', question=change_error, reason='不明确的修改不会静默沿用旧方案。'))}

    if not dataset.get("found"):
        session.update({
            "status": "waiting_for_user",
            "stage": "no_dataset",
            "stage_label": "等待上传数据",
            "pending_questions": [],
        })
        _save_session(req, session)
        response = AgentResponse(
            ok=False,
            action="autopilot",
            mode="autopilot",
            status="waiting_for_user",
            thread_id=session.get("thread_id"),
            message="自动分析需要先读取数据。请先在数据工作区上传 CSV 或 XLSX；上传后可以继续当前研究需求。",
            dataset_summary=dataset,
            warnings=warnings,
            workflow_state=_workflow_state(session, next_step="upload_dataset"),
            actions=[AgentAction(id="go_upload", label="去上传数据", type="navigate", payload={"page": "upload"}, requires_confirmation=False)],
            trace=[AgentTraceEvent(**item) for item in session.get("trace") or []],
        )
        return {"route": "wait", "session": session, "dataset": dataset, "response": response}

    completed = _completed_response_if_current(req, session, dataset)
    if completed:
        return {"route": "wait", "session": session, "dataset": dataset, "response": completed}

    _apply_answers(req, session, dataset)
    if session.get("cancelled"):
        session.update({"status": "cancelled", "stage": "cancelled", "stage_label": "自动分析已暂停"})
        _save_session(req, session)
        response = AgentResponse(
            ok=False,
            action="autopilot",
            mode="autopilot",
            status="cancelled",
            thread_id=session.get("thread_id"),
            message="已暂停自动分析。当前研究状态已经保留；补充处理变量、对照组和识别策略后可以重新开始。",
            dataset_summary=dataset,
            research_spec=session.get("research_spec") or {},
            workflow_state=_workflow_state(session),
        )
        return {"route": "wait", "session": session, "dataset": dataset, "response": response}

    suggestions = recommend_variables(dataset, (session.get("research_spec") or {}).get("research_question") or "")
    question = _next_intake_question(session, dataset, suggestions)
    if question:
        _trace(session, "detect_information_gap", "需要用户补充：{}".format(question.id))
        response = _waiting_response(req, session, dataset, question, warnings)
        return {"route": "wait", "session": session, "dataset": dataset, "response": response}

    session.update({"status": "planning", "stage": "analysis_planning", "stage_label": "自动分析：生成计划", "pending_questions": []})
    _trace(session, "information_ready", "研究目标、结果变量和关键字段类型已满足最低要求")
    _save_session(req, session)
    return {"route": "plan", "session": session, "dataset": dataset, "suggestions": suggestions}


def _predictor_question(dataset: Dict[str, Any], missing_roles: List[str]) -> AgentQuestion:
    return AgentQuestion(
        id="predictor_variables",
        kind="text",
        question="请填写要纳入分析的解释或预测字段，多个字段可用逗号分隔。",
        reason="所选方法仍缺少必要变量角色：{}。".format("、".join(missing_roles) or "自变量"),
    )


def _method_goal_text(spec: Dict[str, Any]) -> str:
    goal_hints = {
        "description": "描述统计 数据概况",
        "explanation": "解释变量关系 关联分析",
        "prediction": "预测 新样本表现",
        "comparison": "比较群体差异",
    }
    return "{} {}".format(
        spec.get("research_question") or "",
        goal_hints.get(spec.get("analysis_goal"), ""),
    ).strip()


def prepare_autonomous_plan(req: Any, session: Dict[str, Any], dataset: Dict[str, Any]) -> Dict[str, Any]:
    spec = session.get("research_spec") or {}
    research_question = spec.get("research_question") or ""
    method_goal = _method_goal_text(spec)
    suggestions = recommend_variables(dataset, research_question)
    selection = dict(session.get("selected_variables") or {})
    if not selection:
        selection = build_default_variable_selection(suggestions)

    target = spec.get("target_variable")
    if target:
        selection["dependent_variable"] = target
    selection.setdefault("independent_variables", [])
    selection.setdefault("control_variables", [])
    selection.setdefault("mediator_variables", [])
    selection.setdefault("moderator_variables", [])
    selection.setdefault("group_variable", None)
    variable_types = dict(selection.get("variable_types") or {})
    if target and spec.get("target_type"):
        variable_types[target] = spec["target_type"]
    selection["variable_types"] = variable_types
    if spec.get("analysis_goal"):
        selection["analysis_goal"] = spec["analysis_goal"]

    explicit_predictors = [name for name in spec.get("predictor_variables") or [] if name != target]
    if explicit_predictors:
        selection["independent_variables"] = explicit_predictors
    else:
        selection["independent_variables"] = [
            name for name in selection.get("independent_variables") or [] if name != target
        ][:8]

    methods = recommend_methods_for_selection(selection, session.get("dataset_id") or "", method_goal)
    if spec.get("analysis_goal") == "description" and not session.get("selected_model"):
        primary = "descriptive_statistics"
    else:
        primary = session.get("selected_model") or choose_primary_method(methods, method_goal)
    if not primary:
        primary = "descriptive_statistics"
    method_state = get_method_configuration(primary, selection, session.get("dataset_id") or "", method_goal)

    if not method_state.get("enabled"):
        proposed, proposed_suggestions = build_selection_for_method(
            primary,
            selection,
            session.get("dataset_id") or "",
            method_goal,
        )
        if proposed:
            selection = proposed
            suggestions = proposed_suggestions or suggestions
        if target:
            selection["dependent_variable"] = target
            selection.setdefault("variable_types", {})
            if spec.get("target_type"):
                selection["variable_types"][target] = spec["target_type"]
        method_state = get_method_configuration(primary, selection, session.get("dataset_id") or "", method_goal)

    session["selected_variables"] = selection
    session["selected_model"] = primary
    session["method_state"] = method_state
    session["method_suggestions"] = [_dump_model(item) for item in methods]
    _trace(session, "select_method", "根据分析目标和字段约束选择模型：{}".format(primary))

    if not method_state.get("enabled"):
        missing_roles = list(method_state.get("missing_roles") or [])
        if any(role in {"dependent_variable", "target", "outcome"} for role in missing_roles):
            session.setdefault("research_spec", {})["target_variable"] = None
            question = _target_question(dataset, suggestions, "候选模型缺少有效的结果变量。")
        elif missing_roles:
            question = _predictor_question(dataset, missing_roles)
        else:
            session.update({"status": "blocked", "stage": "analysis_blocked", "stage_label": "自动分析：方法要求未满足"})
            _save_session(req, session)
            response = AgentResponse(
                ok=False,
                action="autopilot",
                mode="autopilot",
                status="blocked",
                thread_id=session.get("thread_id"),
                message="已选择候选模型 **{}**，但它的执行要求仍未满足：{}。我没有自动降级为描述统计，请检查变量配置或改选模型。".format(primary, method_state.get("reason") or "变量配置不完整"),
                dataset_summary=dataset,
                variable_suggestions=suggestions,
                method_suggestions=methods,
                warnings=list(session.get("warnings") or []) + list(method_state.get("validation_messages") or []),
                research_spec=spec,
                workflow_state=_workflow_state(session, next_step="review_model_requirements"),
                actions=[
                    AgentAction(id="go_variables", label="检查变量配置", type="navigate", payload={"page": "upload"}, requires_confirmation=False),
                    AgentAction(id="go_models", label="选择其他模型", type="open_model_selector", payload={}, requires_confirmation=False),
                ],
                trace=[AgentTraceEvent(**item) for item in session.get("trace") or []],
            )
            return {"route": "wait", "session": session, "dataset": dataset, "response": response}

        _trace(session, "validate_method_requirements", "模型要求未满足，需要补充：{}".format("、".join(missing_roles)), "needs_clarification")
        response = _waiting_response(req, session, dataset, question, summarize_dataset_quality(dataset))
        response.variable_suggestions = suggestions
        response.method_suggestions = methods
        return {"route": "wait", "session": session, "dataset": dataset, "response": response}

    plan = {
        "research_question": research_question,
        "analysis_goal": spec.get("analysis_goal"),
        "target_variable": target,
        "model_id": primary,
        "selected_variables": selection,
        "method_reason": method_state.get("reason") or "满足当前目标和变量要求",
    }
    session["analysis_plan"] = plan
    session.update({"status": "ready_to_execute", "stage": "ready_to_run", "stage_label": "自动分析：计划已就绪"})
    _trace(session, "validate_method_requirements", "模型变量要求校验通过")
    _persist_project_analysis_state(req, session)
    _save_session(req, session)
    return {"route": "execute", "session": session, "dataset": dataset, "plan": plan, "suggestions": suggestions, "methods": methods}


def execute_autonomous_plan(req: Any, session: Dict[str, Any], dataset: Dict[str, Any]) -> AgentResponse:
    plan = session.get("analysis_plan") or {}
    model_id = plan.get("model_id") or session.get("selected_model")
    selection = plan.get("selected_variables") or session.get("selected_variables") or {}
    options = (getattr(req, "options", None) or {}).get("analysis_options") or session.get("model_options") or {}
    _trace(session, "run_analysis", "执行模型：{}".format(model_id))
    try:
        result = run_selected_analysis(
            dataset_id=session.get("dataset_id") or "",
            model_id=model_id,
            selected_variables=selection,
            options=options,
        )
    except Exception as exc:
        _trace(session, "run_analysis", "{} 执行失败：{}".format(model_id, exc), "failed")
        session.update({"status": "failed", "stage": "execution_failed", "stage_label": "自动分析：执行失败", "last_error": str(exc)})
        _save_session(req, session)
        return AgentResponse(
            ok=False,
            action="autopilot",
            mode="autopilot",
            status="failed",
            thread_id=session.get("thread_id"),
            message="分析计划已经完成，但模型 **{}** 执行失败：{}\n\n为避免产生与研究目标不一致的结果，我没有静默改用描述统计。".format(model_id, exc),
            dataset_summary=dataset,
            warnings=list(session.get("warnings") or []),
            research_spec=session.get("research_spec") or {},
            analysis_plan=plan,
            workflow_state=_workflow_state(session, next_step="review_execution_error"),
            actions=[
                AgentAction(id="go_models", label="检查模型设置", type="open_model_selector", payload={}, requires_confirmation=False),
                AgentAction(id="go_variables", label="检查变量配置", type="navigate", payload={"page": "upload"}, requires_confirmation=False),
            ],
            trace=[AgentTraceEvent(**item) for item in session.get("trace") or []],
        )

    _trace(session, "validate_result", "模型返回结构化分析结果")
    session.update({
        "status": "completed",
        "stage": "analysis_ready",
        "stage_label": "自动分析：已完成",
        "analysis_id": result.get("analysis_id"),
        "pending_questions": [],
    })
    _persist_project_analysis_state(req, session, result.get("analysis_id"))
    _save_session(req, session)
    warnings = summarize_dataset_quality(dataset) + list(session.get("warnings") or [])
    return AgentResponse(
        ok=True,
        action="autopilot",
        mode="autopilot",
        status="completed",
        thread_id=session.get("thread_id"),
        message=(
            "自动分析已完成：检查研究需求 → 验证数据字段 → 选择模型 → 配置变量 → 校验要求 → 执行分析。\n\n"
            "实际运行模型：**{}**。结果变量：**{}**。接下来可以查看结果、生成图表或报告。"
        ).format(result.get("model_name") or model_id, (session.get("research_spec") or {}).get("target_variable") or "无（描述分析）"),
        dataset_summary=dataset,
        variable_suggestions=[],
        method_suggestions=session.get("method_suggestions") or [],
        plan=[
            AgentPlanStep(step=1, title="理解需求与检查数据", detail="已完成", tool="inspect_workspace", status="completed"),
            AgentPlanStep(step=2, title="选择并验证分析方法", detail="已选择 {}".format(model_id), tool="recommend_methods", status="completed"),
            AgentPlanStep(step=3, title="执行并检查结果", detail="分析已成功返回", tool="run_analysis", status="completed"),
        ],
        execution_result=result,
        warnings=warnings,
        research_spec=session.get("research_spec") or {},
        analysis_plan=plan,
        workflow_state=_workflow_state(session, next_step="review_results", analysis_id=result.get("analysis_id")),
        actions=[
            AgentAction(id="go_results", label="查看分析结果", type="navigate", payload={"page": "results"}, requires_confirmation=False),
            AgentAction(id="go_charts", label="生成结果图表", type="navigate", payload={"page": "charts"}, requires_confirmation=False),
            AgentAction(id="go_report", label="生成分析报告", type="navigate", payload={"page": "report"}, requires_confirmation=False),
        ],
        trace=[AgentTraceEvent(**item) for item in session.get("trace") or []],
    )
