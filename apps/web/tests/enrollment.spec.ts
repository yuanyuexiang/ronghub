import { navigateWorkspace, test, expect } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
test("enterprise enrollment materials, regulatory approval and first platform login", async ({
  browser,
}) => {
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.registration_fixture", "role"],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
  ]);
  try {
    const [portal, reg] = await Promise.all(contexts.map((c) => c.newPage()));
    await portal.goto("/");
    await portal.getByRole("link", { name: "企业注册 / 注册进度查询" }).click();
    await expect(
      portal.getByRole("heading", { name: "新企业注册", exact: true }),
    ).toBeVisible();
    await portal.getByLabel("企业注册账号").fill(fixture.username);
    await portal.getByLabel("企业注册密码").fill(password);
    await portal.getByLabel("注册企业名称").fill(fixture.company_name);
    await portal.getByLabel("注册信用代码").fill(fixture.credit_code);
    await portal.getByLabel("注册联系人1姓名").fill("演示注册联系人");
    await portal.getByLabel("注册联系人1电话").fill("100003");
    const created = portal.waitForResponse(
      (r) => r.url().endsWith("/enrollment") && r.request().method() === "POST",
    );
    await portal.getByRole("button", { name: "建立企业注册草稿" }).click();
    const response = await created;
    expect(response.status()).toBe(201);
    const id = (await response.json()).id;
    await expect(
      portal.getByRole("heading", { name: `REG-${id} · 草稿待提交` }),
    ).toBeVisible();
    await portal.getByLabel("注册企业地址").fill("演示注册地");
    await expect(portal.getByLabel("上传注册资质")).toBeDisabled();
    await portal.getByRole("button", { name: "保存注册资料" }).click();
    await expect(portal.getByLabel("上传注册资质")).toBeEnabled();
    await portal.getByLabel("上传注册资质").setInputFiles({
      name: "企业资质.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4\nEnrollment test material"),
    });
    await expect(
      portal.getByRole("link", { name: "企业资质.pdf" }),
    ).toBeVisible();
    await portal.getByRole("button", { name: "提交注册审核" }).click();
    await expect(
      portal.getByRole("heading", { name: `REG-${id} · 待监管审核` }),
    ).toBeVisible();
    await expect(portal.getByLabel("上传注册资质")).toHaveCount(0);
    await reg.goto("/");
    await reg.getByLabel("账号", { exact: true }).fill("regulator");
    await reg.getByLabel("密码", { exact: true }).fill(password);
    await reg.getByRole("button", { name: "登录平台" }).click();
    await navigateWorkspace(reg, /^(注册审核|运营商审核)$/);
    const row = reg.getByRole("row").filter({
      has: reg.getByRole("cell", { name: `REG-${id}`, exact: true }),
    });
    await row.getByRole("button", { name: "查看注册资料" }).click();
    const evidence = reg.getByRole("link", { name: "企业资质.pdf" });
    await expect(evidence).toBeVisible();
    expect(
      (await reg.request.get((await evidence.getAttribute("href"))!)).status(),
    ).toBe(200);
    await reg
      .getByLabel("注册开通权限角色")
      .selectOption(String(fixture.role_id));
    await reg.getByLabel("注册审核意见").fill("演示资质核验通过");
    await reg.getByRole("button", { name: "通过注册并开通账号" }).click();
    await expect(reg.locator(".record-detail")).toContainText("审核通过已开通");
    await reg
      .getByRole("button", { name: "关闭注册详情", exact: true })
      .click();
    await expect(
      reg.getByRole("heading", { name: "企业注册审核", exact: true }),
    ).toBeFocused();
    await expect(row).toContainText("审核通过已开通");
    await portal.getByRole("button", { name: "刷新注册进度" }).click();
    await expect(
      portal.getByRole("heading", { name: `REG-${id} · 审核通过，账号已开通` }),
    ).toBeVisible();
    await portal.screenshot({
      path: "test-results/enrollment-approved.png",
      fullPage: true,
    });
    await portal.getByRole("link", { name: "前往平台登录" }).click();
    await portal.getByLabel("账号", { exact: true }).fill(fixture.username);
    await portal.getByLabel("密码", { exact: true }).fill(password);
    await portal.getByRole("button", { name: "登录平台" }).click();
    await expect(
      portal.getByRole("heading", {
        name: /^(运营总览|接入汇总|多维度数据筛选)$/,
      }),
    ).toBeVisible();
    await navigateWorkspace(portal, "企业资料");
    await expect(
      portal.getByRole("cell", { name: fixture.company_name, exact: true }),
    ).toBeVisible();
  } finally {
    await Promise.allSettled(contexts.map((c) => c.close()));
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.registration_fixture",
        "clean",
        "--role",
        String(fixture.role_id),
      ],
      { cwd: "../server", stdio: "pipe" },
    );
  }
});

test("applicant-cookie expiry stays separate from the business login", async ({
  page,
  context,
}) => {
  await context.addInitScript(() => {
    window.addEventListener("ronghub:session-expired", () => {
      document.documentElement.dataset.businessExpiry = "unexpected";
    });
  });
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("regulator");
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
  const missing = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/enrollment/me") &&
      r.request().method() === "GET",
  );
  await page.goto("/register");
  expect((await missing).status()).toBe(401);
  await expect(
    page.getByRole("heading", { name: "新企业注册", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.dataset.businessExpiry),
  ).toBeUndefined();
  const identity = await page.request.get("/api/v1/auth/me");
  expect(identity.status()).toBe(200);
  expect((await identity.json()).username).toBe("regulator");
});
