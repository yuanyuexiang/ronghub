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
test("district assignment narrows the same enterprise and scopes exports", async ({
  browser,
}) => {
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.company_fixture",
        "create",
        "--with-stations",
      ],
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
    const me = await (await reg.request.get("/api/v1/auth/me")).json();
    const response = await reg.request.post("/api/v1/roles", {
      headers: { "x-csrf-token": me.csrf_token },
      data: {
        name: fixture.name + "区域角色",
        base_role: "operator",
        permissions: [
          "stations.read",
          "companies.read",
          "companies.write",
          "statistics.read",
          "statistics.export",
        ],
      },
    });
    expect(response.status()).toBe(201);
    profileId = (await response.json()).id;
    await reg.getByRole("button", { name: "权限与账号", exact: true }).click();
    await reg.getByLabel("搜索账号").fill(fixture.username);
    await reg.getByRole("button", { name: "查询账号", exact: true }).click();
    const row = reg.getByRole("row").filter({
      has: reg.getByRole("cell", { name: fixture.username, exact: true }),
    });
    await row.getByRole("button", { name: "配置账号权限" }).click();
    await reg.getByLabel("账号权限角色").selectOption(String(profileId));
    await reg.getByLabel("账号授权全部区域", { exact: true }).uncheck();
    await reg.getByLabel("账号授权区域清单", { exact: true }).fill("鼓楼区");
    await reg.getByLabel("账号权限变更依据").fill("演示鼓楼区域岗位");
    await reg.getByRole("button", { name: "保存账号授权" }).click();
    await expect(row).toContainText("鼓楼区");
    expect((await op.request.get("/api/v1/auth/me")).status()).toBe(401);
    await op.reload();
    await expect(op.getByRole("heading", { name: "登录工作台" })).toBeVisible();
    await login(op, fixture.username);
    await expect(
      op.getByText(fixture.stations[0].name, { exact: true }),
    ).toBeVisible();
    await expect(
      op.getByText(fixture.stations[1].name, { exact: true }),
    ).toHaveCount(0);
    await expect(
      op.getByText("授权区域：鼓楼区", { exact: true }),
    ).toBeVisible();
    expect(
      (
        await op.request.get("/api/v1/stations/" + fixture.stations[1].id)
      ).status(),
    ).toBe(404);
    const csv = await op.request.get(
      "/api/v1/statistics/export?start_date=2026-11-01&end_date=2026-11-15",
    );
    expect(csv.status()).toBe(200);
    const text = await csv.text();
    expect(text).toContain(fixture.stations[0].name);
    expect(text).not.toContain(fixture.stations[1].name);
    await expect(
      op.locator(".metrics article").filter({ hasText: "接口有效电量" }),
    ).toContainText("暂无数据");
    await op.screenshot({
      path: "test-results/district-scoped-workspace.png",
      fullPage: true,
    });
    await op.getByRole("button", { name: "企业资料", exact: true }).click();
    await op.getByRole("button", { name: "查看企业资料" }).click();
    await expect(op.getByRole("button", { name: "维护企业资料" })).toHaveCount(
      0,
    );
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
