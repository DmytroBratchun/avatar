import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";

function ResultTable({ columns, rows }: { columns: string[]; rows: Record<string, unknown>[] }) {
  if (rows.length === 0) return <p className="hint">No rows.</p>;
  return (
    <div style={{ overflowX: "auto" }}>
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((c) => {
                const val = row[c];
                const display = val === null || val === undefined ? "" : typeof val === "object" ? JSON.stringify(val) : String(val);
                return (
                  <td key={c} style={{ maxWidth: "320px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={display}>
                    {display}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function DatabasePage() {
  const { data: tables } = useQuery({ queryKey: ["dbTables"], queryFn: api.listTables });
  const [selectedTable, setSelectedTable] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const limit = 50;

  const { data: tableData, isFetching: tableFetching } = useQuery({
    queryKey: ["dbBrowse", selectedTable, offset],
    queryFn: () => api.browseTable(selectedTable!, limit, offset),
    enabled: !!selectedTable,
  });

  const [sql, setSql] = useState("SELECT id, book, episode_number, status, caption_cost_usd FROM sources ORDER BY id");
  const queryMutation = useMutation({ mutationFn: () => api.runQuery(sql) });

  return (
    <div>
      <h1>Database</h1>
      <p className="hint" style={{ marginTop: "-0.5rem" }}>
        Read-only view into <code>backend/data/avatar.db</code>. Browse a table, or run a custom SELECT.
      </p>

      <div className="card">
        <h3>Browse table</h3>
        <div className="field-row">
          <select
            value={selectedTable ?? ""}
            onChange={(e) => {
              setSelectedTable(e.target.value || null);
              setOffset(0);
            }}
          >
            <option value="">Select a table…</option>
            {tables?.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>

        {tableFetching && <p>Loading…</p>}
        {tableData && (
          <>
            <p className="hint">
              {tableData.total} row{tableData.total === 1 ? "" : "s"} total · showing {offset + 1}-
              {Math.min(offset + limit, tableData.total)}
            </p>
            <ResultTable columns={tableData.columns} rows={tableData.rows} />
            <div className="row-actions" style={{ justifyContent: "flex-start", marginTop: "0.6rem" }}>
              <button className="small ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>
                ← Prev
              </button>
              <button
                className="small ghost"
                disabled={offset + limit >= tableData.total}
                onClick={() => setOffset(offset + limit)}
              >
                Next →
              </button>
            </div>
          </>
        )}
      </div>

      <div className="card" style={{ marginTop: "1rem" }}>
        <h3>Custom query</h3>
        <textarea
          style={{ fontFamily: "var(--font-mono)", fontSize: "0.8rem", minHeight: "70px" }}
          value={sql}
          onChange={(e) => setSql(e.target.value)}
        />
        <div style={{ marginTop: "0.5rem" }}>
          <button className="primary" onClick={() => queryMutation.mutate()} disabled={queryMutation.isPending}>
            {queryMutation.isPending ? "Running…" : "Run query"}
          </button>
        </div>
        {queryMutation.isError && <p className="error-text">{(queryMutation.error as Error).message}</p>}
        {queryMutation.data && (
          <div style={{ marginTop: "0.75rem" }}>
            {queryMutation.data.truncated && <p className="hint">Truncated to 500 rows.</p>}
            <ResultTable columns={queryMutation.data.columns} rows={queryMutation.data.rows} />
          </div>
        )}
      </div>
    </div>
  );
}
