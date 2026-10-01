from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from app.schemas.report import ReportResponse
from app.services import store
from app.services.llm_report_writer import write_llm_report
from app.services.report_context_builder import (
    build_report_context,
    fmt_number,
    metric_markdown,
    safe_text,
    variable_sentence,
)

REPORTS = store.REPORTS


def _style_name(style: str) -> str:
    if style == "plain":
        return "通俗解释"
    if style == "concise":
        return "简洁摘要"
    return "学术报告"


def _chart_interpretation(chart: Dict[str, Any], variables: Optional[Dict[str, Any]] = None, project: Optional[Dict[str, Any]] = None) -> str:
    ctype = str(chart.get("chart_type") or "")
    dep = safe_text((variables or {}).get("dependent_variable"), "研究结果")
    goal = safe_text((project or {}).get("research_goal"), "当前研究问题")
    if "feature_importance" in ctype:
        return "该图用于回答模型在预测“{}”时主要依赖哪些变量。重要性越高，表示该变量对模型预测贡献越大；但它反映的是预测贡献，不等同于统计显著性或因果效应。结合“{}”，它更适合作为识别潜在关键因素和后续解释性建模的依据。".format(dep, goal)
    if "confusion" in ctype:
        return "该图用于检查分类模型的预测错误结构。通常行表示真实类别、列表示预测类别；对角线越大说明预测越准确，非对角线越大说明相应类别之间越容易混淆。它能帮助判断模型是否只擅长预测多数类，或是否对少数类识别不足。"
    if "correlation" in ctype:
        return "该图展示数值变量之间的线性相关结构。绝对相关系数越高，说明两个变量越可能共同变化；这有助于发现潜在共线性、变量聚类或后续建模需要合并/筛选的变量。相关性不等于因果关系。"
    if "coefficient" in ctype or "coef" in ctype:
        return "该图展示回归项的方向和相对大小。若模型提供标准误、置信区间或 p 值，应结合这些统计量判断不确定性；如果没有这些信息，该图只能作为方向性或描述性解释。"
    if "odds" in ctype:
        return "该图展示 Logistic 模型中的优势比。OR 大于 1 通常表示变量增加与事件发生概率升高相关，OR 小于 1 则表示负向关联；但仍不能脱离研究设计直接解释为因果效应。"
    if "model_comparison" in ctype:
        return "该图比较不同模型的预测表现。表现更高的模型通常更适合预测任务，但不自动意味着解释性更强，也不代表具备因果识别能力。"
    if "prediction" in ctype or "scatter" in ctype:
        return "该图检查预测值与真实值的贴合程度。点越接近对角线，说明预测越接近真实值；系统性偏离可能提示模型拟合不足、泛化较弱或目标变量存在难以由当前特征解释的部分。"
    if "descriptive" in ctype or "mean" in ctype:
        return "该图用于描述数值变量的均值水平。它有助于理解变量的量级和数据结构，但若变量单位不同，不能把均值高低直接解释为变量重要性或影响大小。"
    return "该图用于辅助解释当前分析结果，应结合研究目标、变量定义和模型类型进行审慎表述。"


def _chart_goal_sentence(variables: Optional[Dict[str, Any]] = None, project: Optional[Dict[str, Any]] = None) -> str:
    dep = safe_text((variables or {}).get("dependent_variable"), "目标变量")
    goal = safe_text((project or {}).get("research_goal"), "当前研究问题")
    return "结合本研究目标“{}”，该图应主要用于理解与“{}”相关的预测、关联或诊断信息，而不是单独作为因果结论。".format(goal, dep)


def _ranked_rows_to_markdown(rows: List[Dict[str, Any]], label: str, max_rows: int = 10) -> str:
    if not rows:
        return ""
    lines = ["| 排名 | {} | 数值 |".format(label), "| :--- | :--- | :--- |"]
    for i, row in enumerate(rows[:max_rows], start=1):
        name = safe_text(row.get("name"), "—")
        val = row.get("formatted") or fmt_number(row.get("value"))
        lines.append("| {} | {} | {} |".format(i, name.replace("|", "/"), val))
    return "\n".join(lines)


