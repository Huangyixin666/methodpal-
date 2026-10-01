import { useState } from 'react';

/** Window the DOM only; every calculated value remains available by paging. */
export function PagedDataTable({title, rows, renderValue, fixedColumn}: {
  title: string; rows: Record<string, any>[]; renderValue: (value: any) => React.ReactNode; fixedColumn?: string;
}) {
  const [rowIndex, setRowIndex] = useState(0);
  const [columnIndex, setColumnIndex] = useState(0);
  const rowSize = 50, columnSize = 12;
  const allColumns = Object.keys(rows[0] || {});
  const fixed = fixedColumn && allColumns.includes(fixedColumn) ? [fixedColumn] : [];
  const columns = allColumns.filter(c => !fixed.includes(c));
  const rowPages = Math.max(1, Math.ceil(rows.length / rowSize));
  const columnPages = Math.max(1, Math.ceil(columns.length / columnSize));
  const rowPage = Math.min(rowIndex, rowPages - 1), columnPage = Math.min(columnIndex, columnPages - 1);
  const visibleColumns = [...fixed, ...columns.slice(columnPage * columnSize, (columnPage + 1) * columnSize)];
  const start = rowPage * rowSize;
  return <section className="bg-card rounded-2xl border border-border overflow-hidden" aria-label={title}>
    <div className="p-4 border-b border-border flex flex-wrap justify-between gap-3">
      <h3 className="text-foreground">{title}</h3>
      <div className="text-xs text-muted-foreground flex flex-wrap items-center gap-3">
        <span>显示 {rows.length ? start + 1 : 0}–{Math.min(start + rowSize, rows.length)} / {rows.length} 行</span>
        {rowPages > 1 && <>
          <button aria-label={`${title}上一页`} disabled={rowPage === 0} onClick={()=>setRowIndex(rowPage - 1)} className="text-primary disabled:opacity-40">上一页</button>
          <button aria-label={`${title}下一页`} disabled={rowPage === rowPages - 1} onClick={()=>setRowIndex(rowPage + 1)} className="text-primary disabled:opacity-40">下一页</button>
        </>}
        {columnPages > 1 && <>
          <span>列组 {columnPage + 1} / {columnPages}</span>
          <button aria-label={`${title}上一组列`} disabled={columnPage === 0} onClick={()=>setColumnIndex(columnPage - 1)} className="text-primary disabled:opacity-40">上一组列</button>
          <button aria-label={`${title}下一组列`} disabled={columnPage === columnPages - 1} onClick={()=>setColumnIndex(columnPage + 1)} className="text-primary disabled:opacity-40">下一组列</button>
        </>}
      </div>
    </div>
    <div className="overflow-x-auto"><table className="w-full text-sm">
      <thead><tr className="bg-muted/50 text-xs text-muted-foreground text-left">{visibleColumns.map(key=><th key={key} className="py-2.5 px-3 font-medium">{key}</th>)}</tr></thead>
      <tbody className="divide-y divide-border">{rows.slice(start, start + rowSize).map((row, index)=><tr key={start + index}>{visibleColumns.map(key=><td key={key} className="py-2.5 px-3 font-mono text-muted-foreground">{renderValue(row[key])}</td>)}</tr>)}</tbody>
    </table></div>
  </section>;
}
