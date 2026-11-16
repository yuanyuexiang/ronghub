import { test, expect } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
test("regional and enterprise quality keeps empty stations, observed cohorts and independent fault samples", async ({
  browser,
}) => {
  const prefix = "quality-" + randomUUID().slice(0, 8),
    fixtures: { station_id: number; name: string }[] = [];
  const context = await browser.newContext({ timezoneId: "America/New_York" });
  try {
    const batch = JSON.parse(
      execFileSync(
        "uv",
        [
          "run",
          "python",
          "-m",
          "app.browser_fixture",
          "create",
          "--count",
          "25",
          "--group-name",
          prefix,
          "--runtime-history",
          "25",
          "--fault-history",
          "25",
        ],
        { cwd: "../server", encoding: "utf8" },
      ),
    );
    fixtures.push(...batch.items);
    fixtures.push(
      JSON.parse(
        execFileSync(
          "uv",
          [
            "run",
            "python",
            "-m",
            "app.browser_fixture",
            "create",
            "--group-name",
            prefix,
          ],
          { cwd: "../server", encoding: "utf8" },
        ),
      ),
    );
    const page = await context.newPage();
    await page.goto("/");
    await page.getByLabel("账号", { exact: true }).fill("operator_a");
    await page.getByLabel("密码", { exact: true }).fill(password);
    await page.getByRole("button", { name: "登录平台" }).click();
    await page.getByRole("button", { name: "运行状态", exact: true }).click();
    await page
      .getByRole("button", { name: "区域与企业质量", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "区域与企业质量观测", exact: true }),
    ).toBeVisible();
    await page.getByLabel("检索质量来源", { exact: true }).fill("browser-test");
    await page.getByRole("button", { name: "筛选来源", exact: true }).click();
    await page
      .getByLabel("质量数据来源", { exact: true })
      .selectOption("browser-test");
    await page.getByLabel("质量站企名称筛选", { exact: true }).fill(prefix);
    await page
      .getByLabel("质量开始时间", { exact: true })
      .fill("2026-11-01T08:00");
    await page
      .getByLabel("质量结束时间", { exact: true })
      .fill("2026-11-01T09:00");
    const response = page.waitForResponse(
      (r) =>
        r.url().includes("/statistics/quality?") &&
        r.url().includes("start_at="),
    );
    await page
      .getByRole("button", { name: "查询质量观测", exact: true })
      .click();
    expect(new URL((await response).url()).searchParams.get("start_at")).toBe(
      "2026-11-01T00:00:00.000Z",
    );
    await expect(page.locator(".quality-panel .runtime-applied")).toContainText(
      "台账26站",
    );
    await expect(page.locator(".quality-panel .runtime-applied")).toContainText(
      "1个分组",
    );
    await expect(page.locator(".quality-panel tbody tr")).toHaveCount(1);
    await expect(page.locator(".quality-panel tbody")).toContainText("25个");
    await expect(page.locator(".quality-panel tbody")).toContainText(
      "已观测625件",
    );
    await expect(page.locator(".quality-panel tbody")).toContainText("30秒");
    await page
      .getByRole("button", { name: "查看鼓楼区演示状态组成", exact: true })
      .click();
    const connection = page.getByLabel("演示连接状态覆盖", { exact: true }),
      operation = page.getByLabel("演示业务状态覆盖", { exact: true });
    await expect(connection).toContainText(
      "已上送37,500秒 · 其中已知25,500秒 / 明确未知12,000秒",
    );
    await expect(connection).toContainText("未上送查询时长52,500秒");
    await expect(operation).toContainText("已上送1,500秒");
    await expect(operation).toContainText("未上送查询时长88,500秒");
    await page.screenshot({
      path: "test-results/quality-observations.png",
      fullPage: true,
    });
    await page
      .getByLabel("质量运行区间层级", { exact: true })
      .selectOption("equipment");
    await page
      .getByRole("button", { name: "查询质量观测", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "查看鼓楼区演示状态组成", exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "查看鼓楼区演示状态组成", exact: true })
      .click();
    await expect(connection).toContainText("已上送90,000秒");
    await expect(operation).toContainText("已上送0秒");
    await expect(page.locator(".quality-panel tbody")).toContainText(
      "已观测625件",
    );
    await page
      .getByLabel("质量分组方式", { exact: true })
      .selectOption("station");
    await page
      .getByRole("button", { name: "查询质量观测", exact: true })
      .click();
    await expect(
      page.getByText("质量第1页 · 共26组", { exact: true }),
    ).toBeVisible();
    await expect(page.locator(".quality-panel tbody tr")).toHaveCount(20);
    await page.getByRole("button", { name: "质量下一页", exact: true }).click();
    await expect(
      page.getByText("质量第2页 · 共26组", { exact: true }),
    ).toBeVisible();
    await expect(page.locator(".quality-panel tbody tr")).toHaveCount(6);
    await page
      .getByLabel("质量站企名称筛选", { exact: true })
      .fill("unapplied-empty");
    const downloadPromise = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "导出当前质量查询", exact: true })
      .click();
    const path = await (await downloadPromise).path();
    expect(path).toBeTruthy();
    const csv = readFileSync(path!, "utf8");
    expect(csv.trim().split("\n")).toHaveLength(27);
    expect(csv).toContain("非设备总数");
    expect(csv).toContain("非维修工时");
    await page.getByLabel("质量站企名称筛选", { exact: true }).fill(prefix);
    await page
      .getByLabel("质量分组方式", { exact: true })
      .selectOption("operator");
    await page
      .getByRole("button", { name: "查询质量观测", exact: true })
      .click();
    await expect(
      page.getByText("质量第1页 · 共1组", { exact: true }),
    ).toBeVisible();
    await expect(page.locator(".quality-panel .runtime-applied")).toContainText(
      "按当前运营企业",
    );
    await page
      .getByLabel("质量站企名称筛选", { exact: true })
      .fill("empty-no-quality-cohort");
    await page
      .getByRole("button", { name: "查询质量观测", exact: true })
      .click();
    await expect(
      page.getByText("当前条件下没有质量观测分组", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "单对象区间", exact: true }).click();
    await expect(
      page.getByRole("heading", {
        name: "历史运行状态与观测覆盖",
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
        { cwd: "../server" },
      );
  }
});
