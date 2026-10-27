import React from "react";

export interface Column<T> {
  key: string;
  header: React.ReactNode;
  render?: (item: T, index: number) => React.ReactNode;
  width?: string;
  align?: "left" | "center" | "right";
  className?: string;
}

export interface DataTableProps<T> {
  columns: Column<T>[];
  data: T[];
  keyExtractor: (item: T, index: number) => string | number;
  isLoading?: boolean;
  loadingRows?: number;
  emptyMessage?: string;
  caption?: string;
  onRowClick?: (item: T) => void;
  className?: string;
}

export function DataTable<T>({
  columns,
  data,
  keyExtractor,
  isLoading = false,
  loadingRows = 3,
  emptyMessage = "No records found",
  caption,
  onRowClick,
  className = "",
}: DataTableProps<T>) {
  return (
    <div
      className={`data-table-container ${className}`}
      role="region"
      aria-label={caption || "Data table"}
      tabIndex={0}
    >
      <table className="data-table">
        {caption && <caption className="data-table-caption">{caption}</caption>}
        <thead>
          <tr>
            {columns.map((col) => (
              <th
                key={col.key}
                scope="col"
                style={{
                  width: col.width,
                  textAlign: col.align || "left",
                }}
                className={col.className}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {isLoading ? (
            Array.from({ length: loadingRows }).map((_, rIdx) => (
              <tr key={`loading-row-${rIdx}`} className="data-table-skeleton-row">
                {columns.map((col, cIdx) => (
                  <td
                    key={`skeleton-cell-${rIdx}-${cIdx}`}
                    style={{ textAlign: col.align || "left" }}
                  >
                    <div
                      className="skeleton skeleton-text"
                      style={{
                        width: cIdx === 0 ? "70%" : "50%",
                        height: "16px",
                      }}
                    />
                  </td>
                ))}
              </tr>
            ))
          ) : data.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length}
                className="data-table-empty"
              >
                {emptyMessage}
              </td>
            </tr>
          ) : (
            data.map((item, index) => {
              const isClickable = Boolean(onRowClick);
              return (
                <tr
                  key={keyExtractor(item, index)}
                  className={isClickable ? "data-table-row-clickable" : undefined}
                  onClick={isClickable ? () => onRowClick!(item) : undefined}
                  tabIndex={isClickable ? 0 : undefined}
                  role={isClickable ? "button" : undefined}
                  onKeyDown={
                    isClickable
                      ? (e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            onRowClick!(item);
                          }
                        }
                      : undefined
                  }
                >
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      style={{ textAlign: col.align || "left" }}
                      className={col.className}
                    >
                      {col.render
                        ? col.render(item, index)
                        : (item as Record<string, any>)[col.key] ?? null}
                    </td>
                  ))}
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}

export default DataTable;
