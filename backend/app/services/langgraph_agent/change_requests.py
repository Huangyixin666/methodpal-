"""Conservative, explicit workspace edits; unclear instructions must be clarified."""
import re
from app.services.langgraph_agent.autonomy_policy import parse_column_list, match_column

MODEL_NAMES = [
    ('有序逻辑回归', 'ordinal_logistic_regression'), ('有序logistic', 'ordinal_logistic_regression'),
    ('逻辑回归', 'logistic_regression'), ('logistic', 'logistic_regression'),
    ('线性回归', 'linear_regression'), ('ols', 'linear_regression'),
    ('随机森林分类', 'random_forest_classification'), ('随机森林回归', 'random_forest_regression'),
    ('xgboost分类', 'xgboost_classification'), ('xgb分类', 'xgboost_classification'),
    ('xgboost回归', 'xgboost_regression'), ('xgb回归', 'xgboost_regression'),
    ('岭回归', 'ridge_regression'), ('ridge', 'ridge_regression'), ('lasso', 'lasso_regression'),
    ('模型比较', 'model_comparison'), ('模型对比', 'model_comparison'),
    ('描述统计', 'descriptive_statistics'), ('相关分析', 'correlation_analysis')]

def is_change_request(text):
    return bool(re.search(r'换成|换为|改成|改为|修改|更换|去掉|删除.*变量|重新选择|改用', text or ''))

def apply_change_request(session, text, names):
    if not is_change_request(text):
        return [], None
    applied = []
    low = re.sub(r'\s+', '', text.lower())
    for label, model in MODEL_NAMES:
        if label in low:
            session['selected_model'] = model
            applied.append('模型改为 ' + model)
            break
    if not any('模型' in change for change in applied) and any(x in low for x in ('xgboost', 'xgb', '随机森林')):
        prefix = 'random_forest' if '随机森林' in low else 'xgboost'
        t = (session.get('research_spec') or {}).get('target_type')
        if t in ('二分类', '分类型', '有序型'):
            suffix = 'classification'
        elif t in ('连续型', '整数型', '计数型', '数值型'):
            suffix = 'regression'
        else:
            return applied, '请明确要使用该模型的回归版还是分类版。'
        session['selected_model'] = prefix + '_' + suffix
        applied.append('模型改为 ' + session['selected_model'])
    selection = session.setdefault('selected_variables', {})
    spec = session.setdefault('research_spec', {})
    target = re.search(r'(?:因变量|目标变量|结果变量)(?:改为|换成|改成|设为|换为|为|[:：])\s*([^，,。；;\n]+)', text)
    if target:
        name = match_column(re.split(r'并|然后', target.group(1))[0].strip(' “”…"\''), names)
        if not name:
            return applied, '没有找到指定的目标字段，请输入数据中准确的列名。'
        selection['dependent_variable'] = spec['target_variable'] = name
        spec['target_type'] = None
        applied.append('目标变量改为 ' + name)
    predictors = re.search(r'(?:自变量|预测变量|解释变量)(?:改为|换成|改成|设为|换为|[:：])\s*([^。；;\n]+)', text)
    if predictors:
        raw = re.split(r'并|然后', predictors.group(1))[0].strip()
        requested = [x.strip(' “”…"\'') for x in re.split(r'[,，、]+', raw) if x.strip()]
        found = [match_column(x, names) for x in requested]
        if not found or any(not x for x in found):
            return applied, '部分自变量没有匹配到数据字段，请用顿号或逗号列出准确列名。'
        selection['independent_variables'] = spec['predictor_variables'] = list(dict.fromkeys(found))
        applied.append('自变量改为 ' + '、'.join(found))
    removed = re.search(r'(?:去掉|删除|移除)(?:自变量|变量)?\s*([^。；;\n]+)', text)
    if removed:
        raw = re.split(r'并|然后', removed.group(1))[0].strip()
        found = parse_column_list(raw, names)
        if not found:
            return applied, '请说明要移除哪个字段。'
        for role in ('independent_variables', 'control_variables', 'mediator_variables', 'moderator_variables'):
            selection[role] = [x for x in selection.get(role, []) if x not in found]
        spec['predictor_variables'] = selection.get('independent_variables', [])
        spec['control_variables'] = selection.get('control_variables', [])
        applied.append('移除变量 ' + '、'.join(found))
    trees = re.search(r'(?:树数量|树数|n_estimators)(?:改为|设为|[:：=])\s*(\d+)', text)
    if trees:
        session.setdefault('model_options', {})['n_estimators'] = int(trees.group(1))
        applied.append('更新树数量')
    if not applied:
        return [], '请明确要修改的模型、字段或参数；数据修改请先在数据工作区完成。'
    # A change always invalidates the old plan/result; history itself is retained.
    session['analysis_id'] = None
    session['analysis_plan'] = {}
    return applied, None
