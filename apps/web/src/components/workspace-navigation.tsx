"use client";

import { useState } from "react";
import {
  Activity,
  Bell,
  Building2,
  ChevronDown,
  ClipboardCheck,
  FileText,
  LayoutDashboard,
  Map,
  Search,
  Settings,
  ShieldCheck,
  Star,
  Wallet,
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
};
export type NavigationGroup = {
  label: string;
  icon: typeof Map;
  items: NavigationItem[];
};

export function workspaceNavigation(
  menu: string[],
  role: string,
  notifications: boolean,
  permissions: readonly string[] = [],
): NavigationGroup[] {
  if (role === "regulator")
    return regulatorNavigation(menu, notifications, permissions);
  const definitions: [string, typeof Map, string[]][] = [
    [
      role === "association" ? "全域监管" : "接入汇总",
      LayoutDashboard,
      ["工作台", "场站地图"],
    ],
    [
      "场站信息管理",
      Building2,
      ["企业台账", "企业资料", "场站台账", "场站申请"],
    ],
    [
      "安全与预警",
      ShieldCheck,
      ["企业告警", "整改任务", "隐患督办", "运行状态", "故障分析"],
    ],
    ["补贴管理", Wallet, ["补贴申报"]],
    [
      role === "association" ? "星级评价管理" : "星级申报",
      Star,
      ["星级评价", "星级申报"],
    ],
    ["统计分析", Activity, ["统计分析", "充电订单", "订单汇总", "充电时段"]],
    ["信息发布", FileText, ["公告与通知", "监管协议"]],
    ["账号管理", Settings, ["人员账号申请", "权限与账号", "账号安全"]],
  ];
  const names: Record<string, string> = { 工作台: "运营总览" };
  return definitions
    .map(([label, icon, tabs]) => {
      const items: NavigationItem[] = tabs
        .filter((tab) => menu.includes(tab))
        .map((tab) => ({
          tab,
          label: names[tab] ?? tab,
          ...(tab === "告警中心" ? { alertView: "events" as const } : {}),
        }));
      if (label === "预警中心" && menu.includes("告警中心")) {
        items.push({ tab: "告警中心", label: "告警配置", alertView: "rules" });
        if (notifications)
          items.push({
            tab: "告警中心",
            label: "通知配置",
            alertView: "notifications",
          });
      }
      return { label, icon, items };
    })
    .filter((group) => group.items.length > 0);
}

