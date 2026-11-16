import { test, expect } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
test("phone binding marks simulation, verifies code, revokes sessions and shows only masked phone", async ({
  page,
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
  try {
    const login = async () => {
      await page.goto("/");
      await page.getByLabel("账号", { exact: true }).fill(actor.username);
      await page.getByLabel("密码", { exact: true }).fill(password);
      await page.getByRole("button", { name: "登录平台", exact: true }).click();
      await expect(
        page.getByRole("button", { name: "退出登录", exact: true }),
      ).toBeVisible();
    };
    await login();
    await page.getByRole("button", { name: "账号安全", exact: true }).click();
    await expect(
      page.getByText(
        "模拟短信通道：未发送真实短信。请由开发者使用本地模拟验证工具提供本次验证码。",
        { exact: true },
      ),
    ).toBeVisible();
    const phone = "139" + String(actor.id).padStart(8, "0");
    await page.getByLabel("账号绑定手机号", { exact: true }).fill(phone);
    await page.getByLabel("绑定手机当前密码", { exact: true }).fill(password);
    const issued = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/auth/phone/challenges") &&
        r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "获取绑定短信验证码", exact: true })
      .click();
    const response = await issued;
    expect(response.status()).toBe(201);
    const challenge = await response.json();
    expect(challenge.delivery_mode).toBe("demo");
    expect(challenge.code).toBeUndefined();
    const prepared = JSON.parse(
      execFileSync(
        "uv",
        [
          "run",
          "python",
          "-m",
          "app.sms_fixture",
          "--",
          challenge.challenge_id,
        ],
        { cwd: "../server", encoding: "utf8" },
      ),
    );
    await page
      .getByLabel("手机短信验证码", { exact: true })
      .fill(prepared.code);
    await page
      .getByRole("button", { name: "确认验证绑定手机", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "登录工作台", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("手机号已完成模拟验证绑定，请重新登录。", { exact: true }),
    ).toBeVisible();
    expect((await page.request.get("/api/v1/auth/me")).status()).toBe(401);
    await login();
    await page.getByRole("button", { name: "账号安全", exact: true }).click();
    await expect(
      page.getByText("已验证手机号（模拟验证）：", { exact: false }),
    ).toBeVisible();
    await expect(
      page.getByText(phone.slice(0, 3) + "****" + phone.slice(-4), {
        exact: true,
      }),
    ).toBeVisible();
    const state = await (await page.request.get("/api/v1/auth/phone")).json();
    expect(state.verification_mode).toBe("demo");
    expect(state.phone).toBeUndefined();
    await page.screenshot({
      path: "test-results/phone-binding.png",
      fullPage: true,
    });
  } finally {
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
