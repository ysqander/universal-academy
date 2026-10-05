import type { VizTable } from "../../types";
import { Markdown } from "../Markdown";

/** Two (or more) easily confused ideas side by side. */
export function ComparisonTable({ table }: { table: VizTable }) {
  return (
    <div className="viz-table-wrap">
      <table className="viz-table">
        {table.headers.length > 0 && (
          <thead>
            <tr>
              {table.headers.map((header, i) => (
                <th key={i} scope="col">
                  <Markdown inline>{header}</Markdown>
                </th>
              ))}
            </tr>
          </thead>
        )}
        <tbody>
          {table.rows.map((row, r) => (
            <tr key={r}>
              {row.map((cell, c) =>
                c === 0 && table.headers.length > 0 ? (
                  <th key={c} scope="row">
                    <Markdown inline>{cell}</Markdown>
                  </th>
                ) : (
                  <td key={c}>
                    <Markdown inline>{cell}</Markdown>
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
