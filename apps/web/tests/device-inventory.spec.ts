import { test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, username: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
}
async function open(page: Page, name: string) {
  await page.getByRole("button", { name: "场站台账", exact: true }).click();
  await page.getByLabel("搜索场站", { exact: true }).fill(name);
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await page
    .getByRole("button", { name: `查看详情 ${name}`, exact: true })
    .click();
  const panel = page
    .getByRole("dialog")
    .getByLabel("场站设备明细", { exact: true });
  await expect(
    panel.getByText("第1页 · 共23个设备", { exact: true }),
  ).toBeVisible();
  return panel;
}
test("source device profiles and connector pages retain unknowns, independent search drafts and three-role scope", async ({
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
        "--device-inventory",
        "--facilities",
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const contexts = await Promise.all(
    [0, 1, 2, 3].map(() =>
      browser.newContext({ timezoneId: "America/New_York" }),
    ),
  );
  try {
    const [owner, regulator, association, outside] = await Promise.all(
      contexts.map((c) => c.newPage()),
    );
    await login(owner, "operator_a");
    const panel = await open(owner, fixture.name);
    const dialog = owner.getByRole("dialog");
    await expect(dialog.getByLabel("场站基本资料")).toContainText("私人共享桩");
    await expect(dialog.getByLabel("场站基本资料")).toContainText("加油加气站");
    await expect(panel).toContainText("23 个设备、24 个终端");
    const first = panel.getByLabel("设备 device-00", { exact: true });
    await expect(first).toContainText("V2G设备");
    await expect(first).toContainText("DEMO-V2G");
    await expect(first).toContainText("2024-02-29");
    await expect(first).toContainText("直流接口枪头（演示）");
    await panel.getByLabel("检索场站设备", { exact: true }).fill("not-applied");
    await first.getByRole("button", { name: /^终端下一页 / }).click();
    await expect(first).toContainText("terminal-19");
    await first.getByRole("button", { name: /^终端下一页 / }).click();
    await expect(first).toContainText("terminal-22");
    await expect(panel.getByLabel("检索场站设备", { exact: true })).toHaveValue(
      "not-applied",
    );
    const input = first.getByLabel(/^检索终端 /);
    await input.fill("terminal-22");
    await first.getByRole("button", { name: "查询终端", exact: true }).click();
    await expect(first).toContainText("第1页 · 匹配1个终端");
    await expect(first.locator("tbody tr")).toHaveCount(1);
    await dialog.screenshot({
      path: "test-results/device-inventory-desktop.png",
    });
    await owner.setViewportSize({ width: 390, height: 900 });
    await first.scrollIntoViewIfNeeded();
    expect(
      await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
    ).toBe(true);
    await expect(
      dialog.getByRole("button", { name: "关闭详情", exact: true }),
    ).toBeInViewport();
    await dialog.screenshot({
      path: "test-results/device-inventory-narrow.png",
    });
    await panel
      .getByRole("button", { name: "场站设备下一页", exact: true })
      .click();
    await expect(panel).toContainText("第2页 · 共23个设备");
    await panel
      .getByRole("button", { name: "场站设备下一页", exact: true })
      .click();
    await expect(panel).toContainText("第3页 · 共23个设备");
    await expect(
      panel.getByLabel("设备 device-22", { exact: true }),
    ).toBeVisible();
    await panel.getByLabel("检索场站设备", { exact: true }).fill("device-22");
    await panel.getByLabel("筛选设备类型", { exact: true }).selectOption("dc");
    await panel
      .getByLabel("筛选设备终端状态", { exact: true })
      .selectOption("charging");
    await panel.getByRole("button", { name: "查询设备", exact: true }).click();
    await expect(panel).toContainText("第1页 · 共1个设备");
    const last = panel.getByLabel("设备 device-22", { exact: true });
    await last
      .getByRole("button", { name: "展开资料与终端", exact: true })
      .click();
    await expect(last).toContainText("来源未提供");
    await expect(last).toContainText("充电中");
    await panel
      .getByRole("button", { name: "重置设备查询", exact: true })
      .click();
    await expect(panel).toContainText("第1页 · 共23个设备");
    await panel
      .getByLabel("筛选设备类型", { exact: true })
      .selectOption("unknown");
    await panel.getByRole("button", { name: "查询设备", exact: true }).click();
    await expect(panel).toContainText("第1页 · 共21个设备");
    await expect(
      panel.getByLabel("设备 device-01", { exact: true }),
    ).toContainText("类型未提供");
    for (const [page, username] of [
      [regulator, "regulator"],
      [association, "association"],
    ] as const) {
      await login(page, username);
      const shared = await open(page, fixture.name);
      await expect(
        shared.getByLabel("设备 device-00", { exact: true }),
      ).toContainText("2024-02-29");
    }
    await login(outside, "operator_b");
    expect(
      (
        await outside.request.get(
          `/api/v1/stations/${fixture.station_id}/equipment/query`,
        )
      ).status(),
    ).toBe(404);
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
