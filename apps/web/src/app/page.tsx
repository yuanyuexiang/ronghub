"use client";
import {
  WorkspaceNavigation,
  AuxiliaryNavigation,
  auxiliaryNavigation,
  contextualNavigation,
  currentNavigationGroup,
  type NavigationItem,
  workspaceNavigation,
  workspaceDefaults,
  flattenNavigation,
  navigationMatches,
  type AlertWorkspaceView,
} from "../components/workspace-navigation";
import { useWorkspaceRoute } from "../components/use-workspace-route";
import { BusinessViewProvider } from "../components/business-view-context";
import { DashboardOperations } from "../components/dashboard-operations";
import { LoginCaptcha } from "../components/login-captcha";
import { typedApi, apiError, type ApiSchemas } from "../lib/typed-api";
import {
  MonitoringWorkspace,
  OperatorOverview,
} from "../components/prototype-read-views";
import { QualityPanel } from "../components/quality-panel";
import { RuntimePanel } from "../components/runtime-panel";
import { FaultsPanel } from "../components/faults-panel";
import { OrderTimeSlotsPanel } from "../components/order-time-slots-panel";
import { OrderSummaryPanel } from "../components/order-summary-panel";
import { OrdersPanel } from "../components/orders-panel";
import { AuditPanel } from "../components/audit-panel";
import { PhonePanel } from "../components/phone-panel";
import { PasswordPanel } from "../components/password-panel";
import { StationMapPanel } from "../components/station-map-panel";
import Link from "next/link";
import { RegistrationPanel } from "../components/registration-panel";
import { StaffApplicationsPanel } from "../components/staff-applications-panel";
import { AuthorizationPanel } from "../components/authorization-panel";
import {
  PageRefreshProvider,
  PageRefreshButton,
} from "../components/page-refresh";
import { PermissionProvider } from "../components/permissions";
import { CompanyPanel } from "../components/company-panel";
import { ContentPanel } from "../components/content-panel";
import { StatisticsPanel } from "../components/statistics-panel";
import { AlertPanel } from "../components/alert-panel";
import { ProtocolPanel } from "../components/protocol-panel";
import { HazardPanel } from "../components/hazard-panel";
import { InspectionPanel } from "../components/inspection-panel";
import { SubsidyPanel } from "../components/subsidy-panel";
import { RatingPanel } from "../components/rating-panel";
import { StationDirectory } from "../components/station-directory";
import { StationDetail } from "../components/station-detail";
import { SyncPanel } from "../components/sync-panel";
import { ApplicationPanel } from "../components/application-panel";
import { useCallback, useEffect, useState, useRef, FormEvent } from "react";
import {
  ArrowLeft,
  ChevronRight,
  LogOut,
  ShieldCheck,
  Zap,
  Bell,
} from "lucide-react";
type Me = ApiSchemas["CurrentUser"];
type Summary = ApiSchemas["DashboardSummary"];
const roles: Record<string, string> = {
  regulator: "监管端",
  association: "协会端",
  operator: "运营商端",
};
export default function Home() {
  const [me, setMe] = useState<Me | null>(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState("");
  const [authNotice, setAuthNotice] = useState("");
  const [summary, setSummary] = useState<Summary | null>(null),
    [tab, setTab] = useState("工作台");
  const [alertView, setAlertView] = useState<AlertWorkspaceView>("events");
  const [selectedView, setSelectedView] = useState({ module: "", view: "" });
  const workspaceView =
    (selectedView.module === tab ? selectedView.view : "") ||
    (tab === "权限与账号" && me && !me.permissions.includes("accounts.manage")
      ? "roles"
      : "");
  const [pageRevision, setPageRevision] = useState(0);
  const [stationEntry, setStationEntry] = useState<{
    id: number;
    name: string;
    kind: string;
  } | null>(null);
  const setWorkspaceView = (view: string) =>
    setSelectedView({ module: tab, view });
  const [navigationOpen, setNavigationOpen] = useState(false);
  const navigationGuard = useRef<(() => boolean) | null>(null);
  const registerLeaveGuard = useCallback((guard: (() => boolean) | null) => {
    navigationGuard.current = guard;
  }, []);
  const canManageAlertNotifications =
    me?.role === "regulator" &&
    (me.allowed_districts ?? null) === null &&
    me.permissions.includes("alerts.notifications");
  const allowedModules = useRef<string[]>([]);
  const allowedViews = useRef<Record<string, string[]>>({});
  useWorkspaceRoute({
    identity: loading || me?.must_change_password ? undefined : me?.username,
    defaultModule:
      me?.role === "association" && me.permissions.includes("stations.read")
        ? "场站台账"
        : "工作台",
    tab,
    alertView,
    view: workspaceView,
    allowedViews,
    canManageAlertNotifications,
    allowed: allowedModules,
    guard: navigationGuard,
    onRestore: (target, alert, view) => {
      setStationEntry(null);
      setTab(target);
      setAlertView(alert);
      setSelectedView({ module: target, view });
      if (
        target !== tab ||
        !["告警中心", "企业告警", "统计分析"].includes(target)
      )
        setPageRevision((value) => value + 1);
      setNavigationOpen(false);
    },
  });
  const [entry, setEntry] = useState("");
  useEffect(() => {
    const requested =
      new URLSearchParams(window.location.search).get("entry") ?? "";
    setEntry(roles[requested] ?? "");
  }, []);
  const [detailId, setDetailId] = useState<number | null>(null);
  const loadSequence = useRef(0);
  const [busy, setBusy] = useState(false),
    [user, setUser] = useState(""),
    [password, setPassword] = useState("");
  const loadSummary = useCallback(async () => {
    const sequence = ++loadSequence.current;
    const { data, error, response } = await typedApi.GET("/api/v1/dashboard", {
      cache: "no-store",
    });
    if (sequence !== loadSequence.current) return;
    if (response.status === 401) throw new Error("会话已失效，请重新登录");
    if (!data) throw new Error(apiError(error, "业务数据加载失败，请重试"));
    setSummary(data);
  }, []);
  useEffect(() => {
    const expired = () => {
      ++loadSequence.current;
      setMe(null);
      setDetailId(null);
      setStationEntry(null);
      setSummary(null);
      setPassword("");
      setTab("工作台");
      setError("会话已失效，请重新登录");
    };
    window.addEventListener("ronghub:session-expired", expired);
    return () => window.removeEventListener("ronghub:session-expired", expired);
  }, []);
  useEffect(() => {
    typedApi
      .GET("/api/v1/auth/me", { cache: "no-store" })
      .then(async ({ data: actor, response }) => {
        if (actor) {
          setMe(actor);
          if (!actor.must_change_password) await loadSummary();
        } else if (response.status !== 401) throw new Error("服务暂不可用");
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [loadSummary]);
  const [captcha, setCaptcha] = useState<ApiSchemas["LoginCaptcha"] | null>(
    null,
  );
  const [captchaAnswer, setCaptchaAnswer] = useState("");
  const [captchaRefresh, setCaptchaRefresh] = useState(0);
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem("ronghub:recovery-result");
      sessionStorage.removeItem("ronghub:recovery-result");
      if (!raw) return;
      const result: unknown = JSON.parse(raw);
      if (
        result &&
        typeof result === "object" &&
        "expires" in result &&
        typeof result.expires === "number" &&
        result.expires > Date.now()
      ) {
        if (
          "account" in result &&
          typeof result.account === "string" &&
          result.account.length <= 100
        )
          setUser(result.account);
        setAuthNotice("密码已重置，全部设备已退出。请使用新密码重新登录。");
      }
    } catch {
      /* Ignore invalid local navigation state; never store a password. */
    }
  }, []);
  async function login(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await typedApi.POST("/api/v1/auth/login", {
        body: {
          username: user,
          password,
          captcha_id: captcha?.captcha_id ?? "",
          captcha_answer: captchaAnswer,
        },
      });
      if (!result.data) throw new Error(apiError(result.error, "登录失败"));
      const identity = await typedApi.GET("/api/v1/auth/me", {
        cache: "no-store",
      });
      const actor = identity.data;
      if (!actor)
        throw new Error(apiError(identity.error, "登录身份读取失败，请重试"));
      setMe(actor);
      setPassword("");
      setAuthNotice("");
      if (!actor.must_change_password) await loadSummary();
    } catch (e) {
      setError(e instanceof Error ? e.message : "请求失败");
      setCaptchaRefresh((v) => v + 1);
    } finally {
      setBusy(false);
    }
  }
  async function logout() {
    const { response: r } = await typedApi.POST("/api/v1/auth/logout", {
      headers: { "x-csrf-token": me?.csrf_token || "" },
    });
    if (r.ok) {
      ++loadSequence.current;
      setMe(null);
      setDetailId(null);
      setStationEntry(null);
      setSummary(null);
      setUser("");
      setTab("工作台");
    } else setError("退出失败，请重试");
  }
  if (loading) return <main className="center">正在连接监管工作台…</main>;
  if (!me)
    return (
      <main className="login">
        <section className="intro">
          <div className="brand">
            <Zap />
            <div>
              充电监管平台<small>福州市</small>
            </div>
          </div>
          <p className="eyebrow">FUZHOU · CHARGING SUPERVISION</p>
          <h1>
            充电设施全生命周期
            <br />
            数字化监管
          </h1>
          <p>
            福州市充电监管平台
            <br />
            场站信息 · 运行监测 · 行业协同
          </p>
          <div className="intro-foot">场站接入 · 安全监管 · 行业协同</div>
        </section>
        <section className="login-form">
          <nav className="login-return-links" aria-label="登录返回导航">
            <Link href="/portal">返回门户</Link>
            <Link href="/portal/login">切换登录入口</Link>
          </nav>
          <form onSubmit={login}>
            <span className="login-icon">
              <ShieldCheck />
            </span>
            <h2>登录工作台</h2>
            {entry && (
              <p className="muted">
                {entry}登录 · 登录后按账号实际授权进入工作台
              </p>
            )}
            <p>使用已分配的账号进入对应角色工作台</p>
            <label>
              账号
              <input
                autoComplete="username"
                value={user}
                onChange={(e) => setUser(e.target.value)}
                required
                placeholder="请输入账号"
              />
            </label>
            <label>
              密码
              <input
                autoComplete="current-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                placeholder="请输入密码"
              />
            </label>
            <LoginCaptcha
              value={captcha}
              onReady={setCaptcha}
              answer={captchaAnswer}
              onAnswer={setCaptchaAnswer}
              refreshKey={captchaRefresh}
              busy={busy}
            />
            {error && (
              <div role="alert" className="error">
                {error}
              </div>
            )}
            <button className="primary" disabled={busy || !captcha}>
              {busy ? "正在登录…" : "登录平台"}
              <ChevronRight size={17} />
            </button>
            <p className="muted">请使用已审核开通的账号登录。</p>
            {authNotice && <p role="status">{authNotice}</p>}
            <p className="muted">
              未绑定手机或无法接收验证码，可联系获授权管理员核验身份后协助重置。
            </p>
            <div className="auth-sms-links">
              <Link href="/recover">忘记密码</Link>
              <Link href="/sms-login">短信验证码登录</Link>
            </div>
            <p>
              <Link href="/staff-register">申请监管 / 协会工作人员账号</Link>
            </p>
            <Link href="/register">企业注册 / 注册进度查询</Link>
            <p>
              <Link href="/portal">查看公开资讯公告</Link>
            </p>
          </form>
        </section>
      </main>
    );
  const securityChanged = (message: string) => {
    ++loadSequence.current;
    setMe(null);
    setDetailId(null);
    setStationEntry(null);
    setSummary(null);
    setPassword("");
    setTab("工作台");
    setError("");
    setAuthNotice(message);
  };
  const passwordChanged = () =>
    securityChanged("密码已更新，请使用新密码重新登录。");
  if (me.must_change_password)
    return (
      <main className="content">
        <PasswordPanel
          csrf={me.csrf_token}
          required
          onChanged={passwordChanged}
        />
        <button className="outline" onClick={logout}>
          退出临时登录
        </button>
      </main>
    );
  const geographicScope = me.allowed_districts ?? null;
  const has = (cap: string) => me.permissions.includes(cap);
  let menu =
    me.role === "regulator"
      ? [
          "工作台",
          "场站汇总",
          "场站台账",
          "场站申请",
          "数据同步",
          "告警中心",
          "巡检督办",
          "隐患督办",
          "补贴管理",
          "星级查看",
        ]
      : me.role === "operator"
        ? [
            "工作台",
            "场站台账",
            "场站申请",
            "企业告警",
            "整改任务",
            "隐患督办",
            "补贴申报",
            "星级申报",
          ]
        : ["工作台", "场站台账", "星级评价"];
  if (me.role !== "association")
    menu.push("充电订单", "订单汇总", "充电时段", "故障分析", "运行状态");
  if (
    me.permissions.includes("accounts.manage") &&
    me.allowed_districts === null
  )
    menu.push("人员账号申请");
  menu.push("账号安全");
  menu.splice(1, 0, me.role === "regulator" ? "全域地图" : "场站地图");
  if (me.role !== "association")
    menu.push(me.role === "regulator" ? "企业台账" : "企业资料");
  if (me.role === "regulator") menu.push("注册审核");
  menu.push("公告与通知", "监管协议", "统计分析");
  const menuPermissions: Record<string, string> = {
    场站汇总: "statistics.read",
    全域地图: "stations.read",
    场站地图: "stations.read",
    注册审核: "registrations.read",
    场站申请: "applications.read",
    数据同步: "sync.read",
    充电订单: "orders.read",
    订单汇总: "orders.read",
    充电时段: "orders.read",
    故障分析: "statistics.read",
    运行状态: "statistics.read",
    告警中心: "alerts.read",
    企业告警: "alerts.read",
    隐患督办: "hazards.read",
    巡检督办: "inspections.read",
    整改任务: "inspections.read",
    补贴管理: "subsidy.read",
    补贴申报: "subsidy.read",
    星级查看: "ratings.read",
    星级申报: "ratings.read",
    星级评价: "ratings.read",
    企业台账: "companies.read",
    企业资料: "companies.read",
    公告与通知: "content.read",
    监管协议: "content.read",
    统计分析: "statistics.read",
  };
  if (geographicScope !== null)
    menu = menu.filter((item) => !["数据同步", "注册审核"].includes(item));
  menu = menu.filter(
    (item) => !menuPermissions[item] || has(menuPermissions[item]),
  );
  if (me.role === "regulator" && geographicScope === null && has("audits.read"))
    menu.push("操作日志");
  if (has("accounts.manage") || has("roles.manage")) menu.push("权限与账号");
  allowedModules.current = menu;
  const navigation = workspaceNavigation(menu, me.role, me.permissions);
  const auxiliary = auxiliaryNavigation(menu, me.role, me.permissions);
  const contextual = contextualNavigation(
    menu,
    me.role,
    me.permissions,
    canManageAlertNotifications,
  );
  const allNavigation = [
    ...flattenNavigation(navigation),
    ...auxiliary,
    ...contextual,
  ];
  allowedViews.current = Object.fromEntries(
    menu.map((module) => [
      module,
      [
        ...new Set(
          allNavigation
            .filter((item) => item.tab === module)
            .flatMap((item) => [item.view ?? "", ...(item.matchViews ?? [])]),
        ),
      ],
    ]),
  );
  if (me.role !== "regulator" && menu.includes("统计分析"))
    allowedViews.current["统计分析"].push(
      "energy",
      "district",
      "operator",
      "power",
      ...(me.role === "operator" ? ["utilization"] : []),
    );
  const internalViews: Record<string, string[]> = {
    场站申请: [
      "all",
      "progress",
      "access",
      "acceptance",
      "commission",
      "decommission",
      "change",
    ],
    人员账号申请: ["pending"],
    巡检督办: [
      "tasks",
      ...(has("inspections.assign") ? ["plans", "quick"] : []),
    ],
    补贴管理: [
      "claims",
      "policies",
      "energy",
      "statistics",
      "statistics.operator",
      "statistics.station",
      "statistics.detail",
    ],
    补贴申报: [
      "claims",
      "records",
      "policies",
      "energy",
      "statistics",
      ...(has("subsidy.create") ? ["annual"] : []),
    ],
    星级评价: [
      "applications",
      "standards",
      "publications",
      "process",
      "results",
      "collaboration",
      ...(has("statistics.read") && has("stations.read") ? ["data"] : []),
    ],
    告警中心: ["", "station_metric", "device_charging"],
    公告与通知: [
      "all",
      "information",
      "notice",
      ...(has("content.manage") ? ["protocol"] : []),
    ],
  };
  for (const [module, views] of Object.entries(internalViews))
    if (menu.includes(module))
      allowedViews.current[module] = [
        ...new Set([...(allowedViews.current[module] ?? []), ...views]),
      ];
  if (has("content.manage"))
    allowedViews.current["公告与通知"] = [
      ...(allowedViews.current["公告与通知"] ?? []),
      "protocol",
    ];
  function navigateWorkspace(
    item: NavigationItem,
    station: typeof stationEntry = null,
  ) {
    if (
      item.tab === tab &&
      (item.view ?? "") === workspaceView &&
      (item.alertView ?? "events") === alertView
    ) {
      setNavigationOpen(false);
      return;
    }
    if (navigationGuard.current && !navigationGuard.current()) return false;
    setStationEntry(station);
    const params = new URLSearchParams({ module: item.tab });
    if (item.view) params.set("view", item.view);
    if (item.alertView && item.alertView !== "events")
      params.set("alert", item.alertView);
    const href = `/?${params}`;
    if (window.location.pathname + window.location.search !== href)
      window.history.pushState(null, "", href);
    if (item.tab !== tab || !["告警中心", "企业告警", "统计分析"].includes(tab))
      setPageRevision((value) => value + 1);
    setSelectedView({ module: item.tab, view: item.view ?? "" });
    setTab(item.tab);
    setAlertView(item.alertView ?? "events");
    setNavigationOpen(false);
  }
  const currentItem = [...allNavigation]
    .reverse()
    .find((item) => navigationMatches(item, tab, alertView, workspaceView));
  const currentGroup = currentNavigationGroup(
    navigation,
    tab,
    alertView,
    workspaceView,
  );
  const relatedActions = contextual.filter((entry) => {
    if (entry.tab !== tab) return false;
    if (tab === "告警中心") return entry.alertView !== alertView;
    if (tab === "运行状态" || tab === "故障分析") return !workspaceView;
    if (tab === "统计分析") return workspaceView === "utilization";
    if (entry.view === "applications.publication")
      return workspaceView === "results";
    if (tab === "补贴管理") return workspaceView === "energy";
    if (tab === "补贴申报" && workspaceView.startsWith("energy"))
      return entry.view?.startsWith("energy") && entry.view !== workspaceView;
    return (
      (!workspaceView || workspaceView === workspaceDefaults[tab]) &&
      (tab !== "补贴申报" || entry.view !== "annual")
    );
  });
  const parentView =
    me.role === "regulator" && tab === "运行状态" && workspaceView === "history"
      ? { tab, label: "返回动态监控" }
      : me.role === "regulator" &&
          tab === "故障分析" &&
          workspaceView === "events"
        ? { tab, label: "返回告警分析" }
        : tab === "统计分析" && workspaceView === "runtime"
          ? { tab, view: "utilization", label: "返回利用率统计" }
          : tab === "补贴管理" && workspaceView === "energy.device"
            ? { tab, view: "energy", label: "返回电量数据" }
            : tab === "星级评价" && workspaceView === "applications.publication"
              ? { tab, view: "results", label: "返回评价结果管理" }
              : tab === "补贴申报" &&
                  ["statistics", "energy", "energy.daily"].includes(
                    workspaceView,
                  )
                ? { tab, view: "claims", label: "返回补贴申请管理" }
                : undefined;
  const pageTitle = currentItem?.label ?? (tab === "工作台" ? "运营总览" : tab);
  return (
    <PageRefreshProvider>
      <PermissionProvider
        permissions={me.permissions}
        allowedDistricts={geographicScope}
      >
        <div
          className={`shell prototype-workspace ${navigationOpen ? "navigation-open" : ""}`}
        >
          <aside className="workspace-sidebar" aria-label="功能导航">
            <div className="brand">
              <Zap />
              <div>
                充电监管平台<small>福州市 · {roles[me.role]}</small>
              </div>
            </div>
            <p className="side-sub">福州市充电监管平台</p>
            <div className="role-label">
              {roles[me.role]}
              {geographicScope !== null && (
                <p>授权区域：{geographicScope!.join("、") || "无场站区域"}</p>
              )}
            </div>
            <WorkspaceNavigation
              groups={navigation}
              activeGroup={currentGroup?.label}
              tab={tab}
              alertView={alertView}
              view={workspaceView}
              onNavigate={navigateWorkspace}
            />
            <div className="side-bottom">
              <ShieldCheck size={16} /> {roles[me.role]} · 授权业务空间
            </div>
          </aside>
          <div className="workspace">
            <header>
              <span className="workspace-breadcrumb">
                <button
                  className="navigation-mobile-toggle"
                  aria-label="展开或收起功能导航"
                  aria-expanded={navigationOpen}
                  onClick={() => setNavigationOpen((value) => !value)}
                >
                  ☰
                </button>
                <span>{roles[me.role]}</span>
                <ChevronRight size={14} />
                {currentGroup && currentGroup.label !== pageTitle && (
                  <>
                    <span>{currentGroup.label}</span>
                    <ChevronRight size={14} />
                  </>
                )}
                <strong>{pageTitle}</strong>
              </span>
              <div>
                <AuxiliaryNavigation
                  items={[...relatedActions, ...auxiliary]}
                  onNavigate={navigateWorkspace}
                />
                <Bell size={17} />
                <b>{me.username}</b>
                <button onClick={logout} aria-label="退出登录">
                  <LogOut size={17} />
                </button>
              </div>
            </header>
            <main className="content">
              <div className="heading">
                <h1>
                  {tab === "工作台" && me.role === "regulator"
                    ? "运营总览"
                    : pageTitle}
                </h1>
                <div className="workspace-page-actions">
                  {parentView && (
                    <button
                      type="button"
                      className="outline"
                      onClick={() => navigateWorkspace(parentView)}
                    >
                      <ArrowLeft size={14} aria-hidden="true" />
                      {parentView.label}
                    </button>
                  )}
                  <PageRefreshButton
                    scope={tab}
                    onRefresh={async () => {
                      setError("");
                      await loadSummary();
                    }}
                  />
                </div>
              </div>
              {error && (
                <p className="error" role="alert">
                  {error}
                </p>
              )}
              {tab === "工作台" && summary && me.role !== "operator" && (
                <section className="workbench-pending" aria-label="待处理事项">
                  <h2>
                    {me.role === "association"
                      ? "协会评审工作台"
                      : "待处理事项"}
                  </h2>
                  <div className="action-row">
                    {(me.role === "regulator"
                      ? ([
                          [
                            "接入管理",
                            "接入待审",
                            summary.applications_pending,
                            "queue",
                          ],
                          [
                            "补贴申请记录",
                            "补贴待审",
                            summary.subsidies_pending,
                            "claims.pending",
                          ],
                          [
                            "企业申报查看",
                            "星级待审",
                            summary.rating_pending,
                            "applications.pending",
                          ],
                          [
                            "人员账号申请",
                            "人员账号待审",
                            summary.staff_applications_pending,
                            "pending",
                          ],
                        ] as const)
                      : ([
                          [
                            "企业申报管理",
                            "待评审",
                            summary.rating_pending,
                            "applications.pending",
                          ],
                          [
                            "待公示申报",
                            "待公示",
                            summary.rating_reviewed,
                            "applications.publication",
                          ],
                        ] as const)
                    ).map(([target, label, count, view]) => {
                      const entry = allNavigation.find(
                        (entry) => entry.label === target,
                      );
                      return (
                        entry && (
                          <button
                            type="button"
                            key={target}
                            className="outline"
                            aria-label={label}
                            onClick={() =>
                              navigateWorkspace({ ...entry, view })
                            }
                          >
                            {label}
                            <strong>{count ?? "—"}</strong>
                            <ChevronRight size={14} />
                          </button>
                        )
                      );
                    })}
                  </div>
                </section>
              )}
              {tab === "工作台" && summary && me.role === "regulator" && (
                <DashboardOperations />
              )}
              {tab === "工作台" &&
                summary &&
                me.role === "operator" &&
                (workspaceView === "operations" ? (
                  <DashboardOperations />
                ) : (
                  <OperatorOverview
                    summary={summary}
                    onNavigate={navigateWorkspace}
                  />
                ))}
              <BusinessViewProvider
                key={pageRevision}
                view={workspaceView}
                setView={setWorkspaceView}
                sidebar={
                  (me.role === "regulator" &&
                    ["星级查看", "统计分析", "权限与账号"].includes(tab)) ||
                  (me.role === "association" && tab === "星级评价") ||
                  (me.role === "operator" &&
                    ["星级申报", "补贴申报"].includes(tab))
                }
              >
                {tab === "人员账号申请" ? (
                  <StaffApplicationsPanel
                    initialStatus={workspaceView === "pending" ? "pending" : ""}
                    csrf={me.csrf_token}
                    registerLeaveGuard={registerLeaveGuard}
                  />
                ) : tab === "账号安全" ? (
                  <div className="business-stack">
                    <PhonePanel
                      csrf={me.csrf_token}
                      onBound={() =>
                        securityChanged(
                          "手机号已完成模拟验证绑定，请重新登录。",
                        )
                      }
                    />
                    <PasswordPanel
                      csrf={me.csrf_token}
                      required={false}
                      onChanged={passwordChanged}
                    />
                  </div>
                ) : ["全域地图", "场站地图"].includes(tab) ? (
                  <StationMapPanel />
                ) : tab === "运行状态" ? (
                  me.role === "regulator" && workspaceView !== "history" ? (
                    <MonitoringWorkspace />
                  ) : (
                    <RuntimePanel />
                  )
                ) : tab === "故障分析" ? (
                  me.role === "regulator" && workspaceView !== "events" ? (
                    <QualityPanel purpose="alarm" />
                  ) : (
                    <FaultsPanel />
                  )
                ) : tab === "充电时段" ? (
                  <OrderTimeSlotsPanel />
                ) : tab === "订单汇总" ? (
                  <OrderSummaryPanel />
                ) : tab === "充电订单" ? (
                  <OrdersPanel />
                ) : tab === "操作日志" ? (
                  <AuditPanel />
                ) : tab === "注册审核" ? (
                  <RegistrationPanel
                    csrf={me.csrf_token}
                    registerLeaveGuard={registerLeaveGuard}
                  />
                ) : tab === "权限与账号" ? (
                  <AuthorizationPanel
                    csrf={me.csrf_token}
                    username={me.username}
                    currentProfileId={me.role_profile_id}
                    permissions={me.permissions}
                    registerLeaveGuard={registerLeaveGuard}
                  />
                ) : ["企业台账", "企业资料"].includes(tab) ? (
                  <CompanyPanel
                    role={me.role}
                    csrf={me.csrf_token}
                    registerLeaveGuard={registerLeaveGuard}
                  />
                ) : tab === "公告与通知" ? (
                  <ContentPanel
                    role={me.role}
                    csrf={me.csrf_token}
                    registerLeaveGuard={registerLeaveGuard}
                  />
                ) : tab === "监管协议" ? (
                  <ProtocolPanel
                    onManage={() => {
                      setSelectedView({
                        module: "公告与通知",
                        view: "protocol",
                      });
                      setTab("公告与通知");
                      setPageRevision((value) => value + 1);
                    }}
                  />
                ) : tab === "统计分析" ? (
                  <StatisticsPanel role={me.role} />
                ) : ["告警中心", "企业告警"].includes(tab) ? (
                  <AlertPanel
                    role={me.role}
                    csrf={me.csrf_token}
                    view={alertView}
                    onViewChange={(view) => {
                      setAlertView(view);
                      setWorkspaceView("");
                    }}
                    registerLeaveGuard={registerLeaveGuard}
                  />
                ) : tab === "隐患督办" ? (
                  <HazardPanel
                    role={me.role}
                    csrf={me.csrf_token}
                    registerLeaveGuard={registerLeaveGuard}
                  />
                ) : ["巡检督办", "整改任务"].includes(tab) ? (
                  <InspectionPanel
                    role={me.role}
                    csrf={me.csrf_token}
                    registerLeaveGuard={registerLeaveGuard}
                  />
                ) : tab.startsWith("补贴") ? (
                  <SubsidyPanel
                    registerLeaveGuard={registerLeaveGuard}
                    role={me.role}
                    csrf={me.csrf_token}
                    onChanged={() => {
                      loadSummary().catch((e) => setError(e.message));
                    }}
                  />
                ) : tab.startsWith("星级") ? (
                  <RatingPanel
                    initialStation={
                      stationEntry?.kind === "rating" ? stationEntry : undefined
                    }
                    registerLeaveGuard={registerLeaveGuard}
                    role={me.role}
                    csrf={me.csrf_token}
                    onChanged={() => {
                      loadSummary().catch((e) => setError(e.message));
                    }}
                  />
                ) : tab === "数据同步" ? (
                  <SyncPanel csrf={me.csrf_token} />
                ) : tab === "场站申请" ? (
                  <ApplicationPanel
                    initialStation={
                      stationEntry?.kind !== "rating"
                        ? (stationEntry ?? undefined)
                        : undefined
                    }
                    role={me.role}
                    csrf={me.csrf_token}
                    registerLeaveGuard={registerLeaveGuard}
                  />
                ) : tab === "场站汇总" ? (
                  <DashboardOperations variant="stations" />
                ) : tab === "场站台账" ||
                  (tab === "工作台" &&
                    me.role !== "operator" &&
                    has("stations.read")) ? (
                  <StationDirectory
                    onOpen={setDetailId}
                    actionMode={me.role === "operator" ? "operator" : undefined}
                    onApplication={
                      me.role === "operator"
                        ? (id, name, kind) => {
                            const item = allNavigation.find((entry) =>
                              kind === "rating"
                                ? entry.tab === "星级申报" &&
                                  entry.view === "create"
                                : entry.tab === "场站申请" &&
                                  entry.view === "all",
                            );
                            if (item)
                              navigateWorkspace(item, { id, name, kind });
                          }
                        : undefined
                    }
                  />
                ) : null}
              </BusinessViewProvider>
              {detailId !== null && (
                <StationDetail
                  key={detailId}
                  stationId={detailId}
                  onClose={() => setDetailId(null)}
                />
              )}
              <p className="footer">福州市充电监管平台 · 场站服务与行业协同</p>
            </main>
          </div>
        </div>
      </PermissionProvider>
    </PageRefreshProvider>
  );
}