def _matrix_to_markdown(matrix_facts: Dict[str, Any], max_size: int = 10) -> str:
    matrix = matrix_facts.get("matrix") or []
    rows = matrix_facts.get("row_labels") or []
    cols = matrix_facts.get("column_labels") or []
    if not isinstance(matrix, list) or not matrix:
        return ""
    cols = [str(x) for x in cols[:max_size]]
    lines = ["| 真实/行 \\ 预测/列 | {} |".format(" | ".join(cols)), "| :--- | {} |".format(" | ".join([":---" for _ in cols]))]
    for i, row in enumerate(matrix[:max_size]):
        label = str(rows[i]) if i < len(rows) else str(i)
        vals = []
        for v in list(row)[:max_size]:
            vals.append(fmt_number(v))
        lines.append("| {} | {} |".format(label.replace("|", "/"), " | ".join(vals)))
    return "\n".join(lines)


def _chart_fact_for(chart: Dict[str, Any], chart_facts: List[Dict[str, Any]], index: int) -> Dict[str, Any]:
    cid = chart.get("chart_id")
    ctype = chart.get("chart_type")
    for fact in chart_facts:
        if cid and fact.get("chart_id") == cid:
            return fact
    for fact in chart_facts:
        if fact.get("figure_no") == index:
            return fact
    for fact in chart_facts:
        if ctype and fact.get("chart_type") == ctype:
            return fact
    return {}


