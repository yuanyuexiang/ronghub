"use client";
import {
  WorkspaceNavigation,
  AuxiliaryNavigation,
  auxiliaryNavigation,
  type NavigationItem,
  workspaceNavigation,
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
  Activity,
  Building2,
  ChevronRight,
  LogOut,
  Search,
  ShieldCheck,
  Zap,
  Bell,
  MapPin,
} from "lucide-react";
type Me = ApiSchemas["CurrentUser"];
type Station = {
  id: number;
  name: string;
  district: string;
  address: string;
  status: string;
  connectors: number;
  power_kw: string;
};
type Summary = ApiSchemas["DashboardSummary"];
const roles: Record<string, string> = {
  regulator: "监管端",
  association: "协会端",
  operator: "运营商端",
};
const states: Record<string, string> = {
  pending: "待接入审核",
  access_approved: "接入通过",
  accepted: "验收通过",
  operating: "运营中",
  decommissioned: "已核销",
};
export default function Home() {
  const [me, setMe] = useState<Me | null>(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState("");
  const [authNotice, setAuthNotice] = useState("");
  const [rows, setRows] = useState<Station[]>([]),
    [summary, setSummary] = useState<Summary | null>(null),
    [q, setQ] = useState(""),
    [tab, setTab] = useState("工作台");
  const [alertView, setAlertView] = useState<AlertWorkspaceView>("events");
  const [selectedView, setSelectedView] = useState({ module: "", view: "" });
  const workspaceView =
    (selectedView.module === tab ? selectedView.view : "") ||
    (tab === "权限与账号" && me && !me.permissions.includes("accounts.manage")
      ? "roles"
      : "");
  const [pageRevision, setPageRevision] = useState(0);
  const setWorkspaceView = (view: string) =>
    setSelectedView({ module: tab, view });
  const [navigationOpen, setNavigationOpen] = useState(false);
  const navigationGuard = useRef<(() => boolean) | null>(null);
  const registerLeaveGuard = useCallback((guard: (() => boolean) | null) => {
    navigationGuard.current = guard;
  }, []);
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
    allowed: allowedModules,
    guard: navigationGuard,
    onRestore: (target, alert, view) => {
      setTab(target);
      setAlertView(alert);
      setSelectedView({ module: target, view });
      if (target !== tab || !["告警中心", "企业告警"].includes(target))
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
  const [stationPage, setStationPage] = useState(1),
    [stationTotal, setStationTotal] = useState(0),
    [stationLoading, setStationLoading] = useState(false);
  const [stationStatus, setStationStatus] = useState(""),
    [district, setDistrict] = useState("");
  const [applied, setApplied] = useState({ q: "", status: "", district: "" });
  const loadSequence = useRef(0);
  const [busy, setBusy] = useState(false),
    [user, setUser] = useState(""),
    [password, setPassword] = useState("");
  const load = useCallback(
    async (query = "", page = 1, status = "", area = "") => {
      const sequence = ++loadSequence.current;
      setStationLoading(true);
      try {
        const [summary, stations] = await Promise.all([
          typedApi.GET("/api/v1/dashboard", { cache: "no-store" }),
          typedApi.GET("/api/v1/stations", {
            params: { query: { q: query, page, status, district: area } },
            cache: "no-store",
          }),
        ]);
        if (summary.response.status === 401 || stations.response.status === 401)
          throw new Error("会话已失效，请重新登录");
        if (!summary.data || !stations.data)
          throw new Error(
            apiError(
              summary.error ?? stations.error,
              "业务数据加载失败，请重试",
            ),
          );
        if (sequence !== loadSequence.current) return;
        const b = stations.data;
        setSummary(summary.data);
        setRows(b.items);
        setStationPage(b.page);
        setStationTotal(b.total);
        setApplied({ q: query, status, district: area });
      } finally {
        if (sequence === loadSequence.current) setStationLoading(false);
      }
    },
    [],
  );
  useEffect(() => {
    const expired = () => {
      ++loadSequence.current;
      setMe(null);
      setRows([]);
      setSummary(null);
      setPassword("");
      setTab("工作台");
      setStationLoading(false);
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
          if (!actor.must_change_password) await load();
        } else if (response.status !== 401) throw new Error("服务暂不可用");
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [load]);
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
      if (!actor.must_change_password) await load();
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
      setStationLoading(false);
      setQ("");
      setStationStatus("");
      setDistrict("");
      setApplied({ q: "", status: "", district: "" });
      setStationPage(1);
      setStationTotal(0);
      setMe(null);
      setRows([]);
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
    setRows([]);
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
  const navigation = workspaceNavigation(
    menu,
    me.role,
    me.permissions.includes("alerts.notifications") && geographicScope === null,
    me.permissions,
  );
  const auxiliary = auxiliaryNavigation(menu, me.role, me.permissions);
  const allNavigation = [...flattenNavigation(navigation), ...auxiliary];
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
  if (me.role !== "regulator")
    Object.assign(allowedViews.current, {
      补贴申报: [
        "claims",
        "policies",
        "statistics",
        "energy",
        ...(has("subsidy.create") ? ["annual"] : []),
      ],
      星级申报: [
        "applications",
        "standards",
        "publications",
        "process",
        ...(has("statistics.read") ? ["data"] : []),
        ...(has("ratings.create") ? ["create"] : []),
      ],
      星级评价: [
        "applications",
        "standards",
        "publications",
        "process",
        ...(has("statistics.read") ? ["data"] : []),
      ],
      统计分析: [
        "energy",
        "district",
        "operator",
        "power",
        ...(me.role !== "association" ? ["utilization", "combined"] : []),
      ],
    });
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
  function navigateWorkspace(item: NavigationItem) {
    if (
      item.tab === tab &&
      (item.view ?? "") === workspaceView &&
      (item.alertView ?? "events") === alertView
    ) {
      setNavigationOpen(false);
      return;
    }
    if (navigationGuard.current && !navigationGuard.current()) return false;
    const params = new URLSearchParams({ module: item.tab });
    if (item.view) params.set("view", item.view);
    if (item.alertView && item.alertView !== "events")
      params.set("alert", item.alertView);
    const href = `/?${params}`;
    if (window.location.pathname + window.location.search !== href)
      window.history.pushState(null, "", href);
    if (item.tab !== tab || !["告警中心", "企业告警"].includes(tab))
      setPageRevision((value) => value + 1);
    setSelectedView({ module: item.tab, view: item.view ?? "" });
    setTab(item.tab);
    setAlertView(item.alertView ?? "events");
    setNavigationOpen(false);
  }
  const currentItem = [...allNavigation]
    .reverse()
    .find((item) => navigationMatches(item, tab, alertView, workspaceView));
  const currentGroup = navigation.find((group) =>
    flattenNavigation([group]).some((item) => item === currentItem),
  );
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
                <span>{currentGroup?.label ?? "更多功能"}</span>
                <ChevronRight size={14} />
                <strong>{pageTitle}</strong>
              </span>
              <div>
                <AuxiliaryNavigation
                  items={auxiliary}
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
                <div>
                  <p className="eyebrow">{currentGroup?.label ?? "更多功能"}</p>
                  <h1>
                    {tab === "工作台" && me.role === "regulator"
                      ? "运营总览"
                      : pageTitle}
                  </h1>
                  <p className="muted">
                    {tab === "权限与账号"
                      ? "按岗位分配角色与业务权限，管理账号状态和数据授权范围"
                      : tab === "注册审核"
                        ? "核验企业入驻资料、资质与联系人，留存审核和账号开通记录"
                        : ["企业台账", "企业资料"].includes(tab)
                          ? "维护企业档案、联系人与资质，核对企业资料和变更记录"
                          : tab.startsWith("补贴")
                            ? "申报、审核与发放登记，分别留存核算依据和办理记录"
                            : ["全域地图", "场站地图"].includes(tab)
                              ? "按授权范围查看场站分布、运行快照与设备档案"
                              : tab === "运行状态"
                                ? "分开核对连接与业务状态，查看历史区间和观测覆盖"
                                : tab === "故障分析"
                                  ? "核对源报故障生命周期、恢复时间与已恢复样本统计"
                                  : tab === "充电时段"
                                    ? "分开查看订单开始分布、源报小时电量与计量缺失"
                                    : tab === "订单汇总"
                                      ? "分场站与源桩编码汇总订单，核对已知指标及缺失覆盖"
                                      : tab === "充电订单"
                                        ? "查询源报充电记录、桩枪编码与时间，按授权范围导出"
                                        : tab === "隐患督办"
                                          ? "独立跟踪隐患整改、留存企业执行事实并明确监管关闭"
                                          : ["巡检督办", "整改任务"].includes(
                                                tab,
                                              )
                                            ? "跟踪整改期限、核验证据并保留回复与复核记录"
                                            : ["告警中心", "企业告警"].includes(
                                                  tab,
                                                )
                                              ? "区分设备事件与人工确认，跟踪异常、恢复及处置依据"
                                              : tab === "场站申请"
                                                ? "按编号、类型与状态检索申请，查阅材料并跟踪审核进度"
                                                : tab === "操作日志"
                                                  ? "按人员、动作与时间追溯已记录的业务操作"
                                                  : tab === "统计分析"
                                                    ? "查看授权范围内的日月年观测电量、排名与数据覆盖情况"
                                                    : tab === "监管协议"
                                                      ? "查阅已发布的监管协议正文与原始文件，核对正式接入要求"
                                                      : tab === "公告与通知"
                                                        ? "查阅公告、接收定向通知并跟踪企业回复"
                                                        : [
                                                              "星级查看",
                                                              "星级申报",
                                                              "星级评价",
                                                            ].includes(tab)
                                                          ? "申报、复核、公示与申诉，保留每次评定依据"
                                                          : tab ===
                                                              "人员账号申请"
                                                            ? "核验工作人员身份，明确角色与区域并留存开通记录"
                                                            : tab === "账号安全"
                                                              ? "管理本人登录凭据、验证手机与会话安全"
                                                              : me.role ===
                                                                  "operator"
                                                                ? "查看本企业场站及设备概况"
                                                                : "掌握已录入场站信息与接入情况"}
                  </p>
                </div>
                <PageRefreshButton
                  scope={tab}
                  onRefresh={async () => {
                    setError("");
                    await load(
                      applied.q,
                      stationPage,
                      applied.status,
                      applied.district,
                    );
                  }}
                />
              </div>
              {error && (
                <p className="error" role="alert">
                  {error}
                </p>
              )}
              {summary &&
                tab === "工作台" &&
                !["operator"].includes(me.role) && (
                  <section className="metrics">
                    {(me.role === "association"
                      ? [
                          ["待评审", summary.rating_pending, "项", ShieldCheck],
                          ["待公示", summary.rating_reviewed, "项", Activity],
                          ["已公示", summary.rating_published, "项", Zap],
                          ["场站总数", summary.stations, "个", Building2],
                        ]
                      : me.role === "operator"
                        ? [
                            ["所属场站", summary.stations, "个", Building2],
                            [
                              "接口有效电量",
                              summary.energy_total === null
                                ? "暂无数据"
                                : summary.energy_total,
                              "kWh",
                              Zap,
                            ],
                            [
                              "已审定补贴",
                              summary.subsidy_total,
                              "元",
                              Activity,
                            ],
                            [
                              "场站申请待审",
                              summary.applications_pending,
                              "项",
                              ShieldCheck,
                            ],
                          ]
                        : [
                            ["场站总数", summary.stations, "个", Building2],
                            ["装机功率", summary.power_kw, "kW", Activity],
                            [
                              "场站申请待审",
                              summary.applications_pending,
                              "项",
                              ShieldCheck,
                            ],
                            [
                              "补贴申请待审",
                              summary.subsidies_pending,
                              "项",
                              Zap,
                            ],
                            [
                              "人员账号待审",
                              summary.staff_applications_pending,
                              "项",
                              ShieldCheck,
                            ],
                          ]
                    )
                      .filter(([, n]) => n !== undefined && n !== null)
                      .map(([label, n, unit, Icon]) => {
                        const I = Icon as typeof Zap;
                        return (
                          <article key={String(label)}>
                            <div>
                              {String(label)}
                              <I size={19} />
                            </div>
                            <strong>
                              {String(n)}
                              {String(n) !== "暂无数据" && (
                                <small>{String(unit)}</small>
                              )}
                            </strong>
                            <p>当前授权范围 · 演示台账</p>
                          </article>
                        );
                      })}
                  </section>
                )}
              {tab === "工作台" &&
                summary &&
                !["operator"].includes(me.role) && (
                  <section className="panel workspace-tasks">
                    <h2>
                      {me.role === "association"
                        ? "协会评审工作台"
                        : me.role === "operator"
                          ? "企业申报工作台"
                          : "待处理事项与常用入口"}
                    </h2>
                    <p className="muted">
                      {me.role === "association"
                        ? "核实申报依据、完成复核并发布评价结果"
                        : me.role === "operator"
                          ? "维护企业场站，跟进申报进度及退回事项"
                          : "集中处理场站审批、补贴核定与数据接入事项"}
                    </p>
                    <div className="action-row">
                      {menu
                        .filter((item) =>
                          (me.role === "regulator"
                            ? ["场站申请", "补贴管理", "星级查看", "隐患督办"]
                            : me.role === "operator"
                              ? ["场站申请", "补贴申报", "星级申报", "整改任务"]
                              : ["星级评价", "场站台账"]
                          ).includes(item),
                        )
                        .map((item) => (
                          <button
                            key={item}
                            className="outline"
                            onClick={() => setTab(item)}
                          >
                            进入{item}
                            <ChevronRight size={14} />
                          </button>
                        ))}
                    </div>
                    <p className="muted">
                      以上汇总来自当前授权范围；示例电量及金额不作为正式结算依据。
                    </p>
                  </section>
                )}
              {tab === "工作台" && summary && me.role === "regulator" && (
                <DashboardOperations />
              )}
              {["企业台账", "场站台账"].includes(tab) && (
                <div className="workspace-context-actions">
                  {auxiliary
                    .filter(
                      (i) =>
                        (tab === "企业台账" && i.tab === "注册审核") ||
                        (tab === "场站台账" && i.tab === "数据同步"),
                    )
                    .map((i) => (
                      <button
                        className="outline"
                        key={i.tab}
                        onClick={() => navigateWorkspace(i)}
                      >
                        {i.label}
                      </button>
                    ))}
                </div>
              )}
              {tab === "工作台" && summary && me.role === "operator" && (
                <OperatorOverview summary={summary} onNavigate={setTab} />
              )}
              <BusinessViewProvider
                key={pageRevision}
                view={workspaceView}
                setView={setWorkspaceView}
                sidebar={
                  (me.role === "regulator" &&
                    ["星级查看", "统计分析", "权限与账号"].includes(tab)) ||
                  (me.role === "association" && tab === "星级评价")
                }
              >
                {tab === "人员账号申请" ? (
                  <StaffApplicationsPanel
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
                  me.role === "regulator" ? (
                    <MonitoringWorkspace />
                  ) : (
                    <RuntimePanel />
                  )
                ) : tab === "故障分析" ? (
                  me.role === "regulator" ? (
                    <div className="business-stack">
                      <QualityPanel purpose="alarm" />
                      <details className="prototype-related">
                        <summary>源报故障事件明细</summary>
                        <FaultsPanel />
                      </details>
                    </div>
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
                      load(
                        applied.q,
                        stationPage,
                        applied.status,
                        applied.district,
                      ).catch((e) => setError(e.message));
                    }}
                  />
                ) : tab.startsWith("星级") ? (
                  <RatingPanel
                    registerLeaveGuard={registerLeaveGuard}
                    role={me.role}
                    csrf={me.csrf_token}
                    onChanged={() => {
                      load(
                        applied.q,
                        stationPage,
                        applied.status,
                        applied.district,
                      ).catch((e) => setError(e.message));
                    }}
                  />
                ) : tab === "数据同步" ? (
                  <SyncPanel csrf={me.csrf_token} />
                ) : tab === "场站申请" ? (
                  <ApplicationPanel
                    role={me.role}
                    csrf={me.csrf_token}
                    registerLeaveGuard={registerLeaveGuard}
                  />
                ) : tab === "场站汇总" ? (
                  <DashboardOperations variant="stations" />
                ) : tab === "场站台账" ? (
                  <StationDirectory onOpen={setDetailId} />
                ) : tab === "工作台" && me.role === "operator" ? null : (
                  <section className="panel">
                    <div className="panel-title">
                      <div>
                        <h2>场站列表</h2>
                        <p>按名称查询，状态与台账同步展示</p>
                      </div>
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          setError("");
                          load(q, 1, stationStatus, district).catch((e) =>
                            setError(e.message),
                          );
                        }}
                        className="search"
                      >
                        <Search size={16} />
                        <input
                          aria-label="搜索场站"
                          placeholder="搜索场站名称"
                          value={q}
                          onChange={(e) => setQ(e.target.value)}
                        />
                        <select
                          aria-label="场站状态筛选"
                          value={stationStatus}
                          onChange={(e) => setStationStatus(e.target.value)}
                        >
                          <option value="">全部状态</option>
                          <option value="pending">待接入</option>
                          <option value="access_approved">接入通过</option>
                          <option value="accepted">验收通过</option>
                          <option value="operating">运营中</option>
                          <option value="decommissioned">已核销</option>
                        </select>
                        <input
                          aria-label="场站区域筛选"
                          value={district}
                          onChange={(e) => setDistrict(e.target.value)}
                          placeholder="全部区域"
                          maxLength={50}
                        />
                        <button disabled={stationLoading}>查询</button>
                      </form>
                    </div>
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            {[
                              "场站名称",
                              "所属区域",
                              "运行 / 业务状态",
                              "充电接口",
                              "装机功率",
                              "数据来源",
                              "操作",
                            ].map((x) => (
                              <th key={x}>{x}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {rows.map((s) => (
                            <tr key={s.id}>
                              <td>
                                <b>{s.name}</b>
                                <span>
                                  <MapPin size={12} />
                                  {s.address}
                                </span>
                              </td>
                              <td>{s.district}</td>
                              <td>
                                <em
                                  className={
                                    s.status === "operating" ? "green" : "amber"
                                  }
                                >
                                  {states[s.status] || s.status}
                                </em>
                              </td>
                              <td>{s.connectors}</td>
                              <td>{s.power_kw} kW</td>
                              <td>模拟适配器</td>
                              <td>
                                <button
                                  className="outline"
                                  aria-label={`查看详情 ${s.name}`}
                                  onClick={() => setDetailId(s.id)}
                                >
                                  查看详情
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {rows.length === 0 && (
                        <p className="empty">当前条件下没有场站数据</p>
                      )}
                    </div>
                    <div className="business-form">
                      <button
                        className="outline"
                        disabled={stationLoading || stationPage <= 1}
                        onClick={() =>
                          load(
                            applied.q,
                            stationPage - 1,
                            applied.status,
                            applied.district,
                          ).catch((e) => setError(e.message))
                        }
                      >
                        场站上一页
                      </button>
                      <span role="status">
                        {stationLoading
                          ? "场站加载中…"
                          : `第${stationPage}页 · 共${stationTotal}个场站`}
                      </span>
                      <button
                        className="outline"
                        disabled={
                          stationLoading || stationPage * 20 >= stationTotal
                        }
                        onClick={() =>
                          load(
                            applied.q,
                            stationPage + 1,
                            applied.status,
                            applied.district,
                          ).catch((e) => setError(e.message))
                        }
                      >
                        场站下一页
                      </button>
                    </div>
                  </section>
                )}
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
