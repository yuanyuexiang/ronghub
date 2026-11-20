"use client";
import {
  WorkspaceViews,
  useWorkspaceLeave,
  type LeaveGuardRegistrar,
} from "./business-workspace";
import { usePageRefresh } from "./page-refresh";
import { AdminPasswordReset } from "./password-panel";
import { DistrictScopeEditor } from "./district-scope-editor";
import { accountApi } from "../lib/account-api";
import type { ApiSchemas } from "../lib/typed-api";
import { useEffect, useState, useRef } from "react";
import { useCapability, useGlobalScope, useDistrictScope } from "./permissions";
type Profile = ApiSchemas["RoleProfileResponse"];
type Account = ApiSchemas["ManagedAccountResponse"];
const permissionModules: Record<string, string> = {
  stations: "场站",
  applications: "接入申请",
  subsidy: "补贴",
  ratings: "星级评价",
  hazards: "隐患督办",
  inspections: "巡检",
  alerts: "告警",
  content: "信息发布",
  companies: "企业档案",
  orders: "充电订单",
  statistics: "统计",
  accounts: "账号",
  roles: "角色",
  audits: "审计",
  sync: "同步",
  registrations: "企业注册",
  staff_applications: "工作人员",
};
const roleNames: Record<string, string> = {
  regulator: "监管",
  association: "协会",
  operator: "运营商",
};
export function AuthorizationPanel({
  csrf,
  username,
  currentProfileId,
  permissions,
  registerLeaveGuard,
}: {
  csrf: string;
  username: string;
  currentProfileId: number | null;
  permissions: string[];
  registerLeaveGuard?: LeaveGuardRegistrar;
}) {
  const globalScope = useGlobalScope();
  const ownScope = useDistrictScope();
  const [accountScope, setAccountScope] = useState<string[] | null>(null),
    [newScope, setNewScope] = useState<string[] | null>(ownScope);
  const canRoles = useCapability("roles.manage"),
    canAccounts = useCapability("accounts.manage");
  const [profiles, setProfiles] = useState<Profile[]>([]),
    [catalog, setCatalog] = useState<{ code: string; name: string }[]>([]),
    [bases, setBases] = useState<Record<string, string[]>>({}),
    [users, setUsers] = useState<Account[]>([]),
    [operators, setOperators] = useState<{ id: number; name: string }[]>([]),
    [page, setPage] = useState(1),
    [total, setTotal] = useState(0),
    [q, setQ] = useState(""),
    [profile, setProfile] = useState<Profile | null>(null),
    [showRole, setShowRole] = useState(false),
    [roleName, setRoleName] = useState(""),
    [base, setBase] = useState("operator"),
    [grants, setGrants] = useState<string[]>(["stations.read"]),
    [account, setAccount] = useState<Account | null>(null),
    [chosenRole, setChosenRole] = useState(""),
    [enabled, setEnabled] = useState(true),
    [reason, setReason] = useState(""),
    [showNew, setShowNew] = useState(false),
    [newName, setNewName] = useState(""),
    [newPassword, setNewPassword] = useState(""),
    [newRole, setNewRole] = useState(""),
    [newOperator, setNewOperator] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [view, setView] = useState(canAccounts ? "accounts" : "roles");
  const roleDirty =
    showRole &&
    (roleName !== (profile?.name || "") ||
      base !== (profile?.base_role || "operator") ||
      JSON.stringify([...grants].sort()) !==
        JSON.stringify(
          [...(profile?.permissions || ["stations.read"])].sort(),
        ));
  const accountDirty =
    !!account &&
    (chosenRole !== String(account.role_profile_id ?? "") ||
      enabled !== account.enabled ||
      !!reason.trim() ||
      JSON.stringify(accountScope) !==
        JSON.stringify(account.allowed_districts));
  const newDirty =
    showNew &&
    (!!(newName || newPassword || newRole || newOperator) ||
      JSON.stringify(newScope) !== JSON.stringify(ownScope));
  const canLeave = useWorkspaceLeave(
    roleDirty || accountDirty || newDirty,
    busy,
    registerLeaveGuard,
  );
  const [listLoading, setListLoading] = useState(true);
  const api = accountApi(csrf);
  const appliedListQuery = useRef({ q: "" });
  const loadSequence = useRef(0);
  async function load() {
    const generation = ++loadSequence.current;
    setListLoading(true);
    try {
      const data = await api.catalog(canRoles);
      if (generation !== loadSequence.current) return;
      setProfiles(data.items);
      setCatalog(data.catalog || []);
      setBases(data.base_roles || {});
      if (canAccounts) {
        const result = await api.list({ ...appliedListQuery.current, page });
        if (generation !== loadSequence.current) return;
        setUsers(result.items);
        setTotal(result.total);
      }
    } catch (error) {
      if (generation === loadSequence.current) throw error;
    } finally {
      if (generation === loadSequence.current) setListLoading(false);
    }
  }
  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }
  const pageRefreshRevision = usePageRefresh();
  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [page, pageRefreshRevision]);
  useEffect(() => {
    if (canAccounts)
      api
        .operators()
        .then((d) => setOperators(d.items))
        .catch((e) => setError(e.message));
  }, [canAccounts, pageRefreshRevision]);
  function startRole(p: Profile | null) {
    setProfile(p);
    setRoleName(p?.name || "");
    setBase(p?.base_role || "operator");
    setGrants(p?.permissions || ["stations.read"]);
    setShowRole(true);
  }
  const selectedNew = profiles.find((p) => String(p.id) === newRole);
  return (
    <div
      className="business-stack authorization-workspace"
      aria-busy={listLoading}
    >
      {listLoading && (
        <p role="status" className="muted">
          正在读取角色与账号…
        </p>
      )}
      <WorkspaceViews
        label="权限管理视图"
        value={view}
        items={[
          ...(canRoles ? [["roles", "角色管理"] as const] : []),
          ...(canAccounts ? [["accounts", "账号管理"] as const] : []),
        ]}
        disabled={busy}
        onChange={(next) => {
          if (canLeave()) {
            setShowRole(false);
            setShowNew(false);
            setAccount(null);
            setView(next);
          }
        }}
      />
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {canRoles && (
        <section
          className="panel"
          hidden={view === "accounts" || showNew || !!account}
        >
          <div className="panel-title">
            <div>
              <h2>业务权限角色</h2>
              <p>
                在三类基础身份内分配模块查看与办理权限，只能下发自身拥有的权限；运营商始终限制在所属企业，数据区域取企业归属与账号区域授权的交集
              </p>
            </div>
            <button
              className="outline"
              disabled={!globalScope || busy || showRole}
              onClick={() => startRole(null)}
            >
              新建权限角色
            </button>
            {!globalScope && (
              <p className="muted">
                区域账号可引用共享角色，不能修改全域共享定义。
              </p>
            )}
          </div>
          {showRole && globalScope && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                run(async () => {
                  const body = {
                    name: roleName,
                    base_role: base,
                    permissions: grants,
                  };
                  if (profile)
                    await api.editRole(profile.id, {
                      ...body,
                      version: profile.version,
                    });
                  else await api.createRole(body);
                  setShowRole(false);
                  await load();
                });
              }}
            >
              <div className="business-form">
                <label>
                  角色名称
                  <input
                    aria-label="权限角色名称"
                    value={roleName}
                    onChange={(e) => setRoleName(e.target.value)}
                    maxLength={100}
                    required
                    disabled={busy}
                  />
                </label>
                <label>
                  基础身份
                  <select
                    aria-label="权限基础身份"
                    value={base}
                    disabled={busy || !!profile}
                    onChange={(e) => {
                      setBase(e.target.value);
                      setGrants(["stations.read"]);
                    }}
                  >
                    {Object.entries(roleNames).map(([key, value]) => (
                      <option key={key} value={key}>
                        {value}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="permission-groups">
                {Object.entries(
                  catalog
                    .filter((c) => bases[base]?.includes(c.code))
                    .reduce<Record<string, typeof catalog>>(
                      (groups, capability) => {
                        const module = capability.code.split(".")[0];
                        (groups[module] ??= []).push(capability);
                        return groups;
                      },
                      {},
                    ),
                ).map(([module, capabilities]) => (
                  <fieldset className="permission-group" key={module}>
                    <legend>{permissionModules[module] ?? "其他业务"}</legend>
                    <div className="capability-grid">
                      {capabilities.map((c) => (
                        <label key={c.code}>
                          <input
                            aria-label={`权限-${c.code}`}
                            type="checkbox"
                            checked={grants.includes(c.code)}
                            disabled={busy || c.code === "stations.read"}
                            onChange={(e) =>
                              setGrants(
                                e.target.checked
                                  ? [...grants, c.code]
                                  : grants.filter((k) => k !== c.code),
                              )
                            }
                          />
                          {c.name}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                ))}
              </div>
              <div className="business-form">
                <p className="muted">
                  办理、回复、启停及导出需同时授予对应查看权限。修改角色将撤销使用该角色的账号会话。
                </p>
                <button className="outline" disabled={busy}>
                  保存权限角色
                </button>
                <button
                  className="outline"
                  type="button"
                  onClick={() => {
                    if (canLeave()) setShowRole(false);
                  }}
                >
                  取消角色编辑
                </button>
              </div>
            </form>
          )}
          <div className="table-wrap" hidden={showRole}>
            <table>
              <thead>
                <tr>
                  <th>角色名称</th>
                  <th>基础身份</th>
                  <th>权限</th>
                  <th>版本</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {profiles.map((p) => (
                  <tr key={p.id}>
                    <td>{p.name}</td>
                    <td>{roleNames[p.base_role]}</td>
                    <td className="permission-description">
                      {p.permissions
                        .map(
                          (k) => catalog.find((c) => c.code === k)?.name || k,
                        )
                        .join("、")}
                    </td>
                    <td>V{p.version}</td>
                    <td>
                      <button
                        className="outline"
                        disabled={
                          busy || !globalScope || p.id === currentProfileId
                        }
                        onClick={() => startRole(p)}
                      >
                        编辑权限角色
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!listLoading && !profiles.length && (
              <p className="empty">暂无自定义角色，现有账号按基础身份授权</p>
            )}
          </div>
        </section>
      )}
      {canAccounts && (
        <section className="panel" hidden={view === "roles" || showRole}>
          <div className="panel-title">
            <div>
              <h2>账号管理</h2>
              <p>
                调整授权或启停后撤销原会话；不能更改自身账号，也不能维护权限高于自身的账号
              </p>
            </div>
            <div className="action-row" hidden={showNew || !!account}>
              <form
                className="search"
                onSubmit={(e) => {
                  e.preventDefault();
                  appliedListQuery.current = { q };
                  if (page !== 1) setPage(1);
                  else run(load);
                }}
              >
                <input
                  aria-label="搜索账号"
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="账号名称"
                  maxLength={100}
                />
                <button disabled={busy}>查询账号</button>
              </form>
              <button
                className="outline"
                disabled={busy}
                onClick={() => {
                  if (canLeave()) {
                    setAccount(null);
                    setShowNew(!showNew);
                  }
                }}
              >
                新建账号
              </button>
            </div>
          </div>
          {(showNew || account) && (
            <div className="panel-title">
              <h2>
                {showNew ? "新建业务账号" : `${account?.username} · 账号授权`}
              </h2>
              <button
                type="button"
                className="outline"
                disabled={busy}
                onClick={() => {
                  if (canLeave()) {
                    setAccount(null);
                    setShowNew(false);
                    setNewPassword("");
                  }
                }}
              >
                返回账号列表
              </button>
            </div>
          )}
          {showNew && (
            <form
              className="business-form"
              onSubmit={(e) => {
                e.preventDefault();
                run(async () => {
                  await api.create({
                    username: newName,
                    password: newPassword,
                    allowed_districts: newScope,
                    role_profile_id: Number(newRole),
                    operator_id:
                      selectedNew?.base_role === "operator"
                        ? Number(newOperator)
                        : null,
                  });
                  setNewName("");
                  setNewPassword("");
                  setShowNew(false);
                  await load();
                });
              }}
            >
              <label>
                账号名称
                <input
                  aria-label="新建账号名称"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  minLength={3}
                  maxLength={100}
                  required
                  autoComplete="off"
                />
              </label>
              <label>
                初始临时密码
                <input
                  aria-label="新建账号密码"
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  minLength={12}
                  maxLength={200}
                  required
                  autoComplete="new-password"
                />
                <small className="muted">
                  至少12个字符。新账号首次登录必须修改临时密码，改密后重新登录。
                </small>
              </label>
              <label>
                配置角色
                <select
                  aria-label="新建账号角色"
                  value={newRole}
                  required
                  onChange={(e) => {
                    setNewRole(e.target.value);
                    setNewOperator("");
                  }}
                >
                  <option value="">请选择角色</option>
                  {profiles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}（{roleNames[p.base_role]}）
                    </option>
                  ))}
                </select>
              </label>
              {selectedNew?.base_role === "operator" && (
                <label>
                  所属企业
                  <select
                    aria-label="新建账号企业"
                    value={newOperator}
                    required
                    onChange={(e) => setNewOperator(e.target.value)}
                  >
                    <option value="">请选择企业</option>
                    {operators.map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <DistrictScopeEditor
                label="新建账号"
                disabled={busy}
                value={newScope}
                onChange={setNewScope}
              />
              <button className="outline" disabled={busy || !newRole}>
                创建账号
              </button>
            </form>
          )}
          <div className="table-wrap" hidden={showNew || !!account}>
            <table>
              <thead>
                <tr>
                  {[
                    "账号",
                    "基础身份",
                    "所属企业",
                    "权限角色",
                    "状态",
                    "操作",
                  ].map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id}>
                    <td>{u.username}</td>
                    <td>{roleNames[u.role]}</td>
                    <td>
                      {u.operator_id == null
                        ? "—"
                        : (operators.find(
                            (operator) => operator.id === u.operator_id,
                          )?.name ?? `企业 #${u.operator_id}`)}
                    </td>
                    <td>
                      {profiles.find((p) => p.id === u.role_profile_id)?.name ||
                        (u.role_profile_id ? "受限角色" : "基础身份默认")}
                    </td>
                    <td>
                      {u.enabled ? "启用" : "停用"}
                      {u.must_change_password && (
                        <em className="amber">待首次改密</em>
                      )}
                      <span>
                        {u.allowed_districts === null
                          ? "全部区域"
                          : u.allowed_districts.join("、") || "无场站区域"}
                      </span>
                    </td>
                    <td>
                      <button
                        className="outline"
                        disabled={
                          busy ||
                          u.username === username ||
                          !u.permissions.every((k) => permissions.includes(k))
                        }
                        onClick={() => {
                          setAccount(u);
                          setAccountScope(u.allowed_districts);
                          setChosenRole(
                            u.role_profile_id ? String(u.role_profile_id) : "",
                          );
                          setEnabled(u.enabled);
                          setReason("");
                        }}
                      >
                        配置账号权限
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!users.length && <p className="empty">暂无账号</p>}
          </div>
          <div className="business-form">
            <button
              className="outline"
              disabled={busy || page === 1}
              onClick={() => setPage(page - 1)}
            >
              账号上一页
            </button>
            <span>
              第{page}页 · 共{total}个账号
            </span>
            <button
              className="outline"
              disabled={busy || page * 20 >= total}
              onClick={() => setPage(page + 1)}
            >
              账号下一页
            </button>
          </div>
          {account && (
            <form
              className="business-form"
              onSubmit={(e) => {
                e.preventDefault();
                run(async () => {
                  await api.edit(account.id, {
                    version: account.version,
                    role_profile_id: Number(chosenRole),
                    enabled,
                    reason,
                    allowed_districts: accountScope,
                  });
                  setAccount(null);
                  await load();
                });
              }}
            >
              <h3>{account.username} · 授权配置</h3>
              <label>
                权限角色
                <select
                  aria-label="账号权限角色"
                  value={chosenRole}
                  required
                  onChange={(e) => setChosenRole(e.target.value)}
                >
                  <option value="">请选择配置角色</option>
                  {profiles
                    .filter((p) => p.base_role === account.role)
                    .map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                账号启用
                <input
                  aria-label="账号启用"
                  type="checkbox"
                  checked={enabled}
                  onChange={(e) => setEnabled(e.target.checked)}
                />
              </label>
              <DistrictScopeEditor
                key={account.id}
                label="账号授权"
                disabled={busy}
                value={accountScope}
                onChange={setAccountScope}
              />
              <label>
                变更依据
                <input
                  aria-label="账号权限变更依据"
                  required
                  maxLength={1000}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
              <button
                className="outline"
                disabled={busy || !chosenRole || !reason.trim()}
              >
                保存账号授权
              </button>
              <button
                className="outline"
                type="button"
                onClick={() => {
                  if (canLeave()) setAccount(null);
                }}
              >
                取消账号配置
              </button>
            </form>
          )}
          {account && account.username !== username && (
            <AdminPasswordReset
              key={`${account.id}:${account.version}`}
              account={account}
              csrf={csrf}
              onChanged={async () => {
                setAccount(null);
                await load();
              }}
            />
          )}
        </section>
      )}
    </div>
  );
}
