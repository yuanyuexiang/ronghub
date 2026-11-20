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
  const response = page.waitForResponse(
    (r) => r.url().endsWith("/auth/login") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "登录平台" }).click();
  expect((await response).status()).toBe(200);
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
}
test("global regulator searches committed audit events while enterprise cannot access audit logs", async ({
  browser,
}) => {
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.company_fixture", "create"],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
  ]);
  try {
    const owner = await contexts[0].newPage(),
      reg = await contexts[1].newPage();
    await login(owner, fixture.username);
    await expect(
      owner.getByRole("button", { name: "系统操作日志", exact: true }),
    ).toHaveCount(0);
    expect((await owner.request.get("/api/v1/audits")).status()).toBe(403);
    for (let index = 0; index < 23; index++)
      expect(
        (await owner.request.get("/api/v1/companies/export")).status(),
      ).toBe(200);
    await login(reg, "regulator");
    await reg
      .getByRole("button", { name: "系统操作日志", exact: true })
      .click();
    await reg.getByLabel("操作人账号").fill(fixture.username);
    await reg.getByLabel("动作代码").fill("company.export");
    await expect(reg.getByRole("button", { name: "查询日志" })).toBeEnabled();
    await reg.getByRole("button", { name: "查询日志" }).click();
    await expect(reg.getByText("第1页 · 共23条")).toBeVisible();
    await reg.getByText("查看结构化详情").first().click();
    await expect(reg.locator("pre").first()).toContainText('"rows": 1');
    await reg.getByRole("button", { name: "日志下一页" }).click();
    await expect(reg.getByText("第2页 · 共23条")).toBeVisible();
    await expect(reg.getByRole("row")).toHaveCount(4);
    await reg.getByLabel("动作代码").fill("unrecorded.action");
    await reg.getByRole("button", { name: "查询日志" }).click();
    await expect(reg.getByText("当前条件下没有操作日志")).toBeVisible();
    await reg.getByLabel("动作代码").fill("company.export");
    await reg.getByRole("button", { name: "查询日志" }).click();
    await expect(reg.getByText("第1页 · 共23条")).toBeVisible();
    await reg.screenshot({
      path: "test-results/audit-search.png",
      fullPage: true,
    });
  } finally {
    await Promise.allSettled(contexts.map((c) => c.close()));
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.company_fixture",
        "clean",
        "--company",
        String(fixture.id),
      ],
      { cwd: "../server", stdio: "pipe" },
    );
  }
});
