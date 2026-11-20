import { navigateWorkspace, test, expect, Page } from "./fixtures";
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
test("order summary shows partial metric coverage and pages equipment groups without truncating exports", async ({
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
        "--order-equipment-groups",
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
  ]);
  try {
    const [operator, association] = await Promise.all(
      contexts.map((c) => c.newPage()),
    );
    await login(operator, "operator_a");
    await login(association, "association");
    await expect(
      association.getByRole("button", { name: "订单汇总", exact: true }),
    ).toHaveCount(0);
    expect(
      (await association.request.get("/api/v1/orders/summary")).status(),
    ).toBe(403);
    await navigateWorkspace(operator, "订单汇总");
    await operator
      .getByLabel("汇总检索名称", { exact: true })
      .fill(fixture.name);
    await operator
      .getByLabel("汇总开始时间", { exact: true })
      .fill("2026-11-01T00:00");
    await operator
      .getByLabel("汇总结束时间", { exact: true })
      .fill("2026-12-01T00:00");
    await expect(
      operator.getByRole("button", { name: "查询订单汇总", exact: true }),
    ).toBeEnabled();
    await operator
      .getByRole("button", { name: "查询订单汇总", exact: true })
      .click();
    await expect(
      operator.getByText("汇总第1页 · 共1组", { exact: true }),
    ).toBeVisible();
    const station = operator.getByRole("row").filter({
      has: operator.getByRole("cell").filter({ hasText: fixture.name }),
    });
    await expect(station).toContainText("252.000000");
    await expect(station).toContainText("79200");
    await expect(station).toContainText("296.16");
    await expect(
      operator.getByText("已知24 · 缺1", { exact: true }),
    ).toHaveCount(3);
    await operator.screenshot({
      path: "test-results/order-summary-station.png",
      fullPage: true,
    });
    await operator
      .getByLabel("订单汇总维度", { exact: true })
      .selectOption("equipment");
    await operator
      .getByLabel("订单汇总周期", { exact: true })
      .selectOption("day");
    await operator
      .getByRole("button", { name: "查询订单汇总", exact: true })
      .click();
    await expect(
      operator.getByText("汇总第1页 · 共25组", { exact: true }),
    ).toBeVisible();
    const missing = operator.getByRole("row").filter({
      has: operator.getByText("browser-equipment-000", { exact: true }),
    });
    await expect(missing.getByText("暂无数据", { exact: true })).toHaveCount(3);
    await operator
      .getByRole("button", { name: "汇总下一页", exact: true })
      .click();
    await expect(
      operator.getByText("汇总第2页 · 共25组", { exact: true }),
    ).toBeVisible();
    await expect(operator.getByRole("row")).toHaveCount(6);
    await operator.screenshot({
      path: "test-results/order-summary-equipment.png",
      fullPage: true,
    });
    await operator
      .getByLabel("汇总检索名称", { exact: true })
      .fill("unapplied-empty");
    const downloading = operator.waitForEvent("download");
    await operator
      .getByRole("button", { name: "导出当前订单汇总", exact: true })
      .click();
    const download = await downloading;
    expect(download.suggestedFilename()).toBe("ronghub-order-summary.csv");
    const content = readFileSync((await download.path())!, "utf8");
    expect(
      content
        .replace(/^\uFEFF/, "")
        .trimEnd()
        .split(/\r?\n/).length,
    ).toBe(26);
    expect(content).toContain("browser-equipment-000");
    expect(content).toContain("缺电量完成订单数");
    expect(content).toContain("周期粒度");
    await operator
      .getByLabel("汇总检索名称", { exact: true })
      .fill(fixture.name);
    await operator
      .getByLabel("汇总订单状态", { exact: true })
      .selectOption("charging");
    await operator
      .getByRole("button", { name: "查询订单汇总", exact: true })
      .click();
    await expect(
      operator.getByText("当前条件下没有订单汇总记录", { exact: true }),
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
