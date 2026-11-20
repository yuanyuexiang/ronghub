import { navigateWorkspace, test, expect } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
test("source fault histories keep lifecycle, recovered sample averages and complete applied-query exports", async ({
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
        "--fault-history",
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
    await navigateWorkspace(page, "故障分析");
    await page.getByLabel("故障检索名称", { exact: true }).fill(fixture.name);
    await page
      .getByLabel("故障开始时间起", { exact: true })
      .fill("2026-11-01T08:00");
    await page
      .getByLabel("故障开始时间止", { exact: true })
      .fill("2026-11-02T08:00");
    const response = page.waitForResponse(
      (r) =>
        r.url().includes("/statistics/faults?") &&
        r.url().includes("start_at="),
    );
    await page.getByRole("button", { name: "查询故障", exact: true }).click();
    expect(new URL((await response).url()).searchParams.get("start_at")).toBe(
      "2026-11-01T00:00:00.000Z",
    );
    await expect(
      page.getByText("故障第1页 · 共25条", { exact: true }),
    ).toBeVisible();
    const summary = page.getByLabel("故障源内观测汇总");
    await expect(summary).toContainText("观测25件 · 未恢复8 · 已恢复9 · 未知8");
    await expect(summary).toContainText("已恢复样本平均经过时长：30");
    await expect(page.locator("tbody tr")).toHaveCount(20);
    await page.getByRole("button", { name: "故障下一页", exact: true }).click();
    await expect(
      page.getByText("故障第2页 · 共25条", { exact: true }),
    ).toBeVisible();
    await expect(page.locator("tbody tr")).toHaveCount(5);
    await page
      .getByLabel("故障检索名称", { exact: true })
      .fill("unapplied-empty");
    const downloadPromise = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "导出当前故障查询", exact: true })
      .click();
    const download = await downloadPromise;
    const path = await download.path();
    expect(path).toBeTruthy();
    const csv = readFileSync(path!, "utf8");
    expect(csv.trim().split("\n")).toHaveLength(26);
    expect(csv).toContain("非维修工时");
    await page.getByLabel("故障检索名称", { exact: true }).fill(fixture.name);
    await page
      .getByLabel("故障筛选状态", { exact: true })
      .selectOption("active");
    await page.getByRole("button", { name: "查询故障", exact: true }).click();
    await expect(
      page.getByText("故障第1页 · 共8条", { exact: true }),
    ).toBeVisible();
    await expect(summary).toContainText("暂无样本");
    await expect(page.locator("tbody")).toContainText("暂无恢复时间");
    await page.screenshot({
      path: "test-results/source-fault-analysis.png",
      fullPage: true,
    });
    await page
      .getByLabel("故障检索名称", { exact: true })
      .fill("empty-no-source-event");
    await page.getByRole("button", { name: "查询故障", exact: true }).click();
    await expect(
      page.getByText("当前条件下没有故障记录", { exact: true }),
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