def _chart_block(
    chart: Dict[str, Any],
    index: int,
    chart_facts: Optional[List[Dict[str, Any]]] = None,
    variables: Optional[Dict[str, Any]] = None,
    project: Optional[Dict[str, Any]] = None,
    include_metadata: bool = False,
) -> str:
    title = safe_text(chart.get("title"), "结果图表")
    subtitle = safe_text(chart.get("subtitle"), "")
    ctype = safe_text(chart.get("chart_type"), "chart")
    cid = safe_text(chart.get("chart_id"), "")
    notes = chart.get("notes") or []
    if not isinstance(notes, list):
        notes = []
    fact = _chart_fact_for(chart, chart_facts or [], index)
    dep = safe_text((variables or {}).get("dependent_variable"), "目标变量")

    parts = ["### 图 {}. {}".format(index, title), ""]
    if subtitle:
        parts.extend([subtitle, ""])
    if include_metadata:
        parts.extend(["图表类型：`{}`{}。".format(ctype, "；图表 ID：`{}`".format(cid) if cid else ""), ""])

    parts.extend(["**读图方式。** " + _chart_interpretation(chart, variables, project), ""])

    if "feature_importance" in ctype:
        ranked = fact.get("ranked_feature_importance") or []
        table = _ranked_rows_to_markdown(ranked, "变量/特征")
        if ranked:
            top = ranked[0]
            top_name = safe_text(top.get("name"), "排名第一的变量")
            top_val = top.get("formatted") or fmt_number(top.get("value"))
            parts.append("**主要发现。** 在当前模型中，{}的重要性最高（{}），说明模型在预测“{}”时较多依赖该变量的信息。排名靠前的变量可以被视为后续解释性分析或变量筛选的重点。".format(top_name, top_val, dep))
            parts.append("")
            parts.extend(["前列变量重要性如下：", "", table, ""])
        parts.append("**研究含义与边界。** 该图可以帮助判断哪些因素对分类或预测最有用，但不能说明这些变量会导致“{}”发生变化；若要写论文中的影响机制，还需要结合回归系数、稳健性检验或研究设计。".format(dep))
    elif "confusion" in ctype:
        mf = fact.get("matrix_facts") or {}
        mat = _matrix_to_markdown(mf)
        if mat:
            parts.extend(["**主要发现。** 混淆矩阵数值如下。对角线代表预测正确，非对角线代表真实类别被错误预测为其他类别。", "", mat, ""])
        if mf:
            largest = mf.get("largest_confusions") or []
            parts.append("对角线正确数合计为{}，非对角线混淆数合计为{}。".format(fmt_number(mf.get("correct_diagonal_total")), fmt_number(mf.get("off_diagonal_total"))))
            if largest:
                lg = largest[0]
                parts.append("最大的类别混淆来自真实类别“{}”被预测为“{}”（{}次），这说明模型在区分这两类时尤其需要谨慎。".format(safe_text(lg.get("true_label")), safe_text(lg.get("predicted_label")), fmt_number(lg.get("count"))))
            parts.append("")
        parts.append("**研究含义与边界。** 如果错误主要集中在少数类别或相近类别之间，报告中应避免只看总体准确率，而应说明模型对不同更新类别的识别能力并不均衡。")
    elif "correlation" in ctype:
        pairs = ((fact.get("matrix_facts") or {}).get("strongest_abs_correlations") or [])[:8]
        if pairs:
            lines = ["| 变量 1 | 变量 2 | 相关系数 |", "| :--- | :--- | :--- |"]
            for pp in pairs:
                lines.append("| {} | {} | {} |".format(safe_text(pp.get("variable_1")).replace("|", "/"), safe_text(pp.get("variable_2")).replace("|", "/"), fmt_number(pp.get("correlation"))))
            strongest = pairs[0]
            parts.append("**主要发现。** 绝对相关最高的变量对是“{}”与“{}”（r={}），提示二者在线性关系上联系较强。".format(safe_text(strongest.get("variable_1")), safe_text(strongest.get("variable_2")), fmt_number(strongest.get("correlation"))))
            parts.extend(["", "绝对相关较高的变量对如下：", "", "\n".join(lines), ""])
        parts.append("**研究含义与边界。** 对“{}”的建模而言，相关矩阵主要用于发现自变量之间的结构和潜在共线性；它不能直接说明某变量对因变量的影响方向或大小。".format(dep))
    elif "prediction" in ctype or "scatter" in ctype:
        ps = fact.get("prediction_scatter_summary") or {}
        if ps:
            parts.append("**主要发现。** 该图包含{}个预测点；由图表点估计的 MAE 为{}，RMSE 为{}。MAE 表示平均绝对误差，RMSE 对较大的预测错误更敏感，二者越小通常表示预测越接近真实值。".format(fmt_number(ps.get("n_points")), fmt_number(ps.get("mae_from_chart_points")), fmt_number(ps.get("rmse_from_chart_points"))))
            parts.append("")
        parts.append("**研究含义与边界。** 如果点云整体靠近对角线，说明模型对“{}”的数值预测较稳定；如果偏离明显，则需要检查模型设定、变量信息是否不足，或是否存在异质性样本。".format(dep))
    elif "descriptive" in ctype or "mean" in ctype:
        vals = fact.get("descriptive_values") or []
        table = _ranked_rows_to_markdown(vals, "变量")
        if vals:
            top = vals[0]
            parts.append("**主要发现。** 均值最高的变量是“{}”（{}）。这说明该变量在样本中的平均量级较高。".format(safe_text(top.get("name")), top.get("formatted") or fmt_number(top.get("value"))))
            parts.extend(["", "图中均值较高的变量如下：", "", table, ""])
        parts.append("**研究含义与边界。** 均值比较有助于了解数据结构和变量尺度，但不同单位的变量不能仅凭均值大小比较重要性；它应作为描述性背景，而不是模型结论。")
    elif "model_comparison" in ctype:
        rows = fact.get("model_comparison_values") or []
        if rows:
            parts.append("**主要发现。** 该图比较不同模型在同一任务上的表现，可用于判断哪个模型更适合预测“{}”。".format(dep))
            parts.append("")
        parts.append("**研究含义与边界。** 预测性能更好通常说明模型更适合预测任务，但不代表变量解释更清晰，也不能自动支持因果结论。")
    else:
        parts.append("**研究含义与边界。** " + _chart_goal_sentence(variables, project))

    if notes:
        note_text = "\n".join(["- {}".format(str(x)) for x in notes if str(x).strip()])
        if note_text:
            parts.extend(["", "图表备注：", note_text])
    return "\n".join(parts).strip()


