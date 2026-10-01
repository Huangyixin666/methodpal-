import type {
  DatasetProfileColumn,
  MethodRoleRequirement,
  VariableSelection,
  VariableType,
} from "./api";

export const VARIABLE_TYPE_OPTIONS: VariableType[] = [
  "ID型",
  "连续型",
  "整数型",
  "计数型",
  "分类型",
  "二分类",
  "有序型",
  "文本型",
  "未知",
];

export const ANALYTICAL_ROLE_FIELDS = [
  "dependent_variable",
  "independent_variables",
  "control_variables",
  "mediator_variables",
  "moderator_variables",
  "group_variable",
] as const;

export const LIST_ROLE_FIELDS = new Set<string>([
  "independent_variables",
  "control_variables",
  "mediator_variables",
  "moderator_variables",
]);

export function asVariableType(value?: string | null): VariableType {
  if (!value) return "未知";
  return (VARIABLE_TYPE_OPTIONS as string[]).includes(value)
    ? (value as VariableType)
    : "未知";
}

export function datasetColumns(
  headers: string[],
  profileColumns: DatasetProfileColumn[] = [],
): DatasetProfileColumn[] {
  const profileByName = new Map(
    profileColumns.map((column) => [column.name, column]),
  );
  return headers.map(
    (name) =>
      profileByName.get(name) || {
        name,
        type: "未知",
        missing_count: 0,
        missing_rate: 0,
        unique_count: 0,
      },
  );
}

function uniqueValid(
  values: string[] | undefined,
  valid: Set<string>,
): string[] {
  return Array.from(
    new Set((values || []).filter((value) => valid.has(value))),
  );
}

export function normalizeVariableSelection(
  columns: DatasetProfileColumn[],
  previous?: VariableSelection | null,
): VariableSelection {
  const valid = new Set(columns.map((column) => column.name));
  const dependent =
    previous?.dependent_variable && valid.has(previous.dependent_variable)
      ? previous.dependent_variable
      : "";
  const group =
    previous?.group_variable &&
    valid.has(previous.group_variable) &&
    previous.group_variable !== dependent
      ? previous.group_variable
      : null;

  const variableTypes: Record<string, VariableType | string> = {};
  columns.forEach((column) => {
    variableTypes[column.name] =
      previous?.variable_types?.[column.name] || asVariableType(column.type);
  });

  const alreadyUsed = new Set<string>();
  if (dependent) alreadyUsed.add(dependent);
  if (group) alreadyUsed.add(group);
  const takeList = (values?: string[]) =>
    uniqueValid(values, valid).filter((value) => {
      if (alreadyUsed.has(value)) return false;
      alreadyUsed.add(value);
      return true;
    });

  return {
    dependent_variable: dependent,
    independent_variables: takeList(previous?.independent_variables),
    control_variables: takeList(previous?.control_variables),
    mediator_variables: takeList(previous?.mediator_variables),
    moderator_variables: takeList(previous?.moderator_variables),
    group_variable: group,
    variable_types: variableTypes,
    analysis_goal: previous?.analysis_goal || "",
  };
}

export function typesOnlySelection(
  selection?: VariableSelection | null,
): VariableSelection {
  return {
    dependent_variable: "",
    independent_variables: [],
    control_variables: [],
    mediator_variables: [],
    moderator_variables: [],
    group_variable: null,
    variable_types: { ...(selection?.variable_types || {}) },
    analysis_goal: selection?.analysis_goal || "",
  };
}

export function roleValues(
  selection: VariableSelection,
  role: string,
): string[] {
  if (LIST_ROLE_FIELDS.has(role))
    return ((selection as any)[role] || []) as string[];
  const value = (selection as any)[role];
  return value ? [String(value)] : [];
}

export function roleRequirements(
  required: MethodRoleRequirement[] = [],
  optional: MethodRoleRequirement[] = [],
): MethodRoleRequirement[] {
  const seen = new Set<string>();
  return [...required, ...optional].filter((item) => {
    if (!item?.role || seen.has(item.role)) return false;
    seen.add(item.role);
    return true;
  });
}

export function roleForColumn(
  selection: VariableSelection,
  requirements: MethodRoleRequirement[],
  columnName: string,
): string {
  return (
    requirements.find((requirement) =>
      roleValues(selection, requirement.role).includes(columnName),
    )?.role || ""
  );
}

export function assignColumnRole(
  selection: VariableSelection,
  role: string,
  columnName: string,
): VariableSelection {
  const next: any = {
    ...selection,
    independent_variables: [...(selection.independent_variables || [])],
    control_variables: [...(selection.control_variables || [])],
    mediator_variables: [...(selection.mediator_variables || [])],
    moderator_variables: [...(selection.moderator_variables || [])],
  };

  ANALYTICAL_ROLE_FIELDS.forEach((field) => {
    if (LIST_ROLE_FIELDS.has(field))
      next[field] = (next[field] || []).filter(
        (value: string) => value !== columnName,
      );
    else if (next[field] === columnName)
      next[field] = field === "group_variable" ? null : "";
  });

  if (!role) return next as VariableSelection;
  if (LIST_ROLE_FIELDS.has(role))
    next[role] = Array.from(new Set([...(next[role] || []), columnName]));
  else next[role] = columnName;
  return next as VariableSelection;
}

export function requirementAllowsType(
  requirement: MethodRoleRequirement,
  type: string,
): boolean {
  return (
    !requirement.allowed_types?.length ||
    requirement.allowed_types.includes(type)
  );
}

export function hasAnyAnalyticalRole(
  selection?: VariableSelection | null,
): boolean {
  return Boolean(
    selection?.dependent_variable ||
    selection?.group_variable ||
    selection?.independent_variables?.length ||
    selection?.control_variables?.length ||
    selection?.mediator_variables?.length ||
    selection?.moderator_variables?.length,
  );
}
export function compatibleSelectionForRequirements(
  columns: DatasetProfileColumn[],
  selection: VariableSelection | null | undefined,
  requirements: MethodRoleRequirement[],
): VariableSelection {
  let next = typesOnlySelection(selection);
  requirements.forEach((requirement) => {
    const candidates = roleValues(
      selection || typesOnlySelection(null),
      requirement.role,
    );
    const maxCount = requirement.max_count ?? candidates.length;
    candidates.slice(0, maxCount).forEach((columnName) => {
      const type = String(next.variable_types?.[columnName] || "未知");
      if (requirementAllowsType(requirement, type)) {
        next = assignColumnRole(next, requirement.role, columnName);
      }
    });
  });
  return normalizeVariableSelection(columns, next);
}
