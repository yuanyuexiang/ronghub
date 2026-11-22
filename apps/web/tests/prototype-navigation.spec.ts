import { test, expect, navigateWorkspace } from "./fixtures";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;

test("prototype module navigation connects map and alert views without losing filter drafts", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("regulator");
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台" }).click();
  await expect(
    page.getByRole("heading", {
      name: /^(运营总览|接入汇总|多维度数据筛选)$/,
      exact: true,
    }),
  ).toBeVisible();
  const navigation = page.getByRole("navigation", { name: "业务模块导航" });
  await expect(
    navigation.getByRole("button", {
      name: "全域监管模块，展开或收起",
      exact: true,
    }),
  ).toHaveAttribute("aria-expanded", "false");
  const groupToggle = navigation.getByRole("button", {
    name: "全域监管模块，展开或收起",
    exact: true,
  });
  await groupToggle.click();
  await expect(groupToggle).toHaveAttribute("aria-expanded", "true");
  await groupToggle.click();
  await expect(
    navigation.getByRole("button", { name: "可视化一张图", exact: true }),
  ).toBeHidden();
  await groupToggle.click();
  await page.getByLabel("筛选功能菜单").fill("告警");
  await expect(
    navigation.getByRole("button", { name: "可视化一张图", exact: true }),
  ).toHaveCount(0);
  await expect(
    navigation.getByRole("button", { name: "告警数据", exact: true }),
  ).toBeVisible();
  await page.getByLabel("筛选功能菜单").fill("");
  await page.screenshot({
    path: "../../.backups/prototype-correction-overview-1440.png",
    fullPage: true,
  });
  await navigateWorkspace(page, "可视化一张图");
  await expect(
    page.getByRole("heading", { name: "可视化一张图", level: 1 }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "场站交互地图" }),
  ).toBeVisible();
  await expect(page.getByLabel("地图用途")).toBeHidden();
  await page
    .getByText("更多筛选（用途、场所、功率、星级）", { exact: true })
    .click();
  await expect(page.getByLabel("地图用途")).toBeVisible();
  await page
    .getByText("更多筛选（用途、场所、功率、星级）", { exact: true })
    .click();
  await page.screenshot({
    path: "../../.backups/prototype-correction-map-1440.png",
    fullPage: true,
  });
  await navigateWorkspace(page, "告警数据");
  await expect(
    page.getByRole("heading", { name: "告警数据", level: 1 }),
  ).toBeVisible();
  await page.getByLabel("告警检索名称").fill("保留中的查询草稿");
  await navigateWorkspace(page, "告警配置");
  await expect(
    page.getByRole("heading", { name: "告警配置", level: 1, exact: true }),
  ).toBeVisible();
  await navigateWorkspace(page, "告警数据");
  await expect(page.getByLabel("告警检索名称")).toHaveValue("保留中的查询草稿");
  const request = page.waitForRequest(
    (r) =>
      r.method() === "GET" &&
      new URL(r.url()).pathname === "/api/v1/alerts" &&
      new URL(r.url()).searchParams.get("category") === "device_offline",
  );
  await page
    .getByRole("navigation", { name: "告警数据分类" })
    .getByRole("button", { name: "设备离线告警", exact: true })
    .click();
  expect(new URL((await request).url()).searchParams.get("q")).toBe(
    "保留中的查询草稿",
  );
  await expect(page.getByLabel("告警筛选类型")).toHaveValue("device_offline");
  await page.screenshot({
    path: "../../.backups/prototype-correction-alerts-1440.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 768, height: 1024 });
  await expect(navigation).toBeHidden();
  await page.getByRole("button", { name: "展开或收起功能导航" }).click();
  await expect(navigation).toBeVisible();
  await navigateWorkspace(page, "可视化一张图");
  await expect(navigation).toBeHidden();
  await expect(
    page.getByRole("heading", { name: "可视化一张图", level: 1 }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../../.backups/prototype-correction-map-768.png",
    fullPage: true,
  });
});
