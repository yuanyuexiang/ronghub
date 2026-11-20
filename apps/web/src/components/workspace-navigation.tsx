"use client";
import { useEffect, useRef, useState } from "react";
import {
  ChevronRight,
  Folder,
  FolderOpen,
  FileText,
  Search,
} from "lucide-react";
export type AlertWorkspaceView =
  "events" | "rules" | "sources" | "notifications";
export type NavigationItem = {
  tab: string;
  label: string;
  alertView?: AlertWorkspaceView;
  view?: string;
  children?: NavigationItem[];
  matchViews?: string[];
  branchOnly?: boolean;
};
export type NavigationGroup = {
  label: string;
  icon: typeof Folder;
  items: NavigationItem[];
  entry?: NavigationItem;
};
const item = (
  tab: string,
  label: string,
  view?: string,
  children?: NavigationItem[],
): NavigationItem => ({
  tab,
  label,
  ...(view ? { view } : {}),
  ...(children?.length ? { children } : {}),
});
const group = (label: string, items: NavigationItem[]): NavigationGroup => ({
  label,
  icon: Folder,
  items,
});
const standalone = (entry: NavigationItem): NavigationGroup => ({
  label: entry.label,
  icon: FileText,
  items: [],
  entry,
});
export const workspaceDefaults: Record<string, string> = {
  工作台: "",
  场站申请: "all",
  巡检督办: "tasks",
  补贴管理: "claims",
  补贴申报: "claims",
  星级查看: "applications",
  星级评价: "applications",
  星级申报: "applications",
  统计分析: "energy",
  权限与账号: "accounts",
  公告与通知: "information",
};
export function navigationMatches(
  item: NavigationItem,
  tab: string,
  alertView: AlertWorkspaceView,
  view: string,
) {
  return (
    !item.branchOnly &&
    item.tab === tab &&
    (!item.alertView || item.alertView === alertView) &&
    (item.view === undefined ||
      item.view === (view || workspaceDefaults[tab] || "") ||
      !!item.matchViews?.includes(view || workspaceDefaults[tab] || ""))
  );
}
export function flattenNavigation(groups: NavigationGroup[]) {
  const flatten = (items: NavigationItem[]): NavigationItem[] =>
    items.flatMap((i) => [i, ...flatten(i.children ?? [])]);
  return groups.flatMap((g) => [
    ...(g.entry ? [g.entry] : []),
    ...flatten(g.items),
  ]);
}
/** Business menus from the rendered canvas masters, never the prototype page tree. */
export function workspaceNavigation(
  menu: string[],
  role: string,
  notifications: boolean,
  permissions: readonly string[] = [],
): NavigationGroup[] {
  const has = (p: string) => permissions.includes(p);
  const data = has("statistics.read") && has("stations.read");
  let groups: NavigationGroup[];
  if (role === "regulator")
    groups = [
      group("全域监管", [
        item("全域地图", "可视化一张图"),
        item("工作台", "运营总览与多维度数据筛选"),
        item("运行状态", "动态监控"),
      ]),
      group("场站信息管理", [
        item("企业台账", "场站运营商管理"),
        item("场站汇总", "场站汇总"),
        item("场站台账", "场站数据同步与场站信息查询"),
        {
          ...item("场站申请", "接入管理", "all"),
          matchViews: ["access", "acceptance", "change"],
        },
        {
          ...item("场站申请", "投运与核销管理", "commission"),
          matchViews: ["decommission"],
        },
      ]),
      group("智慧安全监管", [
        item("巡检督办", "巡检任务管理", "tasks"),
        item("隐患督办", "隐患督办管理"),
      ]),
      group("补贴管理", [
        item("补贴管理", "补贴申请记录", "claims"),
        item("补贴管理", "补贴规则配置", "policies"),
        item("补贴管理", "电量数据", "energy"),
        ...(has("orders.read") && has("statistics.read")
          ? [item("统计分析", "运营商充电及补贴统计分析", "combined")]
          : []),
        {
          ...item("补贴管理", "补贴统计", undefined, [
            item("补贴管理", "运营商统计", "statistics.operator"),
            item("补贴管理", "补贴明细", "statistics.detail"),
          ]),
          branchOnly: true,
        },
      ]),
      group("预警中心", [
        item("故障分析", "告警分析"),
        { ...item("告警中心", "告警数据"), alertView: "events" },
        { ...item("告警中心", "告警配置"), alertView: "rules" },
      ]),
      group("星级评价管理", [
        item("星级查看", "评价标准查看", "standards"),
        item("星级查看", "企业申报查看", "applications"),
        ...(data ? [item("星级查看", "数据支撑查看", "data")] : []),
        item("星级查看", "评审过程查看", "process"),
        item("星级查看", "评审结果查看", "results"),
        item("星级查看", "公示查询查看", "publications"),
      ]),
      group("统计分析", [
        item("统计分析", "建站统计（区域）", "district"),
        item("统计分析", "建站统计（运营商）", "operator"),
        item("统计分析", "利用率统计", "utilization"),
        item("统计分析", "充电量统计", "energy"),
        item("充电时段", "充电时段统计"),
        item("统计分析", "用电分析", "power"),
      ]),
      group("权限管理", [
        ...(has("accounts.manage")
          ? [item("权限与账号", "账号管理", "accounts")]
          : []),
        ...(has("roles.manage")
          ? [item("权限与账号", "角色管理", "roles")]
          : []),
      ]),
      group("信息发布", [
        item("公告与通知", "新增发布资讯", "information"),
        item("公告与通知", "自定义通知发布", "notice"),
      ]),
      standalone(item("操作日志", "操作日志")),
      standalone(item("监管协议", "监管协议开发")),
    ];
  else if (role === "association")
    groups = [
      group("全域监管", [
        item("场站地图", "可视化一张图"),
        item("场站台账", "多维度数据筛选"),
      ]),
      group("星级评价管理", [
        item("星级评价", "评价标准管理", "standards"),
        item("星级评价", "企业申报管理", "applications"),
        ...(data ? [item("星级评价", "数据支撑评价", "data")] : []),
        item("星级评价", "评审过程管理", "process"),
        item("星级评价", "评价结果管理", "results"),
        item("星级评价", "公示查询服务", "publications"),
        item("星级评价", "监管协同应用", "collaboration"),
      ]),
    ];
  else
    groups = [
      standalone(item("工作台", "接入汇总")),
      group("待办事项", [
        item("公告与通知", "通知", "notice"),
        item("整改任务", "巡检任务", "tasks"),
        item("隐患督办", "隐患督办"),
      ]),
      group("接入申请", [
        item("场站申请", "接入场站管理", "all"),
        item("场站申请", "场站接入进展查看", "progress"),
        item("场站台账", "信息推送"),
      ]),
      group("补贴申报", [
        item("补贴申报", "补贴申请管理", "claims"),
        ...(has("orders.read") && has("statistics.read")
          ? [item("统计分析", "充电与补贴数据统计", "combined")]
          : []),
        item("补贴申报", "补贴记录查询与导出", "records"),
      ]),
      group("星级站申报", [
        ...(has("ratings.create")
          ? [item("星级申报", "申报材料填报", "create")]
          : []),
        item("星级申报", "评定进度查询", "applications"),
      ]),
    ];
  const filter = (items: NavigationItem[]): NavigationItem[] =>
    items.flatMap((i) => {
      const children = filter(i.children ?? []);
      if (i.branchOnly) return children.length ? [{ ...i, children }] : [];
      return menu.includes(i.tab)
        ? [{ ...i, ...(i.children ? { children } : {}) }]
        : [];
    });
  // Notification management remains inside the alert workspace with its independent permission.
  void notifications;
  return groups
    .map((g) => ({ ...g, items: filter(g.items) }))
    .filter((g) => (g.entry ? menu.includes(g.entry.tab) : g.items.length > 0));
}
/** Existing capabilities stay reachable outside the customer business sidebar. */
export function auxiliaryNavigation(
  menu: string[],
  role: string,
  permissions: readonly string[] = [],
): NavigationItem[] {
  const primary = new Set(
    flattenNavigation(workspaceNavigation(menu, role, false, permissions))
      .filter((i) => !i.branchOnly)
      .map((i) => i.tab),
  );
  const names: Record<string, string> = {
    注册审核: "运营商审核",
    数据同步: "场站数据同步",
    操作日志: "操作日志",
    工作台: "运营总览",
  };
  const entries = menu
    .filter((tab) => !primary.has(tab))
    .map((tab) => item(tab, names[tab] ?? tab));
  if (role === "operator") {
    if (menu.includes("公告与通知"))
      entries.push(item("公告与通知", "公告与通知", "all"));
    if (menu.includes("统计分析"))
      entries.push(item("统计分析", "统计分析", "energy"));
  }
  return entries;
}
export function WorkspaceNavigation({
  groups,
  tab,
  alertView,
  view = "",
  onNavigate,
}: {
  groups: NavigationGroup[];
  tab: string;
  alertView: AlertWorkspaceView;
  view?: string;
  onNavigate: (i: NavigationItem) => boolean | void;
}) {
  const [search, setSearch] = useState("");
  const [opened, setOpened] = useState<string[]>([]);
  const [anchors, setAnchors] = useState<
    Record<string, { left: number; top: number }>
  >({});
  const root = useRef<HTMLElement>(null);
  const triggers = useRef<Record<string, HTMLButtonElement>>({});
  useEffect(() => {
    if (!opened.length) return;
    const outside = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpened([]);
    };
    const escape = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpened([]);
        triggers.current[opened[0]]?.focus();
        e.stopPropagation();
      }
    };
    const resize = () => setOpened([]);
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    window.addEventListener("resize", resize);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
      window.removeEventListener("resize", resize);
    };
  }, [opened]);
  const toggle = (key: string, button: HTMLButtonElement, count: number) => {
    const rect = button.getBoundingClientRect();
    triggers.current[key] = button;
    setAnchors((current) => ({
      ...current,
      [key]: {
        left: Math.max(
          rect.right + 6,
          (root.current?.getBoundingClientRect().right ?? 0) + 6,
        ),
        top: Math.max(
          76,
          Math.min(
            rect.top,
            innerHeight - Math.min(count * 44 + 16, innerHeight - 110) - 16,
          ),
        ),
      },
    }));
    setOpened((current) =>
      current.includes(key)
        ? current.filter((k) => k !== key && !k.startsWith(key + "/"))
        : [...current.filter((k) => key.startsWith(k + "/")), key],
    );
  };
  const navigate = (item: NavigationItem) => {
    if (onNavigate(item) !== false) setOpened([]);
  };
  function filterItems(
    items: NavigationItem[],
    parent: string,
  ): NavigationItem[] {
    return items.flatMap((i) => {
      const children = filterItems(i.children ?? [], parent + " " + i.label);
      return `${parent} ${i.label}`.includes(search.trim()) || children.length
        ? [{ ...i, children }]
        : [];
    });
  }
  function renderItems(items: NavigationItem[], path: string): React.ReactNode {
    return items.map((i) => {
      const key = path + "/" + i.label;
      const branch = !!i.children?.length;
      const expanded = !!search || opened.includes(key);
      const selected = navigationMatches(i, tab, alertView, view);
      return (
        <div className="navigation-entry" key={key}>
          <button
            className={`navigation-leaf ${selected ? "selected" : ""}`}
            aria-current={selected ? "page" : undefined}
            aria-expanded={branch ? expanded : undefined}
            onClick={(e) =>
              branch
                ? toggle(key, e.currentTarget, i.children!.length)
                : navigate(i)
            }
          >
            <span>{i.label}</span>
            {branch && <ChevronRight size={14} />}
          </button>
          {branch && (
            <div
              className="navigation-flyout"
              hidden={!expanded}
              style={anchors[key]}
            >
              {renderItems(i.children!, key)}
            </div>
          )}
        </div>
      );
    });
  }
  const matches = groups
    .map((g) => ({ ...g, items: filterItems(g.items, g.label) }))
    .filter(
      (g) => g.items.length || (g.entry && g.label.includes(search.trim())),
    );
  return (
    <>
      <label className="navigation-search">
        <Search size={15} />
        <input
          aria-label="筛选功能菜单"
          placeholder="搜索功能菜单"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </label>
      <nav
        ref={root}
        className={`module-navigation ${search ? "navigation-searching" : ""}`}
        aria-label="业务模块导航"
      >
        {matches.map((g) => {
          const expanded = !!search || opened.includes(g.label);
          const active = flattenNavigation([g]).some((i) =>
            navigationMatches(i, tab, alertView, view),
          );
          if (g.entry)
            return (
              <section
                className={`navigation-group ${active ? "active-group" : ""}`}
                key={g.label}
              >
                <button
                  className={`navigation-group-toggle navigation-top-link ${active ? "selected" : ""}`}
                  aria-current={active ? "page" : undefined}
                  onClick={() => navigate(g.entry!)}
                >
                  <FileText size={17} />
                  <span>{g.label}</span>
                </button>
              </section>
            );
          return (
            <section
              className={`navigation-group ${active ? "active-group" : ""}`}
              key={g.label}
            >
              <button
                className="navigation-group-toggle"
                aria-label={`${g.label}模块，展开或收起`}
                aria-expanded={expanded}
                onClick={(e) =>
                  toggle(g.label, e.currentTarget, g.items.length)
                }
              >
                {expanded ? <FolderOpen size={17} /> : <Folder size={17} />}
                <span>{g.label}</span>
                <ChevronRight size={14} />
              </button>
              <div
                className="navigation-flyout"
                hidden={!expanded}
                style={anchors[g.label]}
              >
                {renderItems(g.items, g.label)}
              </div>
            </section>
          );
        })}
        {!matches.length && (
          <p className="navigation-empty">没有匹配的功能，请更换关键词。</p>
        )}
      </nav>
    </>
  );
}
export function AuxiliaryNavigation({
  items,
  onNavigate,
}: {
  items: NavigationItem[];
  onNavigate: (i: NavigationItem) => boolean | void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        root.current?.querySelector("button")?.focus();
      }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  if (!items.length) return null;
  return (
    <div ref={root} className="auxiliary-navigation">
      <button
        aria-expanded={open}
        aria-controls="auxiliary-menu"
        onClick={() => setOpen(!open)}
      >
        更多功能
      </button>
      <nav id="auxiliary-menu" aria-label="辅助功能导航" hidden={!open}>
        {items.map((i) => (
          <button
            key={i.tab + ":" + (i.view ?? "")}
            onClick={() => {
              if (onNavigate(i) !== false) setOpen(false);
            }}
          >
            {i.label}
          </button>
        ))}
      </nav>
    </div>
  );
}
