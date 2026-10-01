import React from "react";

type MarkdownViewProps = {
  content?: string | null;
  className?: string;
};

type Block =
  | { type: "heading"; level: number; text: string }
  | { type: "paragraph"; text: string }
  | { type: "ul"; items: string[] }
  | { type: "ol"; items: string[] }
  | { type: "table"; headers: string[]; rows: string[][] };

function renderInline(text: string): React.ReactNode[] {
  const tokens = String(text || "")
    .split(/(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g)
    .filter(Boolean);

  return tokens.map((token, i) => {
    if (token.startsWith("**") && token.endsWith("**")) {
      return (
        <strong key={i} className="font-semibold text-foreground">
          {token.slice(2, -2)}
        </strong>
      );
    }
    if (token.startsWith("`") && token.endsWith("`")) {
      return (
        <code key={i} className="px-1.5 py-0.5 rounded bg-muted border border-border font-mono text-[0.92em]">
          {token.slice(1, -1)}
        </code>
      );
    }
    if (token.startsWith("*") && token.endsWith("*") && token.length > 2) {
      return (
        <em key={i} className="italic">
          {token.slice(1, -1)}
        </em>
      );
    }
    return <React.Fragment key={i}>{token}</React.Fragment>;
  });
}

function looksLikeTableRow(line: string): boolean {
  const trimmed = line.trim();
  return trimmed.startsWith("|") && trimmed.endsWith("|") && trimmed.split("|").length >= 4;
}

function looksLikeSeparatorRow(line: string): boolean {
  const cells = splitTableRow(line);
  if (!cells.length) return false;
  return cells.every((cell) => /^:?-{3,}:?$/.test(cell.trim()));
}

function splitTableRow(line: string): string[] {
  let trimmed = String(line || "").trim();
  if (trimmed.startsWith("|")) trimmed = trimmed.slice(1);
  if (trimmed.endsWith("|")) trimmed = trimmed.slice(0, -1);
  return trimmed.split("|").map((cell) => cell.trim());
}

/**
 * DeepSeek / LLMs often output tables with blank lines between every row:
 *
 * | A | B |
 *
 * |---|---|
 *
 * | 1 | 2 |
 *
 * Standard Markdown treats those as separate paragraphs, so the table breaks.
 * This preprocessor only removes blank lines when they are between table rows.
 */
function normalizeMarkdownTables(raw: string): string {
  const lines = String(raw || "").replace(/\r\n/g, "\n").split("\n");
  const out: string[] = [];

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      const prev = out.length ? out[out.length - 1] : "";
      let j = i + 1;
      while (j < lines.length && !lines[j].trim()) j += 1;
      const next = j < lines.length ? lines[j] : "";

      if (looksLikeTableRow(prev) && looksLikeTableRow(next)) {
        continue;
      }
    }

    out.push(line);
  }

  return out.join("\n");
}

function parseMarkdown(raw: string): Block[] {
  const normalized = normalizeMarkdownTables(raw || "").trim();
  if (!normalized) return [];

  const lines = normalized.split("\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let listType: "ul" | "ol" | null = null;
  let listItems: string[] = [];

  function flushParagraph() {
    if (paragraph.length) {
      blocks.push({ type: "paragraph", text: paragraph.join("\n") });
      paragraph = [];
    }
  }

  function flushList() {
    if (listType && listItems.length) {
      blocks.push({ type: listType, items: listItems });
    }
    listType = null;
    listItems = [];
  }

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      flushParagraph();
      flushList();
      i += 1;
      continue;
    }

    // GitHub-flavored Markdown table: header row + separator row + body rows.
    if (
      looksLikeTableRow(trimmed) &&
      i + 1 < lines.length &&
      looksLikeTableRow(lines[i + 1]) &&
      looksLikeSeparatorRow(lines[i + 1])
    ) {
      flushParagraph();
      flushList();

      const headers = splitTableRow(trimmed);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && looksLikeTableRow(lines[i])) {
        const row = splitTableRow(lines[i]);
        const padded = headers.map((_, idx) => row[idx] ?? "");
        rows.push(padded);
        i += 1;
      }
      blocks.push({ type: "table", headers, rows });
      continue;
    }

    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      blocks.push({ type: "heading", level: heading[1].length, text: heading[2] });
      i += 1;
      continue;
    }

    const bullet = trimmed.match(/^[-*•]\s+(.+)$/);
    if (bullet) {
      flushParagraph();
      if (listType !== "ul") flushList();
      listType = "ul";
      listItems.push(bullet[1]);
      i += 1;
      continue;
    }

    const numbered = trimmed.match(/^\d+[.)]\s+(.+)$/);
    if (numbered) {
      flushParagraph();
      if (listType !== "ol") flushList();
      listType = "ol";
      listItems.push(numbered[1]);
      i += 1;
      continue;
    }

    flushList();
    paragraph.push(trimmed);
    i += 1;
  }

  flushParagraph();
  flushList();
  return blocks;
}

export function MarkdownView({ content, className = "" }: MarkdownViewProps) {
  const blocks = parseMarkdown(content || "");

  if (!blocks.length) {
    return <div className={className} />;
  }

  return (
    <div className={`space-y-4 text-sm leading-7 text-foreground ${className}`}>
      {blocks.map((block, i) => {
        if (block.type === "heading") {
          const sizeClass = block.level <= 2 ? "text-base" : "text-sm";
          return (
            <h3 key={i} className={`${sizeClass} font-semibold text-foreground mt-1 mb-1`}>
              {renderInline(block.text)}
            </h3>
          );
        }

        if (block.type === "ul") {
          return (
            <ul key={i} className="list-disc pl-5 space-y-1.5">
              {block.items.map((item, j) => (
                <li key={j}>{renderInline(item)}</li>
              ))}
            </ul>
          );
        }

        if (block.type === "ol") {
          return (
            <ol key={i} className="list-decimal pl-5 space-y-1.5">
              {block.items.map((item, j) => (
                <li key={j}>{renderInline(item)}</li>
              ))}
            </ol>
          );
        }

        if (block.type === "table") {
          return (
            <div key={i} className="overflow-x-auto rounded-2xl border border-border bg-background/70">
              <table className="min-w-full border-collapse text-left text-sm">
                <thead className="bg-muted/70">
                  <tr>
                    {block.headers.map((header, j) => (
                      <th key={j} className="px-4 py-2.5 font-semibold text-foreground border-b border-border whitespace-nowrap">
                        {renderInline(header)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {block.rows.map((row, r) => (
                    <tr key={r} className="border-b border-border/60 last:border-b-0">
                      {block.headers.map((_, c) => (
                        <td key={c} className="px-4 py-2.5 align-top text-muted-foreground">
                          {renderInline(row[c] ?? "")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }

        return (
          <p key={i} className="whitespace-pre-line">
            {renderInline(block.text)}
          </p>
        );
      })}
    </div>
  );
}

export default MarkdownView;
