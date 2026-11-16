import { test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { randomBytes } from "node:crypto";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
function code(token: string) {
  return JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.sms_fixture", "--", token],
      { cwd: "../server", encoding: "utf8" },
    ),
  ).code as string;
}
async function passwordLogin(page: Page, username: string, secret: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(secret);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
}
test("bound account uses simulated SMS login and recovers password while revoking both devices", async ({
  page,
  browser,
}) => {
  const actor = JSON.parse(
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.permission_fixture",
        "create",
        "--module",
        "content",
        "--kind",
        "manage",
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const context = await browser.newContext();
  try {
    await passwordLogin(page, actor.username, password);
    const me = await (await page.request.get("/api/v1/auth/me")).json();
    const issued = await page.request.post("/api/v1/auth/phone/challenges", {
      headers: { "x-csrf-token": me.csrf_token },
      data: {
        phone: "139" + String(actor.id).padStart(8, "0"),
        current_password: password,
      },
    });
    expect(issued.status()).toBe(201);
    const binding = await issued.json();
    const bound = await page.request.post("/api/v1/auth/phone/confirm", {
      headers: { "x-csrf-token": me.csrf_token },
      data: {
        challenge_id: binding.challenge_id,
        code: code(binding.challenge_id),
      },
    });
    expect(bound.status()).toBe(200);
    await page.goto("/sms-login");
    await expect(
      page.getByRole("heading", { name: "短信验证码登录", exact: true }),
    ).toBeVisible();
    await page
      .getByLabel("手机验证账号或手机号", { exact: true })
      .fill(actor.username);
    const smsLogin = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/auth/sms/challenges") &&
        r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "获取手机验证码", exact: true })
      .click();
    const response = await smsLogin;
    expect(response.status()).toBe(201);
    const loginChallenge = await response.json();
    expect(loginChallenge.code).toBeUndefined();
    await expect(page.getByRole("status")).toContainText("未发送真实短信");
    await page.screenshot({
      path: "test-results/sms-login.png",
      fullPage: true,
    });
    await page
      .getByLabel("登录找回手机验证码", { exact: true })
      .fill(code(loginChallenge.challenge_id));
    await page.getByRole("button", { name: "验证并登录", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "退出登录", exact: true }),
    ).toBeVisible();
    expect(
      (await (await page.request.get("/api/v1/auth/me")).json()).username,
    ).toBe(actor.username);
    const stale = await context.newPage();
    await passwordLogin(stale, actor.username, password);
    await page.goto("/recover");
    await page
      .getByLabel("手机验证账号或手机号", { exact: true })
      .fill(actor.username);
    const recovery = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/auth/sms/challenges") &&
        r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "获取手机验证码", exact: true })
      .click();
    const resetResponse = await recovery;
    expect(resetResponse.status()).toBe(201);
    const resetChallenge = await resetResponse.json();
    const next = "Recovered-Browser-" + randomBytes(12).toString("hex");
    await page
      .getByLabel("登录找回手机验证码", { exact: true })
      .fill(code(resetChallenge.challenge_id));
    await page.getByLabel("找回新密码", { exact: true }).fill(next);
    await page.getByLabel("确认找回新密码", { exact: true }).fill(next);
    await page
      .getByRole("button", { name: "确认重置密码", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "登录工作台", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("密码已重置，全部设备已退出。请使用新密码重新登录。", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(page.getByLabel("账号", { exact: true })).toHaveValue(
      actor.username,
    );
    expect((await stale.request.get("/api/v1/auth/me")).status()).toBe(401);
    await expect(page.getByLabel("密码", { exact: true })).toHaveValue("");
    await passwordLogin(page, actor.username, next);
  } finally {
    await context.close();
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.permission_fixture",
        "clean",
        "--user",
        String(actor.id),
      ],
      { cwd: "../server", stdio: "pipe" },
    );
  }
});
