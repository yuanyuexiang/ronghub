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
  await expect(
    page.getByRole("heading", { name: "运营总览" }),
  ).toBeVisible();
}
test("configured read-only role hides writes and revokes the prior session", async ({
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
  let profileId: number | undefined;
  try {
    const [reg, op] = await Promise.all(contexts.map((c) => c.newPage()));
    await login(reg, "regulator");
    await login(op, fixture.username);
    await reg.getByRole("button", { name: "权限与账号", exact: true }).click();
    await reg
      .getByRole("button", { name: "新建权限角色", exact: true })
      .click();
    const profileName = fixture.name + "只读";
    await reg.getByLabel("权限角色名称").fill(profileName);
    await reg.getByLabel("权限基础身份").selectOption("operator");
    await reg.getByLabel("权限-applications.read", { exact: true }).check();
    await reg.getByLabel("权限-companies.read", { exact: true }).check();
    const created = reg.waitForResponse(
      (r) => r.url().endsWith("/roles") && r.request().method() === "POST",
    );
    await reg.getByRole("button", { name: "保存权限角色" }).click();
    const response = await created;
    expect(response.status()).toBe(201);
    profileId = (await response.json()).id;
    await reg.getByLabel("搜索账号").fill(fixture.username);
    await reg.getByRole("button", { name: "查询账号", exact: true }).click();
    const row = reg.getByRole("row").filter({
      has: reg.getByRole("cell", { name: fixture.username, exact: true }),
    });
    await row.getByRole("button", { name: "配置账号权限" }).click();
    await reg.getByLabel("账号权限角色").selectOption(String(profileId));
    await reg.getByLabel("账号权限变更依据").fill("演示只读岗位分工");
    await reg.getByRole("button", { name: "保存账号授权" }).click();
    await expect(row).toContainText(profileName);
    expect((await op.request.get("/api/v1/auth/me")).status()).toBe(401);
    await op.reload();
    await expect(op.getByRole("heading", { name: "登录工作台" })).toBeVisible();
    await login(op, fixture.username);
    await expect(
      op.getByRole("button", { name: "补贴申报", exact: true }),
    ).toHaveCount(0);
    await expect(op.getByText("已审定补贴", { exact: true })).toHaveCount(0);
    await expect(op.getByText("undefined", { exact: true })).toHaveCount(0);
    await op.getByRole("button", { name: "场站申请", exact: true }).click();
    await expect(
      op.getByRole("heading", { name: "我的场站申请" }),
    ).toBeVisible();
    await expect(
      op.getByRole("button", { name: "新建草稿", exact: true }),
    ).toHaveCount(0);
    await op.getByRole("button", { name: "企业资料", exact: true }).click();
    await op.getByRole("button", { name: "查看企业资料" }).click();
    await expect(op.getByRole("button", { name: "维护企业资料" })).toHaveCount(
      0,
    );
    await expect(op.getByRole("link", { name: "导出企业CSV" })).toHaveCount(0);
    expect((await op.request.get("/api/v1/subsidy/claims")).status()).toBe(403);
    await op.screenshot({
      path: "test-results/limited-company-role.png",
      fullPage: true,
    });
  } finally {
    await Promise.allSettled(contexts.map((c) => c.close()));
    const args = [
      "run",
      "python",
      "-m",
      "app.company_fixture",
      "clean",
      "--company",
      String(fixture.id),
    ];
    if (profileId) args.push("--profile", String(profileId));
    execFileSync("uv", args, { cwd: "../server", stdio: "pipe" });
  }
});

test("administrator-created account rotates its temporary password before any business access", async ({
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
  let profileId: number | undefined;
  const temporary = password + "-temporary",
    personal = password + "-personal";
  const username = fixture.username + "-managed";
  try {
    const root = await contexts[0].newPage(),
      member = await contexts[1].newPage();
    await login(root, "regulator");
    const csrf = (await (await root.request.get("/api/v1/auth/me")).json())
      .csrf_token;
    const profile = await root.request.post("/api/v1/roles", {
      headers: { "x-csrf-token": csrf },
      data: {
        name: fixture.name + "初始只读",
        base_role: "operator",
        permissions: ["stations.read"],
      },
    });
    expect(profile.status()).toBe(201);
    profileId = (await profile.json()).id;
    await root.getByRole("button", { name: "权限与账号", exact: true }).click();
    await root.getByRole("button", { name: "新建账号", exact: true }).click();
    await expect(
      root.getByText(
        "至少12个字符。新账号首次登录必须修改临时密码，改密后重新登录。",
        { exact: true },
      ),
    ).toBeVisible();
    await root.getByLabel("新建账号名称", { exact: true }).fill(username);
    await root.getByLabel("新建账号密码", { exact: true }).fill(temporary);
    await root
      .getByLabel("新建账号角色", { exact: true })
      .selectOption(String(profileId));
    await root
      .getByLabel("新建账号企业", { exact: true })
      .selectOption(String(fixture.id));
    const created = root.waitForResponse(
      (response) =>
        response.url().endsWith("/accounts") &&
        response.request().method() === "POST",
    );
    await root.getByRole("button", { name: "创建账号", exact: true }).click();
    const response = await created;
    expect(response.status()).toBe(201);
    expect((await response.json()).must_change_password).toBe(true);
    await root.getByLabel("搜索账号", { exact: true }).fill(username);
    await root.getByRole("button", { name: "查询账号", exact: true }).click();
    await expect(
      root
        .getByRole("row")
        .filter({
          has: root.getByRole("cell", { name: username, exact: true }),
        }),
    ).toContainText("待首次改密");
    await member.goto("/");
    await member.getByLabel("账号", { exact: true }).fill(username);
    await member.getByLabel("密码", { exact: true }).fill(temporary);
    await member.getByRole("button", { name: "登录平台", exact: true }).click();
    await expect(
      member.getByRole("heading", {
        name: "首次登录请修改临时密码",
        exact: true,
      }),
    ).toBeVisible();
    expect((await member.request.get("/api/v1/stations")).status()).toBe(403);
    expect((await member.request.get("/api/v1/accounts")).status()).toBe(403);
    await member.getByLabel("本人当前密码", { exact: true }).fill(temporary);
    await member.getByLabel("本人新密码", { exact: true }).fill(personal);
    await member.getByLabel("确认本人新密码", { exact: true }).fill(personal);
    await member
      .getByRole("button", { name: "更新密码并重新登录", exact: true })
      .click();
    await expect(
      member.getByRole("heading", { name: "登录工作台", exact: true }),
    ).toBeVisible();
    await member.getByLabel("账号", { exact: true }).fill(username);
    await member.getByLabel("密码", { exact: true }).fill(personal);
    await member.getByRole("button", { name: "登录平台", exact: true }).click();
    await expect(
      member.getByRole("heading", { name: "运营总览", exact: true }),
    ).toBeVisible();
    expect((await member.request.get("/api/v1/auth/me")).status()).toBe(200);
    expect((await member.request.get("/api/v1/stations")).status()).toBe(200);
    expect((await member.request.get("/api/v1/accounts")).status()).toBe(403);
    await root.getByRole("button", { name: "刷新数据", exact: true }).click();
    await expect(
      root
        .getByRole("row")
        .filter({
          has: root.getByRole("cell", { name: username, exact: true }),
        }),
    ).not.toContainText("待首次改密");
    await root.screenshot({
      path: "test-results/initial-password-account.png",
      fullPage: true,
    });
  } finally {
    await Promise.allSettled(contexts.map((context) => context.close()));
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
        ...(profileId ? ["--profile", String(profileId)] : []),
      ],
      { cwd: "../server", stdio: "pipe" },
    );
  }
});
