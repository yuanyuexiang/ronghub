import { navigateWorkspace, test, expect, type Page } from "./fixtures";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, user: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(user);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  const mobileNavigation = page.getByRole("button", {
    name: "展开或收起功能导航",
  });
  await expect(
    page.getByRole("heading", {
      name: /^(运营总览|接入汇总|多维度数据筛选)$/,
      level: 1,
    }),
  ).toBeVisible();
  if (await mobileNavigation.isVisible()) await mobileNavigation.click();
  await navigateWorkspace(page, user === "regulator" ? "告警数据" : "企业告警");
}
for (const width of [1440, 768]) {
  test(`alert workspace starts with events and preserves independent drafts at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await login(page, "regulator");
    const navigation = page.getByRole("navigation", { name: "告警工作区" });
    const choose = (name: string) =>
      navigation.getByRole("button", { name, exact: true }).click();
    await expect(
      navigation.getByRole("button", { name: "事件跟踪", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    const title = page.getByRole("heading", {
      name: "告警事件跟踪",
      exact: true,
    });
    await expect(title).toBeVisible();
    expect((await title.boundingBox())!.y).toBeLessThan(700);
    await expect(
      page.getByRole("region", { name: "分级与周期告警配置", exact: true }),
    ).toBeHidden();
    await page
      .getByLabel("告警检索名称", { exact: true })
      .fill("未查询事件草稿");
    await expect(
      page.getByRole("button", { name: "查询告警记录", exact: true }),
    ).toBeEnabled();
    if (width === 1440)
      expect(
        (await page.locator(".alert-queue thead").boundingBox())!.y,
      ).toBeLessThan(1000);
    await page.screenshot({
      path: `../../.backups/alert-workspace-${width}.png`,
      fullPage: true,
    });
    await choose("规则配置");
    await page
      .getByRole("button", { name: "新增分级规则", exact: true })
      .click();
    await page
      .getByLabel("分级规则代码", { exact: true })
      .fill("unsaved-layout-draft");
    await choose("通知设置");
    await page
      .getByRole("button", { name: "管理通知与接收人", exact: true })
      .click();
    await page
      .getByRole("button", { name: "新增告警接收人", exact: true })
      .click();
    await page
      .getByLabel("告警接收人姓名", { exact: true })
      .fill("未保存名单草稿");
    await choose("来源与观测");
    await expect(
      page.getByRole("region", { name: "独立分级告警观测", exact: true }),
    ).toBeVisible();
    await choose("事件跟踪");
    await expect(page.getByLabel("告警检索名称", { exact: true })).toHaveValue(
      "未查询事件草稿",
    );
    await page.getByRole("button", { name: "刷新数据", exact: true }).click();
    await choose("规则配置");
    await expect(page.getByLabel("分级规则代码", { exact: true })).toHaveValue(
      "unsaved-layout-draft",
    );
    await choose("通知设置");
    await expect(
      page.getByLabel("告警接收人姓名", { exact: true }),
    ).toHaveValue("未保存名单草稿");
  });
}
test("operator sees event-first workspace without private notification navigation or maintenance", async ({
  page,
}) => {
  await login(page, "operator_a");
  const navigation = page.getByRole("navigation", { name: "告警工作区" });
  await expect(
    page.getByRole("heading", { name: "企业告警事件", exact: true }),
  ).toBeVisible();
  await expect(
    navigation.getByRole("button", { name: "通知设置", exact: true }),
  ).toHaveCount(0);
  await navigation
    .getByRole("button", { name: "规则配置", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "分级与周期告警配置", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "新增分级规则", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "演示样本验证", exact: true }),
  ).toHaveCount(0);
  await navigation
    .getByRole("button", { name: "来源与观测", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "提交独立演示观测", exact: true }),
  ).toHaveCount(0);
  expect(
    (
      await page.request.get("/api/v1/alerts/notifications/recipients")
    ).status(),
  ).toBe(403);
});
