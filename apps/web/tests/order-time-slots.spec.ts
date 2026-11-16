import { test, expect, Page } from "./fixtures";
import { pickStation } from "./station";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
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
test("Beijing time slots keep meter energy separate from order totals and distinguish unknown from zero", async ({
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
        "--order-history",
        "25",
        "--hourly-energy",
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const contexts = await Promise.all([
    browser.newContext({ timezoneId: "America/New_York" }),
    browser.newContext(),
  ]);
  try {
    const [operator, association] = await Promise.all(
      contexts.map((c) => c.newPage()),
    );
    await login(operator, "operator_a");
    await login(association, "association");
    await expect(
      association.getByRole("button", { name: "充电时段", exact: true }),
    ).toHaveCount(0);
    expect(
      (await association.request.get("/api/v1/orders/time-slots")).status(),
    ).toBe(403);
    await operator
      .getByRole("button", { name: "充电时段", exact: true })
      .click();
    await pickStation(operator, "时段分析场站", fixture);
    await expect(
      operator.getByLabel("时段数据来源", { exact: true }),
    ).toHaveValue("browser-test");
    await operator
      .getByLabel("时段开始时间", { exact: true })
      .fill("2026-11-01T00:00");
    await operator
      .getByLabel("时段结束时间", { exact: true })
      .fill("2026-11-02T00:00");
    await expect(
      operator.getByRole("button", { name: "查询充电时段", exact: true }),
    ).toBeEnabled();
    const response = operator.waitForResponse(
      (r) =>
        r.url().includes("/orders/time-slots?") &&
        r.request().method() === "GET",
    );
    await operator
      .getByRole("button", { name: "查询充电时段", exact: true })
      .click();
    const done = await response;
    expect(done.status()).toBe(200);
    expect(new URL(done.url()).searchParams.get("start_at")).toBe(
      "2026-10-31T16:00:00.000Z",
    );
    await expect(
      operator.getByRole("img", {
        name: "北京时间24小时已观测订单开始数，演示",
        exact: true,
      }),
    ).toBeVisible();
    await expect(operator.getByRole("row")).toHaveCount(25);
    const row = (label: string) =>
      operator.getByRole("row").filter({
        has: operator.getByRole("cell", { name: label, exact: true }),
      });
    await expect(row("08:00–09:00")).toContainText("100.00%");
    await expect(
      row("08:00–09:00").getByRole("cell", { name: "0.300000", exact: true }),
    ).toBeVisible();
    await expect(
      row("09:00–10:00").getByRole("cell", {
        name: "暂无计量数据",
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      row("10:00–11:00").getByRole("cell", { name: "0.000000", exact: true }),
    ).toBeVisible();
    await expect(
      row("11:00–12:00").getByRole("cell", {
        name: "暂无计量数据",
        exact: true,
      }),
    ).toBeVisible();
    await row("08:00–09:00").scrollIntoViewIfNeeded();
    await operator.screenshot({
      path: "test-results/order-time-slots.png",
      fullPage: true,
    });
    await operator
      .getByLabel("时段数据来源", { exact: true })
      .fill("unapplied-source");
    const downloading = operator.waitForEvent("download");
    await operator
      .getByRole("button", { name: "导出当前时段查询", exact: true })
      .click();
    const download = await downloading;
    expect(download.suggestedFilename()).toBe("ronghub-order-time-slots.csv");
    const content = readFileSync((await download.path())!, "utf8");
    expect(
      content
        .replace(/^\uFEFF/, "")
        .trimEnd()
        .split(/\r?\n/).length,
    ).toBe(25);
    expect(content).toContain("0.300000");
    expect(content).toContain("0.000000");
    expect(content).toContain("暂无数据");
    await operator
      .getByLabel("时段数据来源", { exact: true })
      .fill("browser-test");
    await operator
      .getByLabel("时段计量层级", { exact: true })
      .selectOption("equipment");
    await operator
      .getByLabel("时段源桩编码", { exact: true })
      .fill("browser-equipment");
    await operator
      .getByRole("button", { name: "查询充电时段", exact: true })
      .click();
    await expect(
      row("08:00–09:00").getByRole("cell", {
        name: "暂无计量数据",
        exact: true,
      }),
    ).toBeVisible();
    await operator
      .getByLabel("时段数据来源", { exact: true })
      .fill("absent-source");
    await operator
      .getByRole("button", { name: "查询充电时段", exact: true })
      .click();
    await expect(
      operator.getByText("当前条件下没有时段观测记录", { exact: true }),
    ).toBeVisible();
  } finally {
    await Promise.allSettled(contexts.map((c) => c.close()));
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
