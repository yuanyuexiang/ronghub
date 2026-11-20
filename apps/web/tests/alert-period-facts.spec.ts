import { test, expect } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { pickStation } from "./station";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
function fixture(module: string, args: string[]) {
  return execFileSync("uv", ["run", "python", "-m", module, ...args], {
    cwd: "../server",
    encoding: "utf8",
  });
}
test("independent periods expose historical coverage and preserve unknown values", async ({
  browser,
}) => {
  const station = JSON.parse(
    fixture("app.browser_fixture", ["create", "--operating"]),
  );
  const context = await browser.newContext();
  try {
    fixture("app.alert_period_fixture", [
      "--station",
      String(station.station_id),
    ]);
    const page = await context.newPage();
    await page.goto("/");
    await page.getByLabel("账号", { exact: true }).fill("regulator");
    await page.getByLabel("密码", { exact: true }).fill(password);
    await page.getByRole("button", { name: "登录平台", exact: true }).click();
    await page.getByRole("button", { name: "告警数据", exact: true }).click();
    await page
      .getByRole("navigation", { name: "告警工作区" })
      .getByRole("button", { name: "来源与观测", exact: true })
      .click();
    const panel = page.getByRole("region", {
      name: "独立周期来源",
      exact: true,
    });
    await panel
      .getByRole("button", { name: "查看周期来源", exact: true })
      .click();
    await pickStation(page, "周期来源场站", station);
    const response = page.waitForResponse(
      (r) =>
        r.url().includes("/alerts/period-facts?") &&
        r.url().includes(`station_id=${station.station_id}`),
    );
    await panel
      .getByRole("button", { name: "查询周期来源", exact: true })
      .click();
    const received = await response;
    expect(received.status()).toBe(200);
    expect(received.headers()["cache-control"]).toBe("no-store");
    await expect(
      panel.getByRole("row").filter({ hasText: "完整覆盖" }),
    ).toHaveCount(1);
    const unknown = panel.getByRole("row").filter({ hasText: "存在未知状态" });
    expect(
      (await received.json()).items.find(
        (r: { quality_reason: string | null }) => r.quality_reason,
      )?.percent,
    ).toBeNull();
    await unknown
      .getByRole("button", { name: "查看依据", exact: true })
      .click();
    const proof = panel.getByRole("region", {
      name: "周期来源依据",
      exact: true,
    });
    await expect(proof).toContainText("未知 / 未知");
    await expect(proof).toContainText("演示历史设备");
    await panel.screenshot({ path: "../../.backups/alert-period-browser.png" });
  } finally {
    await context.close();
    fixture("app.browser_fixture", [
      "clean",
      "--station",
      String(station.station_id),
    ]);
  }
});
