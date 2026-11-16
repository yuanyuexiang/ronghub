import { test, expect } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
test("historical runtime keeps independent axes, unknown gaps and clipped complete-query exports", async ({
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
        "--runtime-history",
        "25",
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const context = await browser.newContext({ timezoneId: "America/New_York" });
  try {
    const page = await context.newPage();
    await page.goto("/");
    await page.getByLabel("账号", { exact: true }).fill("operator_a");
    await page.getByLabel("密码", { exact: true }).fill(password);
    await page.getByRole("button", { name: "登录平台" }).click();
    await page.getByRole("button", { name: "运行状态", exact: true }).click();
    await expect(
      page.getByRole("heading", {
        name: "历史运行状态与观测覆盖",
        exact: true,
      }),
    ).toBeVisible();
    const stationPicker = page
      .locator(".station-picker")
      .filter({ has: page.getByLabel("历史运行场站", { exact: true }) });
    await page
      .getByLabel("历史运行场站名称筛选", { exact: true })
      .fill(fixture.name);
    await stationPicker
      .getByRole("button", { name: "筛选", exact: true })
      .click();
    await page
      .getByLabel("历史运行场站", { exact: true })
      .selectOption(String(fixture.station_id));
    await expect(
      page.getByLabel("历史运行数据来源", { exact: true }),
    ).toHaveValue("browser-test");
    await page
      .getByLabel("历史运行源报对象", { exact: true })
      .selectOption(JSON.stringify(["fixture-equipment", "fixture-connector"]));
    await page
      .getByLabel("历史运行开始时间", { exact: true })
      .fill("2026-11-01T08:00");
    await page
      .getByLabel("历史运行结束时间", { exact: true })
      .fill("2026-11-01T09:00");
    const response = page.waitForResponse(
      (r) =>
        r.url().includes("/statistics/runtime?") &&
        r.url().includes("start_at="),
    );
    await page
      .getByRole("button", { name: "查询历史运行", exact: true })
      .click();
    expect(new URL((await response).url()).searchParams.get("start_at")).toBe(
      "2026-11-01T00:00:00.000Z",
    );
    await expect(
      page.getByText("运行记录第1页 · 共26个区间", { exact: true }),
    ).toBeVisible();
    const connection = page.getByLabel("演示连接状态覆盖", { exact: true }),
      operation = page.getByLabel("演示业务状态覆盖", { exact: true });
    await expect(connection).toContainText(
      "已上送1,500秒 · 其中已知1,020秒 / 明确未知480秒",
    );
    await expect(connection).toContainText("未上送查询时长2,100秒");
    await expect(operation).toContainText("已上送60秒");
    await expect(operation).toContainText("未上送查询时长3,540秒");
    await expect(
      operation.locator("li").filter({ hasText: "故障" }),
    ).toContainText("60秒");
    await expect(
      connection.locator("li").filter({ hasText: "在线" }),
    ).toContainText("540秒");
    await expect(page.locator("tbody tr")).toHaveCount(20);
    await page
      .getByRole("button", { name: "运行记录下一页", exact: true })
      .click();
    await expect(
      page.getByText("运行记录第2页 · 共26个区间", { exact: true }),
    ).toBeVisible();
    await expect(page.locator("tbody tr")).toHaveCount(6);
    await page
      .getByLabel("历史运行开始时间", { exact: true })
      .fill("2026-11-10T00:00");
    const downloadPromise = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "导出当前运行区间", exact: true })
      .click();
    const path = await (await downloadPromise).path();
    expect(path).toBeTruthy();
    const csv = readFileSync(path!, "utf8");
    expect(csv.trim().split("\n")).toHaveLength(27);
    expect(csv).toContain("窗口内观测秒数");
    await page
      .getByLabel("历史运行开始时间", { exact: true })
      .fill("2026-11-01T08:00");
    await page
      .getByLabel("历史运行源报对象", { exact: true })
      .selectOption(JSON.stringify(["fixture-equipment", ""]));
    await page
      .getByRole("button", { name: "查询历史运行", exact: true })
      .click();
    await expect(
      page.getByText("运行记录第1页 · 共1个区间", { exact: true }),
    ).toBeVisible();
    await expect(connection).toContainText("已上送3,600秒");
    await expect(operation).toContainText("已上送0秒");
    await expect(operation).toContainText("暂无占比");
    await page
      .getByLabel("历史运行源报对象", { exact: true })
      .selectOption(JSON.stringify(["fixture-equipment", "fixture-connector"]));
    await page
      .getByLabel("历史运行开始时间", { exact: true })
      .fill("2026-11-01T08:00:30");
    await page
      .getByLabel("历史运行结束时间", { exact: true })
      .fill("2026-11-01T08:00:45");
    await page
      .getByRole("button", { name: "查询历史运行", exact: true })
      .click();
    await expect(
      page.getByText("运行记录第1页 · 共2个区间", { exact: true }),
    ).toBeVisible();
    await expect(connection).toContainText("已上送15秒");
    await expect(operation).toContainText("已上送15秒");
    await page.screenshot({
      path: "test-results/historical-runtime-coverage.png",
      fullPage: true,
    });
    await page
      .getByLabel("历史运行开始时间", { exact: true })
      .fill("2026-11-02T00:00");
    await page
      .getByLabel("历史运行结束时间", { exact: true })
      .fill("2026-11-03T00:00");
    await page
      .getByRole("button", { name: "查询历史运行", exact: true })
      .click();
    await expect(
      page.getByText(
        "当前条件下没有历史运行区间；未上送不能视为零故障或全在线。",
        { exact: true },
      ),
    ).toBeVisible();
  } finally {
    await context.close();
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
