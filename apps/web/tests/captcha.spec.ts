import { test, expect, prepareCaptcha } from "./fixtures";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
test.use({ automaticCaptcha: false });
test("password login requires one-use image challenge and refreshes after an invalid attempt", async ({
  page,
}) => {
  await page.goto("/");
  const image = page.getByRole("img", { name: "登录图片验证码", exact: true });
  await expect(image).toHaveAttribute("data-captcha-id", /.+/);
  const first = (await image.getAttribute("data-captcha-id"))!;
  await page.getByRole("button", { name: "换一张验证码", exact: true }).click();
  await expect(image).not.toHaveAttribute("data-captcha-id", first);
  await page.getByLabel("账号", { exact: true }).fill("regulator");
  await page.getByLabel("密码", { exact: true }).fill(password);
  await prepareCaptcha(page);
  const attempted = (await image.getAttribute("data-captcha-id"))!;
  await page.getByLabel("图片验证码", { exact: true }).fill("WRONG");
  const rejected = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/auth/login") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  expect((await rejected).status()).toBe(422);
  await expect(page.locator(".login-form .error[role=alert]")).toContainText(
    "验证码无效或已过期",
  );
  await expect(image).not.toHaveAttribute("data-captcha-id", attempted);
  await expect(page.getByLabel("图片验证码", { exact: true })).toHaveValue("");
  expect((await page.request.get("/api/v1/auth/me")).status()).toBe(401);
  await prepareCaptcha(page);
  const accepted = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/auth/login") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  expect((await accepted).status()).toBe(200);
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(page.getByLabel("密码", { exact: true })).toHaveValue("");
  await expect(image).toHaveAttribute("data-captcha-id", /.+/);
  await page.screenshot({
    path: "test-results/login-captcha.png",
    fullPage: true,
  });
});
