import { useState } from "react";
import { Star, ChevronRight, CheckCircle2, AlertCircle } from "lucide-react";

const methods = [
  {
    id: "mlr",
    name: "多元线性回归",
    tag: "强烈推荐",
    tagColor: "bg-primary text-primary-foreground",
    stars: 5,
    scenario: "因变量为连续型，多个连续型自变量，探索变量间线性关系",
    reason: '因变量"心理健康得分"为连续型变量，所有自变量均为连续型，样本量 n=248 满足每个预测变量至少10个样本的要求，适合进行多元线性回归。',
    assumptions: ["因变量为连续型 ✓", "自变量与因变量线性关系", "残差正态分布", "无严重多重共线性"],
    ok: true,
  },
  {
    id: "corr",
    name: "相关分析（Pearson）",
    tag: "推荐",
    tagColor: "bg-emerald-100 text-emerald-700",
    stars: 4,
    scenario: "探索两个连续型变量之间的线性相关程度",
    reason: "所有变量均为连续型，可先通过相关矩阵初步了解变量间两两关系，作为回归分析的预探索步骤。",
    assumptions: ["变量为连续型 ✓", "大致正态分布"],
    ok: true,
  },
  {
    id: "desc",
    name: "描述统计",
    tag: "基础必做",
    tagColor: "bg-blue-100 text-blue-700",
    stars: 3,
    scenario: "呈现变量的均值、标准差、最大最小值等基本统计量",
    reason: "描述统计是任何分析的首要步骤，需要在回归分析结果表之前提供样本基本特征描述，符合社科论文写作惯例。",
    assumptions: [],
    ok: true,
  },
  {
    id: "logistic",
    name: "Logistic 回归",
    tag: "不适用",
    tagColor: "bg-muted text-muted-foreground",
    stars: 1,
    scenario: "因变量为二分类变量（0/1），分析影响某事件发生概率的因素",
    reason: '当前因变量"心理健康得分"为连续型变量，不适合 Logistic 回归。如将其转化为"高/低"二分类，可考虑此方法。',
    assumptions: ["因变量为二分类 ✗"],
    ok: false,
  },
  {
    id: "anova",
    name: "单因素 ANOVA",
    tag: "备选",
    tagColor: "bg-amber-100 text-amber-700",
    stars: 2,
    scenario: "比较三组及以上在因变量上的均值差异",
    reason: "若将研究重点转为比较不同性别或年级在心理健康得分上的差异，可考虑 ANOVA 分析。目前设计以影响因素分析为主，优先选用回归。",
    assumptions: ["分组变量为分类型", "因变量为连续型"],
    ok: true,
  },
];

interface MethodsPageProps {
  onNavigate: (page: string) => void;
}

export function MethodsPage({ onNavigate }: MethodsPageProps) {
  const [selected, setSelected] = useState<string>("mlr");

  return (
    <div className="p-6 space-y-5 max-w-4xl">
      <div className="bg-secondary rounded-xl p-4 border border-primary/20 flex items-start gap-3">
        <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center shrink-0">
          <span className="text-xs text-white font-bold">AI</span>
        </div>
        <div>
          <p className="text-sm font-medium text-foreground mb-0.5">AI 方法推荐说明</p>
          <p className="text-xs text-muted-foreground">
            基于您选择的变量类型（1个连续型因变量 + 3个连续型自变量 + 2个控制变量，n=248），AI 已为您推荐最适合的统计方法，并按推荐程度从高到低排列。
          </p>
        </div>
      </div>

      <div className="space-y-3">
        {methods.map((method) => (
          <div
            key={method.id}
            onClick={() => setSelected(method.id)}
            className={`bg-card rounded-2xl border p-5 cursor-pointer transition-all ${
              selected === method.id ? "border-primary shadow-md" : "border-border hover:border-primary/30"
            }`}
          >
            <div className="flex items-start justify-between mb-2">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm font-medium text-foreground">{method.name}</span>
                <span className={`text-xs px-2 py-0.5 rounded-full ${method.tagColor}`}>{method.tag}</span>
              </div>
              <div className="flex items-center gap-0.5 ml-2 shrink-0">
                {Array.from({ length: 5 }).map((_, i) => (
                  <Star
                    key={i}
                    size={12}
                    className={i < method.stars ? "text-amber-400 fill-amber-400" : "text-border"}
                  />
                ))}
              </div>
            </div>

            <p className="text-xs text-muted-foreground mb-2">
              <span className="font-medium text-foreground">适用场景：</span>{method.scenario}
            </p>
            <p className="text-xs text-muted-foreground mb-3">
              <span className="font-medium text-foreground">推荐理由：</span>{method.reason}
            </p>

            {method.assumptions.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {method.assumptions.map((a) => (
                  <span
                    key={a}
                    className={`text-xs px-2 py-0.5 rounded-md flex items-center gap-1 ${
                      a.includes("✓") ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-600"
                    }`}
                  >
                    {a.includes("✓") ? <CheckCircle2 size={10} /> : <AlertCircle size={10} />}
                    {a}
                  </span>
                ))}
              </div>
            )}

            {selected === method.id && (
              <div className="mt-3 pt-3 border-t border-border flex items-center justify-between">
                <span className="text-xs text-primary font-medium">已选择此方法</span>
                <button
                  onClick={(e) => { e.stopPropagation(); onNavigate("results"); }}
                  className="flex items-center gap-1 text-xs bg-primary text-primary-foreground px-3 py-1.5 rounded-lg hover:opacity-90 transition-opacity"
                >
                  运行分析 <ChevronRight size={12} />
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
