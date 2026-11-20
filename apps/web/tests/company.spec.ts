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
  await expect(
    page.getByRole("heading", { name: /^(运营总览|接入汇总)$/ }),
  ).toBeVisible();
}
test("company contacts are maintained and disabling revokes enterprise sessions", async ({
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
    const [op, reg] = await Promise.all(contexts.map((c) => c.newPage()));
    await login(op, fixture.username);
    await login(reg, "regulator");
    await navigateWorkspace(op, "企业资料");
    await op.getByRole("button", { name: "查看企业资料" }).click();
    await op.getByRole("button", { name: "维护企业资料" }).click();
    await op
      .getByLabel("信用代码", { exact: true })
      .fill("A" + String(fixture.id).padStart(17, "0"));
    await op.getByLabel("注册地址", { exact: true }).fill("演示地址");
    await op.getByRole("button", { name: "添加联系人" }).click();
    await op.getByLabel("联系人1姓名").fill("演示联系人甲");
    await op.getByLabel("联系人1电话").fill("100001");
    await op.getByRole("button", { name: "添加联系人" }).click();
    await op.getByLabel("联系人2姓名").fill("演示联系人乙");
    await op.getByLabel("联系人2电话").fill("100002");
    await op.getByRole("button", { name: "保存企业资料" }).click();
    await expect(op.getByText("演示联系人乙", { exact: true })).toBeVisible();
    await navigateWorkspace(reg, /^(企业台账|场站运营商管理)$/);
    await reg.getByLabel("搜索企业名称").fill(fixture.name);
    await reg.getByRole("button", { name: "查询企业", exact: true }).click();
    await reg
      .getByRole("row")
      .filter({
        has: reg.getByRole("cell", { name: fixture.name, exact: true }),
      })
      .getByRole("button", { name: "查看企业资料" })
      .click();
    await expect(reg.getByText("演示联系人甲", { exact: true })).toBeVisible();
    await reg
      .getByRole("button", { name: "返回运营商列表", exact: true })
      .click();
    const exportLink = reg.getByRole("link", { name: "导出企业CSV" });
    await expect(exportLink).toBeVisible();
    const csv = await reg.request.get((await exportLink.getAttribute("href"))!);
    expect(csv.status()).toBe(200);
    expect(await csv.text()).toContain(fixture.name);
    await reg
      .getByRole("button", { name: "查看企业资料", exact: true })
      .click();
    await reg.getByLabel("企业启停原因").fill("演示账号停用验证");
    await reg.getByRole("button", { name: "停用企业账号" }).click();
    await reg
      .getByRole("button", { name: "返回运营商列表", exact: true })
      .click();
    await expect(
      reg.getByRole("row").filter({
        has: reg.getByRole("cell", { name: fixture.name, exact: true }),
      }),
    ).toContainText("企业已停用");
    expect((await op.request.get("/api/v1/auth/me")).status()).toBe(401);
    await op.getByRole("button", { name: "刷新数据", exact: true }).click();
    await expect(op.getByRole("heading", { name: "登录工作台" })).toBeVisible();
    await reg.screenshot({
      path: "test-results/company-profile.png",
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
