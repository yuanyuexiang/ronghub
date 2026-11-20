import { test, expect, Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { randomBytes } from "node:crypto";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, name: string, value: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(name);
  await page.getByLabel("密码", { exact: true }).fill(value);
  const done = page.waitForResponse(
    (r) => r.url().endsWith("/auth/login") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "登录平台" }).click();
  expect((await done).status()).toBe(200);
}
test("admin reset revokes sessions and user must change temporary password before returning", async ({
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
  const temporary = randomBytes(24).toString("base64url"),
    permanent = randomBytes(24).toString("base64url");
  try {
    const [reg, owner] = await Promise.all(contexts.map((c) => c.newPage()));
    await login(reg, "regulator", password);
    await login(owner, fixture.username, password);
    await expect(
      owner.getByRole("heading", { name: "运营总览" }),
    ).toBeVisible();
    await reg.getByRole("button", { name: "权限与账号", exact: true }).click();
    await reg.getByLabel("搜索账号").fill(fixture.username);
    await reg.getByRole("button", { name: "查询账号", exact: true }).click();
    await reg
      .getByRole("row")
      .filter({
        has: reg.getByRole("cell", { name: fixture.username, exact: true }),
      })
      .getByRole("button", { name: "配置账号权限" })
      .click();
    await reg.getByLabel("重置验证管理员密码").fill(password);
    await reg.getByLabel("账号临时密码", { exact: true }).fill(temporary);
    await reg.getByLabel("确认账号临时密码", { exact: true }).fill(temporary);
    await reg.getByLabel("密码重置依据").fill("已通过演示身份资料核验");
    const reset = reg.waitForResponse(
      (r) =>
        r.url().endsWith("/password-reset") && r.request().method() === "POST",
    );
    await reg.getByRole("button", { name: "重置并要求首次改密" }).click();
    expect((await reset).status()).toBe(200);
    expect((await owner.request.get("/api/v1/auth/me")).status()).toBe(401);
    await login(owner, fixture.username, temporary);
    await expect(
      owner.getByRole("heading", { name: "首次登录请修改临时密码" }),
    ).toBeVisible();
    await expect(
      owner.getByRole("button", { name: /^(场站台账|场站信息查询)$/, exact: true }),
    ).toHaveCount(0);
    expect((await owner.request.get("/api/v1/stations")).status()).toBe(403);
    await owner.getByLabel("本人当前密码").fill(temporary);
    await owner.getByLabel("本人新密码", { exact: true }).fill(permanent);
    await owner.getByLabel("确认本人新密码", { exact: true }).fill(permanent);
    await owner.getByRole("button", { name: "更新密码并重新登录" }).click();
    await expect(
      owner.getByRole("heading", { name: "登录工作台" }),
    ).toBeVisible();
    await expect(owner.getByRole("status")).toContainText("密码已更新");
    await login(owner, fixture.username, permanent);
    await expect(
      owner.getByRole("heading", { name: "运营总览" }),
    ).toBeVisible();
    await owner.getByRole("button", { name: "账号安全", exact: true }).click();
    await expect(
      owner.getByRole("heading", { name: "修改本人密码" }),
    ).toBeVisible();
    await owner.screenshot({
      path: "test-results/password-management.png",
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
