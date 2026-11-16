import { test, expect, Page } from "./fixtures";
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
test("operator pages source orders, preserves unknown fields and exports the full applied query", async ({
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
      association.getByRole("button", { name: "充电订单", exact: true }),
    ).toHaveCount(0);
    expect((await association.request.get("/api/v1/orders")).status()).toBe(
      403,
    );
    await operator
      .getByRole("button", { name: "充电订单", exact: true })
      .click();
    await operator
      .getByLabel("订单检索名称", { exact: true })
      .fill(fixture.name);
    await expect(
      operator.getByRole("button", { name: "查询订单", exact: true }),
    ).toBeEnabled();
    await operator
      .getByRole("button", { name: "查询订单", exact: true })
      .click();
    await expect(
      operator.getByText("订单第1页 · 共25条", { exact: true }),
    ).toBeVisible();
    await operator
      .getByRole("button", { name: "订单下一页", exact: true })
      .click();
    await expect(
      operator.getByText("订单第2页 · 共25条", { exact: true }),
    ).toBeVisible();
    await expect(operator.getByRole("row")).toHaveCount(6);
    const unknown = operator
      .getByRole("row")
      .filter({
        has: operator
          .getByRole("cell")
          .filter({
            hasText: `browser-order-${fixture.name.replace("浏览器验收-", "")}-0`,
          }),
      });
    await expect(
      unknown.getByRole("cell", { name: "暂无数据", exact: true }),
    ).toHaveCount(3);
    await operator.screenshot({
      path: "test-results/orders-history.png",
      fullPage: true,
    });
    // Unapplied text must not change an export of the active query.
    await operator
      .getByLabel("订单检索名称", { exact: true })
      .fill("unapplied-empty-query");
    const downloaded = operator.waitForEvent("download");
    await operator
      .getByRole("button", { name: "导出当前订单查询", exact: true })
      .click();
    const file = await downloaded;
    expect(file.suggestedFilename()).toBe("ronghub-orders.csv");
    const content = readFileSync((await file.path())!, "utf8");
    const rows = content
      .replace(/^\uFEFF/, "")
      .trimEnd()
      .split(/\r?\n/);
    expect(rows.length).toBe(26);
    expect(rows.slice(1).every((row) => row.includes(fixture.name))).toBe(true);
    expect(content).toContain("暂无数据");
    await operator
      .getByLabel("订单检索名称", { exact: true })
      .fill(fixture.name);
    await operator
      .getByLabel("订单筛选状态", { exact: true })
      .selectOption("charging");
    await operator
      .getByRole("button", { name: "查询订单", exact: true })
      .click();
    await expect(
      operator.getByText("当前条件下没有订单记录", { exact: true }),
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
