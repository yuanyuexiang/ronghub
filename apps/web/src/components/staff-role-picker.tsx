"use client";
import { useEffect, useState } from "react";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
export function StaffRolePicker({
  baseRole,
  value,
  onChange,
  disabled,
}: {
  baseRole: "regulator" | "association";
  value: ApiSchemas["StaffRoleOption"] | null;
  onChange: (value: ApiSchemas["StaffRoleOption"]) => void;
  disabled: boolean;
}) {
  const [draft, setDraft] = useState(""),
    [query, setQuery] = useState({ q: "", page: 1 });
  const [data, setData] = useState<ApiSchemas["StaffRoleOptions"] | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    typedApi
      .GET("/api/v1/staff-applications/role-options", {
        params: { query: { base_role: baseRole, ...query } },
        cache: "no-store",
        signal: controller.signal,
      })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return;
        if (!data) throw new Error(apiError(error, "角色选项读取失败"));
        const last = Math.max(1, Math.ceil(data.total / 20));
        if (query.page > last) {
          setQuery((current) => ({ ...current, page: last }));
          return;
        }
        setData(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [baseRole, query]);
  return (
    <fieldset className="wide-field" disabled={disabled}>
      <legend>分配与申请身份一致的角色</legend>
      <label>
        查找角色
        <input
          aria-label="申请审核查找角色"
          value={draft}
          maxLength={100}
          onChange={(e) => setDraft(e.target.value)}
        />
      </label>
      <button
        className="outline"
        type="button"
        onClick={() => setQuery({ q: draft, page: 1 })}
      >
        筛选申请审核角色
      </button>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {loading ? (
        <p role="status">正在读取角色…</p>
      ) : (
        <div className="staff-role-options">
          {data?.items.map((row) => (
            <label key={row.id}>
              <input
                type="radio"
                name="staff-role"
                aria-label={`分配申请角色：${row.name}`}
                checked={value?.id === row.id && value?.version === row.version}
                onChange={() => onChange(row)}
              />
              {row.name} · V{row.version} · {row.permissions.length}项权限
            </label>
          ))}
        </div>
      )}
      {!loading && data?.total === 0 && (
        <p className="muted">
          暂无可下发角色，请由获授权角色管理员配置后刷新。
        </p>
      )}
      <div className="pagination">
        <button
          type="button"
          className="outline"
          disabled={loading || query.page <= 1}
          onClick={() => setQuery({ ...query, page: query.page - 1 })}
        >
          上一页审核角色
        </button>
        <span>
          第{query.page}页 · 共{data?.total ?? 0}个角色
        </span>
        <button
          type="button"
          className="outline"
          disabled={loading || !data || query.page * 20 >= data.total}
          onClick={() => setQuery({ ...query, page: query.page + 1 })}
        >
          下一页审核角色
        </button>
      </div>
      <p>
        已选：
        {value
          ? `${value.name}（V${value.version}）`
          : "请选择角色，不会自动分配管理员权限"}
      </p>
    </fieldset>
  );
}
