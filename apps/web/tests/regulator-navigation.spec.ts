import { test, expect, navigateWorkspace, type Page } from "./fixtures";
import { readFileSync } from "node:fs";
import baseline from "./fixtures/canvas-navigation.json";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, username = "regulator") {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
}
for (const role of ["regulator", "association", "operator"] as const)
  test(`rendered ${role} canvas menu matches actual hierarchy and opens a desktop flyout`, async ({
    page,
  }) => {
    await login(page, role === "operator" ? "operator_a" : role);
    const nav = page.getByRole("navigation", {
      name: "业务模块导航",
      exact: true,
    });
    const actual = await nav.evaluate((el) => {
      const items = (parent: Element): unknown[] =>
        Array.from(parent.querySelectorAll(":scope > .navigation-entry")).map(
          (entry) => ({
            label: entry.querySelector(":scope > button > span")!.textContent,
            children: entry.querySelector(":scope > .navigation-flyout")
              ? items(entry.querySelector(":scope > .navigation-flyout")!)
              : [],
          }),
        );
      return Array.from(el.querySelectorAll(":scope > section")).map(
        (group) => ({
          label: group.querySelector(":scope > button > span")!.textContent,
          children: group.querySelector(":scope > .navigation-flyout")
            ? items(group.querySelector(":scope > .navigation-flyout")!)
            : [],
        }),
      );
    });
    expect(actual).toEqual(baseline[role]);
    await expect(
      nav.locator('.navigation-group-toggle[aria-expanded="true"]'),
    ).toHaveCount(0);
    await page.screenshot({
      path: `../../.backups/canvas-corrected-${role}-collapsed.png`,
    });
    const group = nav.locator(".navigation-group").filter({
      has: page.getByRole("button", {
        name:
          role === "operator"
            ? "待办事项模块，展开或收起"
            : "全域监管模块，展开或收起",
        exact: true,
      }),
    });
    const trigger = group.locator(":scope > button");
    await trigger.click();
    const panel = group.locator(":scope > .navigation-flyout");
    await expect(panel).toBeVisible();
    const side = await page
      .getByRole("complementary", { name: "功能导航" })
      .boundingBox();
    const bounds = await panel.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(side!.x + side!.width - 1);
    await page.screenshot({
      path: `../../.backups/canvas-corrected-${role}-flyout.png`,
    });
    await page.keyboard.press("Escape");
    await expect(panel).toBeHidden();
    await expect(trigger).toBeFocused();
    await page.setViewportSize({ width: 390, height: 900 });
    await navigateWorkspace(
      page,
      role === "regulator"
        ? "企业申报查看"
        : role === "association"
          ? "企业申报管理"
          : "评定进度查询",
    );
    await expect(nav).toBeHidden();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `../../.backups/canvas-corrected-${role}-390.png`,
    });
  });
test("page actions stay inside their business workspace and auxiliary tools stay out of the sidebar", async ({
  page,
}) => {
  await login(page);
  const nav = page.getByRole("navigation", { name: "业务模块导航" });
  for (const name of [
    "运营商审核",
    "巡检计划创建",
    "设备离线告警",
    "通知配置",
    "场站统计",
    "人员账号申请",
    "账号安全",
  ])
    await expect(
      nav.getByRole("button", { name, exact: true, includeHidden: true }),
    ).toHaveCount(0);
  await navigateWorkspace(page, "巡检任务管理");
  await expect(
    page.getByRole("button", { name: "巡检计划", exact: true }),
  ).toBeVisible();
  await navigateWorkspace(page, "告警数据");
  await page
    .getByRole("navigation", { name: "告警数据分类" })
    .getByRole("button", { name: "设备离线告警", exact: true })
    .click();
  await expect(page.getByLabel("告警筛选类型", { exact: true })).toHaveValue(
    "device_offline",
  );
  await navigateWorkspace(page, "角色管理");
  await expect(page).toHaveURL(/view=roles/);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "角色管理", level: 1 }),
  ).toBeVisible();
  await navigateWorkspace(page, "账号安全");
  await expect(
    page.getByRole("heading", { name: "账号手机验证", exact: true }),
  ).toBeVisible();
});
test("association collaboration distinguishes shared published results from an unavailable external sync", async ({
  page,
}) => {
  await login(page, "association");
  await navigateWorkspace(page, "监管协同应用");
  await expect(
    page.getByRole("heading", { name: "监管协同应用", level: 2 }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "全部同步", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByText("外部监管系统尚未接通", { exact: false }),
  ).toBeVisible();
});

test("subsidy statistics keeps only the real third-level menu and station drill-down stays in the page", async ({
  page,
}) => {
  await login(page);
  const nav = page.getByRole("navigation", { name: "业务模块导航" });
  await nav
    .getByRole("button", { name: "补贴管理模块，展开或收起", exact: true })
    .click();
  const branch = nav.getByRole("button", { name: "补贴统计", exact: true });
  await branch.click();
  await expect(branch).toHaveAttribute("aria-expanded", "true");
  const panel = branch
    .locator("xpath=..")
    .locator(":scope > .navigation-flyout");
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("button")).toHaveText([
    "运营商统计",
    "补贴明细",
  ]);
  const parent = branch
    .locator('xpath=ancestor::div[contains(@class,"navigation-flyout")]')
    .first();
  const a = await parent.boundingBox(),
    b = await panel.boundingBox();
  expect(b!.x).toBeGreaterThanOrEqual(a!.x + a!.width - 1);
  await page.screenshot({
    path: "../../.backups/canvas-corrected-regulator-third-level.png",
  });
  await panel.getByRole("button", { name: "运营商统计", exact: true }).click();
  await expect(
    page.getByRole("navigation", { name: "补贴业务视图" }),
  ).toHaveCount(0);
  await expect(page.getByLabel("补贴统计维度", { exact: true })).toHaveValue(
    "operator",
  );
  await page
    .getByLabel("补贴统计维度", { exact: true })
    .selectOption("station");
  const response = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/api/v1/subsidy/statistics" &&
      new URL(r.url()).searchParams.get("dimension") === "station",
  );
  await page.getByRole("button", { name: "查询补贴统计", exact: true }).click();
  expect((await response).status()).toBe(200);
  await expect(page).toHaveURL(/view=statistics.operator/);
  await expect(
    page.getByRole("heading", { name: "运营商统计", level: 1, exact: true }),
  ).toBeVisible();
  await expect(nav.locator(".active-group > button")).toContainText("补贴管理");
});
