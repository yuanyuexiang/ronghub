import { test, expect, type Page } from "./fixtures";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("regulator");
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
}
function navigation(page: Page) {
  return page.getByRole("navigation", { name: "业务模块导航", exact: true });
}
test("regulator sidebar exposes customer modules and every primary destination", async ({
  page,
}) => {
  await login(page);
  const nav = navigation(page);
  for (const name of [
    "可视化一张图",
    "运营总览与多维度数据筛选",
    "动态监控",
    "场站运营商管理",
    "运营商审核",
    "场站汇总",
    "场站数据同步与场站信息查询",
    "接入管理",
    "投运与核销管理",
    "巡检任务管理",
    "巡检计划创建",
    "隐患督办管理",
    "补贴申请记录",
    "补贴规则配置",
    "电量数据",
    "运营商统计",
    "场站统计",
    "补贴明细",
    "电站指标告警",
    "设备充电告警",
    "设备离线告警",
    "设备故障告警",
    "电量功率异常",
    "未接入设备告警",
    "指标阈值告警",
    "设备充电告警配置",
    "通知配置",
    "评价标准查看",
    "企业申报查看",
    "数据支撑查看",
    "评审过程查看",
    "评价结果查看",
    "公示查询查看",
    "建站统计（区域）",
    "建站统计（运营商）",
    "利用率统计",
    "充电量统计",
    "充电时段统计",
    "用电分析",
    "账号管理",
    "角色管理",
    "新增发布资讯",
    "自定义通知发布",
    "系统操作日志",
    "监管协议",
  ])
    await expect(nav.getByRole("button", { name, exact: true })).toBeVisible();
  await page.screenshot({
    path: "../../.backups/regulator-sidebar-complete.png",
    fullPage: true,
  });
});
test("sidebar changes select real views with URL reload and browser history", async ({
  page,
}) => {
  await login(page);
  const nav = navigation(page);
  await nav
    .getByRole("button", { name: "建站统计（运营商）", exact: true })
    .click();
  await expect(page).toHaveURL(/view=operator/);
  await expect(
    page.getByRole("heading", { name: "运营商建站统计", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "运营商建站统计", exact: true }),
  ).toBeVisible();
  await nav.getByRole("button", { name: "角色管理", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "业务权限角色", exact: true }),
  ).toBeVisible();
  await page.goBack();
  await expect(
    page.getByRole("heading", { name: "运营商建站统计", exact: true }),
  ).toBeVisible();
  await nav
    .getByRole("button", { name: "投运与核销管理", exact: true })
    .click();
  await expect(page.getByLabel("筛选申请类型", { exact: true })).toHaveValue(
    "commission",
  );
  await page.getByRole("button", { name: "核销申请", exact: true }).click();
  await expect(page.getByLabel("筛选申请类型", { exact: true })).toHaveValue(
    "decommission",
  );
  await nav.getByRole("button", { name: "运营商统计", exact: true }).click();
  await expect(page.getByLabel("补贴统计维度", { exact: true })).toHaveValue(
    "operator",
  );
});
test("rating, content and alert destinations do not collapse to their module default", async ({
  page,
}) => {
  await login(page);
  const nav = navigation(page);
  const destinations = [
    ["评价标准查看", "星级评价标准"],
    ["数据支撑查看", "数据支撑评价"],
    ["评审过程查看", "评审过程管理"],
    ["评价结果查看", "评价结果查看"],
    ["公示查询查看", "星级公示查询"],
  ] as const;
  for (const [link, heading] of destinations) {
    const reviewed =
      link === "评价结果查看"
        ? page.waitForResponse(
            (r) =>
              new URL(r.url()).pathname === "/api/v1/ratings/applications" &&
              new URL(r.url()).searchParams.get("status") === "reviewed",
          )
        : null;
    await nav.getByRole("button", { name: link, exact: true }).click();
    if (reviewed) {
      expect((await reviewed).status()).toBe(200);
      await expect(
        page.getByLabel("星级筛选状态", { exact: true }),
      ).toHaveValue("reviewed");
    }
    await expect(
      page.getByRole("heading", { name: heading, exact: true, level: 2 }),
    ).toBeVisible();
    await expect(
      nav.getByRole("button", { name: link, exact: true }),
    ).toHaveAttribute("aria-current", "page");
  }
  for (const [name, kind] of [
    ["新增发布资讯", "information"],
    ["自定义通知发布", "notice"],
  ] as const) {
    const response = page.waitForResponse(
      (r) =>
        new URL(r.url()).pathname === "/api/v1/content" &&
        new URL(r.url()).searchParams.get("kind") === kind,
    );
    await nav.getByRole("button", { name, exact: true }).click();
    expect((await response).status()).toBe(200);
  }
  for (const [name, category] of [
    ["设备离线告警", "device_offline"],
    ["电量功率异常", "energy_power"],
  ] as const) {
    const response = page.waitForResponse(
      (r) =>
        new URL(r.url()).pathname === "/api/v1/alerts" &&
        new URL(r.url()).searchParams.get("category") === category,
    );
    await nav.getByRole("button", { name, exact: true }).click();
    expect((await response).status()).toBe(200);
    await expect(page.getByLabel("告警筛选类型", { exact: true })).toHaveValue(
      category,
    );
  }
});

test("alarm configuration destinations scope actual rule groups", async ({
  page,
}) => {
  await login(page);
  for (const [name, group] of [
    ["指标阈值告警", "station"],
    ["设备充电告警配置", "device"],
  ] as const) {
    const response = page.waitForResponse(
      (r) =>
        new URL(r.url()).pathname === "/api/v1/alerts/configured-rules" &&
        new URL(r.url()).searchParams.get("kind_group") === group,
    );
    await navigation(page).getByRole("button", { name, exact: true }).click();
    expect((await response).status()).toBe(200);
  }
});
