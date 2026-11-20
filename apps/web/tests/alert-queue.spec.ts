import { test, expect } from "./fixtures";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
test("alert queue distinguishes confirmation from recovery and reaches historical pages", async ({
  page,
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
        "--alert-history",
        "25",
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  try {
    await page.goto("/");
    await page.getByLabel("账号", { exact: true }).fill("regulator");
    await page.getByLabel("密码", { exact: true }).fill(password);
    await page.getByRole("button", { name: "登录平台" }).click();
    await page.getByRole("button", { name: "告警数据", exact: true }).click();
    await page.getByLabel("告警检索名称").fill(fixture.name);
    await page.getByLabel("告警筛选状态").selectOption("active");
    await page.getByLabel("告警筛选确认").selectOption("false");
    await expect(
      page.getByRole("button", { name: "查询告警记录", exact: true }),
    ).toBeEnabled();
    await page
      .getByRole("button", { name: "查询告警记录", exact: true })
      .click();
    await expect(
      page.getByText("告警第1页 · 共25条", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "告警下一页", exact: true }).click();
    await expect(
      page.getByText("告警第2页 · 共25条", { exact: true }),
    ).toBeVisible();
    const id = fixture.event_ids[0];
    await page
      .getByRole("row")
      .filter({
        has: page.getByRole("cell", { name: `ALERT-${id}`, exact: true }),
      })
      .getByRole("button", { name: "告警详情", exact: true })
      .click();
    await page
      .getByLabel("告警确认依据")
      .fill("已查看演示异常，设备状态仍待运维处置");
    await page
      .getByRole("button", { name: "确认已查看告警", exact: true })
      .click();
    await page
      .getByRole("button", { name: "关闭告警详情", exact: true })
      .click();
    await expect(
      page.getByText("告警第2页 · 共24条", { exact: true }),
    ).toBeVisible();
    await page.getByLabel("告警检索编号").fill(String(id));
    await page.getByLabel("告警筛选确认").selectOption("true");
    await page
      .getByRole("button", { name: "查询告警记录", exact: true })
      .click();
    await expect(
      page.getByText("告警第1页 · 共1条", { exact: true }),
    ).toBeVisible();
    const row = page.getByRole("row").filter({
      has: page.getByRole("cell", { name: `ALERT-${id}`, exact: true }),
    });
    await expect(row).toContainText("持续异常");
    await expect(row).toContainText("已人工确认");
    await page.getByLabel("告警筛选状态").selectOption("recovered");
    await page
      .getByRole("button", { name: "查询告警记录", exact: true })
      .click();
    await expect(
      page.getByText("当前条件下没有告警记录", { exact: true }),
    ).toBeVisible();
    await page.getByLabel("告警筛选状态").selectOption("active");
    await page
      .getByRole("button", { name: "查询告警记录", exact: true })
      .click();
    await expect(
      page.getByText("告警第1页 · 共1条", { exact: true }),
    ).toBeVisible();
    await page
      .getByLabel("告警筛选类型", { exact: true })
      .selectOption("device_offline");
    const now = new Date();
    const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-01T00:00`;
    const next = new Date(now.getFullYear(), now.getMonth() + 1, 1);
    const end = `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, "0")}-01T00:00`;
    await page.getByLabel("告警触发开始时间", { exact: true }).fill(month);
    await page.getByLabel("告警触发结束时间", { exact: true }).fill(end);
    await page
      .getByRole("button", { name: "查询告警记录", exact: true })
      .click();
    await expect(
      page.getByText("告警第1页 · 共1条", { exact: true }),
    ).toBeVisible();
    // Editing draft conditions must not silently change the exported queried result.
    await page.getByLabel("告警筛选状态").selectOption("recovered");
    const downloadPromise = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "导出告警CSV", exact: true })
      .click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe("ronghub-alert-events.csv");
    const csv = readFileSync((await download.path())!, "utf8");
    expect(csv).toContain("事件编号");
    expect(csv).toContain(String(id));
    expect(csv.trim().split("\n")).toHaveLength(2);
    expect(csv).not.toContain("短信通知号码");
    await page.screenshot({
      path: "test-results/alert-queue.png",
      fullPage: true,
    });
  } finally {
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