def _template_content(report_ctx: Dict[str, Any], fmt: str, style: str) -> str:
    fact = report_ctx["fact_pack"]
    title = report_ctx["title"]
    charts = report_ctx["charts"]
    chart_facts = fact.get("charts") or []
    dataset = fact.get("dataset") or {}
    project = fact.get("project") or {}
    variables = fact.get("variables") or {}
    analysis = fact.get("analysis") or {}
    metrics = analysis.get("metrics") or []
    feature_importance = analysis.get("top_feature_importance") or []
    coefficients = analysis.get("top_coefficients_or_terms") or []
    guardrails = fact.get("interpretation_guardrails") or []
    ai_exp = fact.get("ai_explanation") or {}

    chart_section = "本报告暂未插入图表。建议先生成相关矩阵、变量重要性、混淆矩阵、模型对比或系数图，再重新生成报告。"
    if charts:
        chart_section = "\n\n".join([_chart_block(chart, i + 1, chart_facts, variables=variables, project=project, include_metadata=False) for i, chart in enumerate(charts)])

    goal_text = safe_text(project.get("research_goal"), "")
    goal_block = "\n研究目标：{}\n".format(goal_text) if goal_text else ""

    pieces = []
    importance_block = _ranked_rows_to_markdown(feature_importance, "变量/特征")
    coefficient_block = _ranked_rows_to_markdown(coefficients, "变量/项")
    if importance_block:
        pieces.extend(["### 3.1 变量重要性 / 预测贡献", "", importance_block, ""])
    if coefficient_block:
        pieces.extend(["### 3.2 系数 / 模型项", "", coefficient_block, ""])
    if not pieces:
        pieces.append("当前结果未提取到变量重要性或系数排序。")
    extra_result = "\n".join(pieces).strip()

    ai_text = safe_text(ai_exp.get("text"), "")
    ai_block = ""
    if ai_text:
        ai_block = "\n\n### 3.3 已有 AI 解释摘录\n\n{}".format(ai_text[:1800])

    warning_text = "\n".join(["- {}".format(x) for x in guardrails]) if guardrails else "- 当前报告没有发现额外自动警告，但仍建议结合研究设计审慎解释。"
    model_params = analysis.get("model_parameters_or_options") or {}
    model_params_text = ""
    if model_params:
        pairs = ["{}={}".format(k, v) for k, v in list(model_params.items())[:12]]
        model_params_text = "\n模型参数/选项：{}。".format("；".join(pairs))

    return """# {title}

## 1. 数据与研究设计

数据集：{dataset_name}。{dataset_size}{goal_block}
{variables}

## 2. 分析方法

当前主模型为：{model_name}。模型标识：{model_key}。报告风格：{style_name}。{model_params_text}

## 3. 主要结果

{metrics}

{extra_result}{ai_block}

## 4. 图表结果

{chart_section}

## 5. 解释与注意事项

{warnings}

以上结果应围绕研究问题、变量定义和模型假设进行解释。若使用 XGBoost、CatBoost、随机森林等机器学习模型，变量重要性主要表示预测贡献，不应直接写成因果效应。若使用回归或 Logistic 模型，应结合系数、置信区间、p 值、共线性诊断和稳健性检验进行报告。
""".format(
        title=title,
        dataset_name=safe_text(dataset.get("name"), "当前数据集"),
        dataset_size="样本量：{}；变量数：{}。".format(dataset.get("row_count"), dataset.get("column_count")) if dataset.get("row_count") or dataset.get("column_count") else "",
        goal_block=goal_block,
        variables=variable_sentence(variables),
        model_name=safe_text(analysis.get("model_name"), "当前模型"),
        model_key=safe_text(analysis.get("model_key"), "未提供"),
        style_name=_style_name(style),
        model_params_text=model_params_text,
        metrics=metric_markdown(metrics),
        extra_result=extra_result,
        ai_block=ai_block,
        chart_section=chart_section,
        warnings=warning_text,
    ).strip() + "\n"


def _append_fact_appendix(content: str, report_ctx: Dict[str, Any]) -> str:
    """Append a deterministic appendix so LLM prose remains auditable."""
    fact = report_ctx["fact_pack"]
    analysis = fact.get("analysis") or {}
    metrics = analysis.get("metrics") or []
    charts = report_ctx.get("charts") or []
    chart_facts = fact.get("charts") or []
    parts: List[str] = [content.strip(), "", "---", "", "## 附录：统计事实摘录", ""]
    parts.extend(["### 变量角色", "", variable_sentence(fact.get("variables") or {}), ""])
    parts.extend(["### 模型指标", "", metric_markdown(metrics), ""])
    fi = analysis.get("top_feature_importance") or []
    if fi:
        parts.extend(["### 变量重要性摘录", "", _ranked_rows_to_markdown(fi, "变量/特征"), ""])
    coef = analysis.get("top_coefficients_or_terms") or []
    if coef:
        parts.extend(["### 系数/模型项摘录", "", _ranked_rows_to_markdown(coef, "变量/项"), ""])
    if charts:
        parts.extend(["### 插入图表与底层数据", ""])
        for i, chart in enumerate(charts, start=1):
            parts.append(_chart_block(chart, i, chart_facts, variables=fact.get("variables") or {}, project=fact.get("project") or {}, include_metadata=True))
            parts.append("")
    return "\n".join(parts).strip() + "\n"


