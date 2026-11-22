import { navigateWorkspace, test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync, copyFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, name: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(name);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
}
test("combined report keeps source identities, all-query totals and real Excel download under applied filters", async ({
  browser,
}) => {
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.browser_fixture",
        "create",
        "--located",
        "--order-history",
        "25",
        "--order-equipment-groups",
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const contexts = await Promise.all([0, 1, 2].map(() => browser.newContext()));
  try {
    const [own, reg, association] = await Promise.all(
      contexts.map((context) => context.newPage()),
    );
    await login(own, "operator_a");
    await navigateWorkspace(own, /^(统计分析|充电量统计)$/);
    await navigateWorkspace(own, "充电与补贴报表");
    await expect(
      own.getByRole("navigation", { name: "统计分析视图" }),
    ).toHaveCount(0);
    const section = own.getByLabel("充电与补贴综合报表", { exact: true });
    await own.getByLabel("综合周期日期", { exact: true }).fill("2026-11-15");
    await own.getByLabel("综合场站名称", { exact: true }).fill(fixture.name);
    await own
      .getByRole("button", { name: "查询综合报表", exact: true })
      .click();
    const summary = own.getByLabel("综合全查询汇总", { exact: true });
    await expect(summary).toContainText("已完成 25 / 观测 25 单");
    await expect(summary).toContainText("296.16 元");
    await expect(summary).toContainText("消费 1");
    await expect(section).toContainText("2026-11-01 至 2026-11-30");
    await own
      .getByRole("group", { name: "综合汇总维度", exact: true })
      .getByRole("button", { name: "电桩汇总", exact: true })
      .click();
    await expect(
      section.getByRole("columnheader", {
        name: "充电时长（小时）",
        exact: true,
      }),
    ).toBeVisible();
    await expect(section).toContainText("综合第1页 · 共26个对象");
    await own.getByRole("button", { name: "综合下一页", exact: true }).click();
    await expect(section).toContainText("综合第2页 · 共26个对象");
    await expect(section.locator("tbody tr")).toHaveCount(6);
    await expect(summary).toContainText("已完成 25 / 观测 25 单");
    await own
      .getByLabel("综合场站名称", { exact: true })
      .fill("unapplied-combined-name");
    await own.getByLabel("综合周期日期", { exact: true }).fill("2024-02-01");
    const pending = own.waitForEvent("download");
    await own
      .getByRole("button", { name: "导出综合报表 Excel", exact: true })
      .click();
    const download = await pending;
    expect(download.suggestedFilename()).toMatch(
      /^运营商电桩补贴汇总-\d{14}\.xlsx$/,
    );
    const path = await download.path();
    expect(path).toBeTruthy();
    expect(readFileSync(path!).subarray(0, 2).toString()).toBe("PK");
    copyFileSync(path!, "test-results/charging-subsidy-sample.xlsx");
    await navigateWorkspace(own, /^(观测电量分析|充电量统计)$/);
    await navigateWorkspace(own, "充电与补贴报表");
    await expect(section).toContainText("综合第2页 · 共26个对象");
    await expect(own.getByLabel("综合场站名称", { exact: true })).toHaveValue(
      "unapplied-combined-name",
    );
    await own.screenshot({
      path: "test-results/charging-subsidy-desktop.png",
      fullPage: true,
    });
    await own.setViewportSize({ width: 768, height: 1024 });
    await own.screenshot({
      path: "test-results/charging-subsidy-narrow.png",
      fullPage: true,
    });
    await login(reg, "regulator");
    await navigateWorkspace(reg, /^(统计分析|充电量统计)$/);
    await navigateWorkspace(reg, "充电与补贴报表");
    await expect(
      reg.getByLabel("充电与补贴综合报表", { exact: true }),
    ).toBeVisible();
    await login(association, "association");
    await navigateWorkspace(association, /^(统计分析|充电量统计)$/);
    await expect(
      association.getByLabel("充电与补贴综合报表", { exact: true }),
    ).toHaveCount(0);
    const forbidden = await association.request.get(
      "/api/v1/statistics/charging-subsidy?period_date=2026-11-15",
    );
    expect(forbidden.status()).toBe(403);
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.browser_fixture",
        "clean",
        "--station",
        String(fixture.station_id),
      ],
      { cwd: "../server" },
    );
  }
});
