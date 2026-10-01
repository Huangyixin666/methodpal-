import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ReferenceLine,
} from "recharts";
import type { ChartSpec } from "../lib/api";

function fmt(value: any, digits = 4) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  if (typeof value === "number") return value.toFixed(digits).replace(/0+$/, "").replace(/\.$/, "");
  const n = Number(value);
  if (Number.isFinite(n)) return n.toFixed(digits).replace(/0+$/, "").replace(/\.$/, "");
  return String(value);
}

function numericValues(chart: ChartSpec) {
  const key = chart.y_key || chart.series?.[0]?.data_key;
  const values = (chart.data || []).map((d) => Number(d?.[key || ""])).filter((x) => Number.isFinite(x));
  return values;
}

function heatCellClass(value: any) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "bg-muted text-muted-foreground";
  if (n === 0) return "bg-muted/60 text-muted-foreground";
  const abs = Math.abs(n);
  if (abs >= 0.8) return n > 0 ? "bg-primary text-primary-foreground" : "bg-red-600 text-white";
  if (abs >= 0.5) return n > 0 ? "bg-primary/70 text-white" : "bg-red-400 text-white";
  if (abs >= 0.2) return n > 0 ? "bg-primary/25 text-foreground" : "bg-red-100 text-red-700";
  return "bg-muted/60 text-muted-foreground";
}

function ChartNotes({ chart }: { chart: ChartSpec }) {
  if (!chart.notes || chart.notes.length === 0) return null;
  return (
    <div className="mt-3 space-y-1">
      {chart.notes.map((note, i) => (
        <p key={i} className="text-xs text-muted-foreground">• {note}</p>
      ))}
    </div>
  );
}

function HeatmapChart({ chart }: { chart: ChartSpec }) {
  const matrix = chart.matrix || [];
  const rowLabels = chart.row_labels || matrix.map((_, i) => String(i));
  const colLabels = chart.column_labels || (matrix[0] || []).map((_, i: number) => String(i));
  const isConfusion = chart.chart_type.includes("confusion");

  return (
    <div className="overflow-auto rounded-xl border border-border">
      <table className="min-w-full text-xs border-collapse">
        <thead>
          <tr className="bg-muted/50">
            <th className="p-2 text-left text-muted-foreground font-medium">{isConfusion ? "真实\\预测" : "变量"}</th>
            {colLabels.map((label, i) => (
              <th key={i} className="p-2 text-center text-muted-foreground font-medium whitespace-nowrap">{label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {matrix.map((row, ri) => (
            <tr key={ri}>
              <td className="p-2 text-muted-foreground font-medium whitespace-nowrap border-t border-border">{rowLabels[ri] || ri}</td>
              {row.map((value: any, ci: number) => (
                <td key={ci} className={`p-2 text-center font-mono border-t border-border ${heatCellClass(value)}`}>
                  {fmt(value, isConfusion ? 0 : 2)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BarSpecChart({ chart }: { chart: ChartSpec }) {
  const xKey = chart.x_key || "name";
  const yKey = chart.y_key || chart.series?.[0]?.data_key || "value";
  const data = chart.data || [];
  const values = numericValues(chart);
  const hasNegative = values.some((v) => v < 0);
  const layout = chart.layout === "vertical" || data.length >= 7 ? "vertical" : "horizontal";

  if (layout === "vertical") {
    return (
      <ResponsiveContainer width="100%" height={Math.max(300, data.length * 30)}>
        <BarChart data={data} layout="vertical" margin={{ left: 16, right: 24, top: 12, bottom: 12 }}>
          <CartesianGrid strokeDasharray="3 3" horizontal={false} />
          <XAxis type="number" tick={{ fontSize: 11 }} />
          <YAxis dataKey={xKey} type="category" width={150} tick={{ fontSize: 11 }} />
          <Tooltip formatter={(v: any) => [fmt(v), chart.series?.[0]?.label || chart.y_label || yKey]} />
          {hasNegative && <ReferenceLine x={0} stroke="currentColor" strokeOpacity={0.35} />}
          <Bar dataKey={yKey} radius={[0, 6, 6, 0]} fill="currentColor" className="text-primary" />
        </BarChart>
      </ResponsiveContainer>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={320}>
      <BarChart data={data} margin={{ left: 8, right: 16, top: 12, bottom: 18 }}>
        <CartesianGrid strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey={xKey} tick={{ fontSize: 11 }} />
        <YAxis tick={{ fontSize: 11 }} />
        <Tooltip formatter={(v: any) => [fmt(v), chart.series?.[0]?.label || chart.y_label || yKey]} />
        {hasNegative && <ReferenceLine y={0} stroke="currentColor" strokeOpacity={0.35} />}
        <Bar dataKey={yKey} radius={[6, 6, 0, 0]} fill="currentColor" className="text-primary" />
      </BarChart>
    </ResponsiveContainer>
  );
}

function ScatterSpecChart({ chart }: { chart: ChartSpec }) {
  const xKey = chart.x_key || "x";
  const yKey = chart.y_key || chart.series?.[0]?.data_key || "y";
  return (
    <ResponsiveContainer width="100%" height={360}>
      <ScatterChart margin={{ left: 8, right: 16, top: 12, bottom: 18 }}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey={xKey} type="number" name={chart.x_label || xKey} tick={{ fontSize: 11 }} />
        <YAxis dataKey={yKey} type="number" name={chart.y_label || yKey} tick={{ fontSize: 11 }} />
        <Tooltip formatter={(v: any) => fmt(v)} />
        <Scatter data={chart.data || []} fill="currentColor" className="text-primary" />
      </ScatterChart>
    </ResponsiveContainer>
  );
}

export function ChartRenderer({ chart }: { chart: ChartSpec }) {
  const isHeatmap = chart.chart_type.includes("heatmap") || chart.matrix?.length;
  const isScatter = chart.chart_type.includes("scatter");

  return (
    <div className="bg-card rounded-2xl border border-border p-5 space-y-4">
      <div>
        <h3 className="text-base font-semibold text-foreground">{chart.title}</h3>
        {chart.subtitle && <p className="text-xs text-muted-foreground mt-1">{chart.subtitle}</p>}
      </div>
      <div className="min-h-[260px]">
        {isHeatmap ? <HeatmapChart chart={chart} /> : isScatter ? <ScatterSpecChart chart={chart} /> : <BarSpecChart chart={chart} />}
      </div>
      <ChartNotes chart={chart} />
    </div>
  );
}