export const workspaceDefaults: Record<string, string> = {
  工作台: "",
  场站申请: "all",
  巡检督办: "tasks",
  补贴管理: "claims",
  星级查看: "applications",
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
    item.tab === tab &&
    (!item.alertView || item.alertView === alertView) &&
    (item.view === undefined ||
      item.view === (view || workspaceDefaults[tab] || "") ||
      !!item.matchViews?.includes(view || workspaceDefaults[tab] || ""))
  );
}
export function flattenNavigation(groups: NavigationGroup[]) {
  const flatten = (items: NavigationItem[]): NavigationItem[] =>
    items.flatMap((item) => [item, ...flatten(item.children ?? [])]);
  return groups.flatMap((group) => flatten(group.items));
}
function regulatorNavigation(
  menu: string[],
  notifications: boolean,
  permissions: readonly string[],
): NavigationGroup[] {
  const allowed = (tab: string) => menu.includes(tab);
  const has = (permission: string) => permissions.includes(permission);
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
  const groups: NavigationGroup[] = [
    {
      label: "全域监管",
      icon: Map,
      items: [
        item("全域地图", "可视化一张图"),
        item("工作台", "运营总览与多维度数据筛选"),
        item("运行状态", "动态监控"),
      ],
    },
    {
      label: "场站信息管理",
      icon: Building2,
      items: [
        item("企业台账", "场站运营商管理", undefined, [
          item("注册审核", "运营商审核"),
        ]),
        item("场站汇总", "场站汇总"),
        item("场站台账", "场站数据同步与场站信息查询", undefined, [
          item("数据同步", "场站数据同步"),
        ]),
        {
          ...item("场站申请", "接入管理", "access", [
            item("场站申请", "电站接入审核", "access"),
            item("场站申请", "竣工验收审核", "acceptance"),
          ]),
          matchViews: ["all", "change"],
        },
        {
          ...item("场站申请", "投运与核销管理", "commission"),
          matchViews: ["decommission"],
        },
      ],
    },
    {
      label: "智慧安全监管",
      icon: ShieldCheck,
      items: [
        item(
          "巡检督办",
          "巡检任务管理",
          "tasks",
          has("inspections.assign")
            ? [
                item("巡检督办", "巡检计划创建", "plans"),
                item("巡检督办", "单站快捷下发", "quick"),
              ]
            : [],
        ),
        item("隐患督办", "隐患督办管理"),
      ],
    },
    {
      label: "补贴管理",
      icon: Wallet,
      items: [
        item("补贴管理", "补贴申请记录", "claims"),
        item("补贴管理", "补贴规则配置", "policies"),
        item("补贴管理", "电量数据", "energy"),
        item("补贴管理", "补贴统计", "statistics", [
          item("补贴管理", "运营商统计", "statistics.operator"),
          item("补贴管理", "场站统计", "statistics.station"),
          item("补贴管理", "补贴明细", "statistics.detail"),
        ]),
      ],
    },
    {
      label: "预警中心",
      icon: Bell,
      items: [
        item("故障分析", "告警分析"),
        {
          ...item("告警中心", "告警数据"),
          alertView: "events",
          children: [
            item("告警中心", "电站指标告警", "station_metric"),
            item("告警中心", "设备充电告警", "device_charging"),
            item("告警中心", "设备离线告警", "device_offline"),
            item("告警中心", "设备故障告警", "device_fault"),
            item("告警中心", "电量功率异常", "energy_power"),
            item("告警中心", "未接入设备告警", "unconnected_device"),
          ],
        },
        {
          ...item("告警中心", "告警配置"),
          alertView: "rules",
          children: [
            {
              ...item("告警中心", "指标阈值告警", "station_metric"),
              alertView: "rules",
            },
            {
              ...item("告警中心", "设备充电告警配置", "device_charging"),
              alertView: "rules",
            },
            ...(notifications
              ? [
                  {
                    ...item("告警中心", "通知配置"),
                    alertView: "notifications" as const,
                  },
                ]
              : []),
          ],
        },
      ],
    },
    {
      label: "星级评价管理",
      icon: Star,
      items: [
        item("星级查看", "评价标准查看", "standards"),
        item("星级查看", "企业申报查看", "applications"),
        ...(has("statistics.read") && has("stations.read")
          ? [item("星级查看", "数据支撑查看", "data")]
          : []),
        item("星级查看", "评审过程查看", "process"),
        item("星级查看", "评价结果查看", "results"),
        item("星级查看", "公示查询查看", "publications"),
      ],
    },
    {
      label: "统计分析",
      icon: Activity,
      items: [
        item("统计分析", "建站统计（区域）", "district"),
        item("统计分析", "建站统计（运营商）", "operator"),
        item("统计分析", "利用率统计", "utilization"),
        item("统计分析", "充电量统计", "energy"),
        item("充电时段", "充电时段统计"),
        item("统计分析", "用电分析", "power"),
      ],
    },
    {
      label: "权限管理",
      icon: Settings,
      items: [
        ...(has("accounts.manage")
          ? [item("权限与账号", "账号管理", "accounts")]
          : []),
        ...(has("roles.manage")
          ? [item("权限与账号", "角色管理", "roles")]
          : []),
      ],
    },
    {
      label: "信息发布",
      icon: FileText,
      items: [
        item("公告与通知", "新增发布资讯", "information"),
        item("公告与通知", "自定义通知发布", "notice"),
      ],
    },
    {
      label: "操作日志",
      icon: ClipboardCheck,
      items: [item("操作日志", "系统操作日志")],
    },
    {
      label: "监管协议开发",
      icon: FileText,
      items: [item("监管协议", "监管协议")],
    },
    {
      label: "系统工具",
      icon: Settings,
      items: [
        ...(has("orders.read") && has("subsidy.read")
          ? [item("统计分析", "充电与补贴报表", "combined")]
          : []),
        item("人员账号申请", "人员账号申请"),
        item("充电订单", "充电订单"),
        item("订单汇总", "订单汇总"),
        item("账号安全", "账号安全"),
      ],
    },
  ];
  const filter = (items: NavigationItem[]): NavigationItem[] =>
    items
      .filter((i) => allowed(i.tab))
      .map((i) => ({
        ...i,
        children: filter(i.children ?? []).map((child) => ({
          ...child,
          alertView: child.alertView ?? i.alertView,
        })),
      }));
  return groups
    .map((group) => ({ ...group, items: filter(group.items) }))
    .filter((group) => group.items.length);
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
  onNavigate: (item: NavigationItem) => void;
}) {
  const [search, setSearch] = useState("");
  const [collapsed, setCollapsed] = useState<string[]>([]);
  function filterItems(
    items: NavigationItem[],
    parent: string,
  ): NavigationItem[] {
    return items.flatMap((item) => {
      const children = filterItems(
        item.children ?? [],
        parent + " " + item.label,
      );
      return `${parent} ${item.label}`.includes(search.trim()) ||
        children.length
        ? [{ ...item, children }]
        : [];
    });
  }
  const matches = groups
    .map((group) => ({
      ...group,
      items: filterItems(group.items, group.label),
    }))
    .filter((group) => group.items.length);
  function renderItems(items: NavigationItem[], depth = 0): React.ReactNode {
    return items.map((item) => {
      const selected = navigationMatches(item, tab, alertView, view);
      const childSelected = item.children?.some((child) =>
        navigationMatches(child, tab, alertView, view),
      );
      return (
        <div
          className="navigation-entry"
          key={`${item.tab}:${item.alertView ?? ""}:${item.view ?? ""}:${item.label}`}
        >
          <button
            className={`navigation-leaf ${selected && !childSelected ? "selected" : ""}`}
            style={{ paddingLeft: `${40 + depth * 16}px` }}
            aria-current={selected && !childSelected ? "page" : undefined}
            onClick={() => onNavigate(item)}
          >
            <span>{item.label}</span>
          </button>
          {!!item.children?.length && (
            <div className="navigation-subtree">
              {renderItems(item.children, depth + 1)}
            </div>
          )}
        </div>
      );
    });
  }
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
      <nav className="module-navigation" aria-label="业务模块导航">
        {matches.map((group) => {
          const Icon = group.icon;
          const expanded = !!search || !collapsed.includes(group.label);
          const active = flattenNavigation([group]).some((item) =>
            navigationMatches(item, tab, alertView, view),
          );
          return (
            <section
              className={`navigation-group ${active ? "active-group" : ""}`}
              key={group.label}
            >
              <button
                className="navigation-group-toggle"
                aria-label={`${group.label}模块，展开或收起`}
                aria-expanded={expanded}
                onClick={() =>
                  setCollapsed((current) =>
                    current.includes(group.label)
                      ? current.filter((label) => label !== group.label)
                      : [...current, group.label],
                  )
                }
              >
                <Icon size={17} />
                <span>{group.label}</span>
                <ChevronDown size={14} className={expanded ? "expanded" : ""} />
              </button>
              <div hidden={!expanded}>{renderItems(group.items)}</div>
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
