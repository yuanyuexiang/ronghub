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
): NavigationGroup[] {
  const definitions: [string, typeof Map, string[]][] =
    role === "regulator"
      ? [
          ["全域监管", Map, ["工作台", "全域地图", "运行状态"]],
          [
            "场站信息管理",
            Building2,
            [
              "企业台账",
              "注册审核",
              "场站汇总",
              "场站台账",
              "场站申请",
              "数据同步",
            ],
          ],
          ["智慧安全监管", ShieldCheck, ["巡检督办", "隐患督办"]],
          ["补贴管理", Wallet, ["补贴管理"]],
          ["预警中心", Bell, ["故障分析", "告警中心"]],
          ["星级评价管理", Star, ["星级查看"]],
          [
            "统计分析",
            Activity,
            ["统计分析", "充电订单", "订单汇总", "充电时段"],
          ],
          ["权限管理", Settings, ["权限与账号", "人员账号申请"]],
          ["信息发布", FileText, ["公告与通知"]],
          ["操作日志", ClipboardCheck, ["操作日志"]],
          ["监管协议", FileText, ["监管协议"]],
          ["系统工具", Settings, ["账号安全"]],
        ]
      : [
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
          [
            "统计分析",
            Activity,
            ["统计分析", "充电订单", "订单汇总", "充电时段"],
          ],
          ["信息发布", FileText, ["公告与通知", "监管协议"]],
          ["账号管理", Settings, ["人员账号申请", "权限与账号", "账号安全"]],
        ];
  const names: Record<string, string> =
    role === "regulator"
      ? {
          注册审核: "运营商审核",
          数据同步: "场站数据同步",
          企业台账: "场站运营商管理",
          场站台账: "场站信息查询",
          场站申请: "接入管理",
          工作台: "运营总览与多维度数据筛选",
          全域地图: "可视化一张图",
          运行状态: "动态监控",
          告警中心: "告警数据",
          故障分析: "告警分析",
        }
      : { 工作台: "运营总览" };
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

export function WorkspaceNavigation({
  groups,
  tab,
  alertView,
  onNavigate,
}: {
  groups: NavigationGroup[];
  tab: string;
  alertView: AlertWorkspaceView;
  onNavigate: (item: NavigationItem) => void;
}) {
  const [search, setSearch] = useState("");
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const matches = groups
    .map((group) => ({
      ...group,
      items: group.items.filter((item) =>
        `${group.label} ${item.label}`.includes(search.trim()),
      ),
    }))
    .filter((group) => group.items.length);
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
          const active = group.items.some((item) => item.tab === tab);
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
              <div hidden={!expanded}>
                {group.items.map((item) => {
                  const selected =
                    item.tab === tab &&
                    (!item.alertView || item.alertView === alertView);
                  return (
                    <button
                      key={item.tab + (item.alertView ?? "")}
                      className={`navigation-leaf ${selected ? "selected" : ""}`}
                      aria-current={selected ? "page" : undefined}
                      onClick={() => onNavigate(item)}
                    >
                      <span>{item.label}</span>
                    </button>
                  );
                })}
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
