import { navigateWorkspace, test, expect, Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, username: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台" }).click();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
}
test("multi-station slots keep the authorized cohort and exact independent meter totals", async ({
  browser,
}) => {
  const group = "multi-slots-" + randomUUID().slice(0, 8);
  const fixtures = JSON.parse(
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.browser_fixture",
        "create",
        "--count",
        "3",
        "--group-name",
        group,
        "--order-history",
        "25",
        "--hourly-energy",
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  ).items;
  const context = await browser.newContext({ timezoneId: "America/New_York" });
  try {
    const page = await context.newPage();
    await login(page, "operator_a");
    await navigateWorkspace(page, /^(充电时段|充电时段统计)$/);
    await page.getByRole("button", { name: "多站汇总", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "授权范围多站时段汇总", exact: true }),
    ).toBeVisible();
    await page.getByLabel("检索多站来源", { exact: true }).fill("browser-test");
    await page.getByRole("button", { name: "筛选来源", exact: true }).click();
    await page
      .getByLabel("多站数据来源", { exact: true })
      .selectOption("browser-test");
    await page.getByLabel("多站检索名称", { exact: true }).fill(group);
    await page
      .getByLabel("多站开始时间", { exact: true })
      .fill("2026-11-01T00:00");
    await page
      .getByLabel("多站结束时间", { exact: true })
      .fill("2026-11-02T00:00");
    await expect(
      page.getByRole("button", { name: "查询多站时段", exact: true }),
    ).toBeEnabled();
    const response = page.waitForResponse(
      (r) =>
        r.url().includes("/time-slots/aggregate?") &&
        r.request().method() === "GET",
    );
    await page
      .getByRole("button", { name: "查询多站时段", exact: true })
      .click();
    const done = await response;
    expect(done.status()).toBe(200);
    expect(new URL(done.url()).searchParams.get("start_at")).toBe(
      "2026-10-31T16:00:00.000Z",
    );
    await expect(
      page.getByText("当前筛选集合：3个场站", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("有订单观测3站 · 有站级计量记录3站", { exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("row")).toHaveCount(25);
    const row = (label: string) =>
      page
        .getByRole("row")
        .filter({ has: page.getByRole("cell", { name: label, exact: true }) });
    await expect(
      row("08:00–09:00").getByRole("cell", { name: "0.900000", exact: true }),
    ).toBeVisible();
    await expect(row("08:00–09:00")).toContainText("已完成75");
    await expect(
      row("09:00–10:00").getByRole("cell", {
        name: "暂无计量数据",
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      row("10:00–11:00").getByRole("cell", { name: "0.000000", exact: true }),
    ).toBeVisible();
    await row("08:00–09:00").scrollIntoViewIfNeeded();
    await page.screenshot({
      path: "test-results/multi-station-time-slots.png",
      fullPage: true,
    });
    await page
      .getByLabel("多站检索名称", { exact: true })
      .fill("unapplied-query");
    const downloading = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "导出当前多站查询", exact: true })
      .click();
    const download = await downloading;
    expect(download.suggestedFilename()).toBe(
      "ronghub-multi-station-time-slots.csv",
    );
    const content = readFileSync((await download.path())!, "utf8");
    const rows = content
      .replace(/^\uFEFF/, "")
      .trimEnd()
      .split(/\r?\n/);
    expect(rows.length).toBe(25);
    expect(content).toContain("0.900000");
    expect(content).toContain(group);
    await page
      .getByLabel("多站检索名称", { exact: true })
      .fill(fixtures[0].name);
    await page
      .getByRole("button", { name: "查询多站时段", exact: true })
      .click();
    await expect(
      page.getByText("当前筛选集合：1个场站", { exact: true }),
    ).toBeVisible();
    await expect(
      row("08:00–09:00").getByRole("cell", { name: "0.300000", exact: true }),
    ).toBeVisible();
    await page
      .getByLabel("多站检索名称", { exact: true })
      .fill("empty-applied-query");
    await page
      .getByRole("button", { name: "查询多站时段", exact: true })
      .click();
    await expect(
      page.getByText("当前筛选集合：0个场站", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("当前筛选集合没有多站时段观测", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "单站查询", exact: true }).click();
    await expect(
      page.getByRole("heading", {
        name: "订单开始分布与源报小时电量",
        exact: true,
      }),
    ).toBeVisible();
  } finally {
    await context.close();
    for (const fixture of fixtures)
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
        { cwd: "../server", stdio: "pipe" },
      );
  }
});
