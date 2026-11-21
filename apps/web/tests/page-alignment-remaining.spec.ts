import { test, expect, navigateWorkspace, type Page } from "./fixtures";
import { readFileSync } from "node:fs";
import baseline from "./fixtures/canvas-navigation.json";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, user: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(user);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
}
test("remaining regulator primary views follow prototype business objects", async ({
  page,
}) => {
  await login(page, "regulator");
  await navigateWorkspace(page, "动态监控");
  const monitor = page.getByRole("region", {
    name: "动态监控工作区",
    exact: true,
  });
  await expect(
    monitor.getByRole("heading", { name: "监控数据", exact: true }),
  ).toBeVisible();
  await expect(
    monitor.getByRole("button", { name: "查询监控", exact: true }),
  ).toBeEnabled();
  await monitor.getByLabel("监控要素类别").selectOption("electrical");
  const response = page.waitForResponse(
    (r) =>
      r.url().includes("/page-views/monitoring?") &&
      r.url().includes("element=electrical"),
  );
  await monitor.getByRole("button", { name: "查询监控", exact: true }).click();
  expect((await response).status()).toBe(200);
  await expect(monitor).toContainText("待接入");
  for (const leaf of ["接入管理", "投运与核销管理"]) {
    await navigateWorkspace(page, leaf);
    await expect(
      page.getByRole("columnheader", { name: "当前星级", exact: true }).first(),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "场站列表", exact: true }),
    ).toBeVisible();
  }
  await navigateWorkspace(page, "补贴规则配置");
  await expect(
    page.getByRole("region", { name: "充换电补贴规则" }),
  ).toBeVisible();
  await navigateWorkspace(page, "电量数据");
  await expect(
    page.getByRole("columnheader", { name: "日期", exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("columnheader", { name: /补贴/ }).first(),
  ).toBeVisible();
  for (const [leaf, title] of [
    ["告警分析", "告警分析汇总"],
    ["利用率统计", "设备平均利用率统计"],
  ]) {
    await navigateWorkspace(page, leaf);
    await expect(
      page.getByRole("heading", { name: title, exact: true }),
    ).toBeVisible();
  }
  await navigateWorkspace(page, "用电分析");
  await expect(page.getByText("同比", { exact: false }).first()).toBeVisible();
  await expect(
    page.getByRole("button", { name: "查询统计", exact: true }),
  ).toBeEnabled();
  await page.screenshot({
    path: "../../.backups/remaining-regulator-power.png",
  });
});
test("association indicators and collaboration expose classified standards and application queue", async ({
  page,
}) => {
  await login(page, "association");
  await navigateWorkspace(page, "评价标准管理");
  await expect(
    page.getByRole("region", { name: "分类评价指标", exact: true }),
  ).toBeVisible();
  const createStandard = page.getByRole("button", {
    name: "新增指标 / 配置新版本",
    exact: true,
  });
  await createStandard.click();
  await expect(
    page.getByRole("heading", { name: "标准编辑与暂存", exact: true }),
  ).toBeVisible();
  await expect(createStandard).toBeDisabled();
  await page
    .getByRole("button", { name: "配置新标准版本", exact: true })
    .click();
  await navigateWorkspace(page, "监管协同应用");
  const area = page.getByRole("region", { name: "监管协同应用", exact: true });
  await expect(
    area.getByRole("columnheader", { name: "申报时间", exact: true }),
  ).toBeVisible();
  await expect(
    area.getByRole("button", { name: "全部同步", exact: true }),
  ).toBeDisabled();
  await expect(
    area.getByRole("button", { name: "查询协同申报记录", exact: true }),
  ).toBeEnabled();
  await page.screenshot({
    path: "../../.backups/remaining-association-collaboration.png",
  });
  await navigateWorkspace(page, "公示查询服务");
  await expect(
    page.getByRole("tab", { name: "充电站", exact: true }),
  ).toBeVisible();
  const changed = page.waitForResponse(
    (r) =>
      r.url().includes("/ratings/publications?") &&
      r.url().includes("station_type=swapping"),
  );
  await page.getByRole("tab", { name: "换电站", exact: true }).click();
  expect((await changed).status()).toBe(200);
});
test("operator dedicated overview exposes map, ranking and scoped shortcuts on desktop and mobile", async ({
  page,
}) => {
  await login(page, "operator_a");
  await expect(
    page.getByLabel("运营商接入总览", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "昨日电量排名", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "监管通知", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel("昨日电量排名", { exact: true }).getByRole("status"),
  ).toHaveCount(0);
  await page.screenshot({
    path: "../../.backups/remaining-operator-overview.png",
  });
  await page.setViewportSize({ width: 390, height: 900 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../../.backups/remaining-operator-overview-390.png",
  });
});

test("all 58 canvas menu leaves render settled business content without runtime or business API errors", async ({
  browser,
}) => {
  test.setTimeout(180000);
  const fs = await import("node:fs");
  fs.mkdirSync("../../.backups/page-alignment-followup", { recursive: true });
  type Entry = { label: string; children: Entry[] };
  const leaves = (entries: Entry[]): string[] =>
    entries.flatMap((e) =>
      e.children.length ? leaves(e.children) : [e.label],
    );
  const inventory: unknown[] = [];
  for (const role of ["regulator", "association", "operator"] as const) {
    const context = await browser.newContext({
        viewport: { width: 1440, height: 1000 },
      }),
      page = await context.newPage();
    const errors: string[] = [],
      failed: string[] = [],
      pending = new Set<unknown>();
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("request", (r) => {
      if (r.url().includes("/api/v1/")) pending.add(r);
    });
    page.on("requestfinished", (r) => pending.delete(r));
    page.on("requestfailed", (r) => pending.delete(r));
    page.on("response", (r) => {
      if (
        r.url().includes("/api/v1/") &&
        r.status() >= 400 &&
        !r.url().includes("/auth/me")
      )
        failed.push(`${r.status()} ${new URL(r.url()).pathname}`);
    });
    try {
      await login(page, role === "operator" ? "operator_a" : role);
      let i = 0;
      for (const name of leaves(baseline[role])) {
        await navigateWorkspace(page, name);
        await expect
          .poll(
            async () => {
              await page.evaluate(
                () =>
                  new Promise<void>((resolve) =>
                    requestAnimationFrame(() =>
                      requestAnimationFrame(() => resolve()),
                    ),
                  ),
              );
              return pending.size;
            },
            { timeout: 20000 },
          )
          .toBe(0);
        await expect(page.locator("main")).toBeVisible();
        await page.screenshot({
          path: `../../.backups/page-alignment-followup/${role}-${++i}.png`,
        });
        inventory.push({
          role,
          name,
          url: page.url(),
          headings: await page
            .locator("main h1,main h2")
            .filter({ visible: true })
            .allTextContents(),
          columns: await page.getByRole("columnheader").allTextContents(),
        });
      }
      expect(errors).toEqual([]);
      expect(failed).toEqual([]);
    } finally {
      await context.close();
    }
  }
  expect(inventory).toHaveLength(58);
  fs.writeFileSync(
    "../../.backups/page-alignment-followup/58-pages.json",
    JSON.stringify(inventory, null, 2),
  );
});