def generate_report(
    project_id: Optional[str],
    analysis_id: Optional[str],
    dataset_id: Optional[str] = None,
    fmt: str = "markdown",
    style: str = "academic",
    chart_ids: Optional[List[str]] = None,
    charts: Optional[List[Dict[str, Any]]] = None,
    include_charts: bool = True,
    title: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    report_mode: str = "template",
) -> ReportResponse:
    from app.services.artifact_versions import dataset_snapshot
    from app.services.store import DATASETS, ANALYSES, PROJECTS
    from datetime import datetime
    from fastapi import HTTPException
    if fmt not in ('markdown', 'docx', 'pdf'):
        raise HTTPException(422, '仅支持 Markdown、Word 和 PDF 格式。')
    dataset_id = dataset_id or (ANALYSES.get(analysis_id) or {}).get('dataset_id') if analysis_id else dataset_id or (PROJECTS.get(project_id) or {}).get('dataset_id')
    provenance = {**dataset_snapshot(DATASETS.get(dataset_id) or {}),
        'analysis_id': analysis_id, 'analysis_snapshot': (ANALYSES.get(analysis_id) or {}).get('source_snapshot') if analysis_id else None}
    report_ctx = build_report_context(
        project_id=project_id,
        analysis_id=analysis_id,
        dataset_id=dataset_id,
        chart_ids=chart_ids,
        charts=charts,
        include_charts=include_charts,
        title=title,
        style=style,
        context=context,
    )
    selected_charts = report_ctx["charts"]
    fact_pack = report_ctx["fact_pack"]
    warnings: List[str] = list(report_ctx.get("warnings") or [])

    mode = (report_mode or "template").lower().strip()
    source = "template"
    if mode in ["ai", "llm", "academic_ai"]:
        ok, llm_content, reason = write_llm_report(fact_pack, style=style)
        if ok:
            content = _append_fact_appendix(llm_content, report_ctx)
            source = "llm"
            mode = "ai"
        else:
            content = _template_content(report_ctx, fmt, style)
            source = "template_fallback"
            mode = "ai"
            warnings.append(reason)
    else:
        content = _template_content(report_ctx, fmt, style)
        mode = "template"

    report_id = "rep_{}".format(uuid.uuid4().hex[:10])
    sections = [
        {"title": "数据与研究设计", "status": "ready"},
        {"title": "分析方法", "status": "ready"},
        {"title": "主要结果", "status": "ready" if fact_pack.get("raw_available_flags", {}).get("has_analysis") else "placeholder"},
        {"title": "图表结果", "status": "ready" if selected_charts else "placeholder", "chart_count": len(selected_charts)},
        {"title": "解释与注意事项", "status": "ready"},
    ]
    if source == "llm":
        sections.append({"title": "AI 写作", "status": "ready", "backend": "DeepSeek"})
    elif source == "template_fallback":
        sections.append({"title": "AI 写作", "status": "fallback"})

    report = ReportResponse(
        report_id=report_id,
        project_id=project_id,
        analysis_id=analysis_id,
        title=report_ctx["title"],
        format=fmt,
        content=content,
        sections=sections,
        charts=selected_charts,
        download_url=f'/api/reports/{report_id}/download?format={fmt}',
        dataset_id=dataset_id,
        created_at=datetime.now().isoformat(timespec='seconds'),
        source_snapshot=provenance,
        source=source,
        report_mode=mode,
        warnings=warnings,
        fact_pack=fact_pack,
    )
    try:
        REPORTS[report_id] = report.model_dump()
    except AttributeError:
        REPORTS[report_id] = report.dict()
    from app.services.background_jobs import workspace_unchanged
    if project_id and PROJECTS.get(project_id) and workspace_unchanged():
        project = dict(PROJECTS[project_id])
        project['report_id'] = report_id
        PROJECTS[project_id] = project
        from app.services.job_progress import job_id_context
        from app.services.store import JOBS
        from app.services.artifact_versions import digest
        if job_id_context.get():
            job = JOBS.get(job_id_context.get())
            if job:
                job['workspace_fingerprint'] = digest(project)
                JOBS[job_id_context.get()] = job
    return report
