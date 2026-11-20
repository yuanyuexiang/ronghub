import { navigateWorkspace, test, expect, Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
const fixture = (module: string, args: string[]) =>
  JSON.parse(
    execFileSync("uv", ["run", "python", "-m", module, ...args], {
      cwd: "../server",
      encoding: "utf8",
    }),
  );
async function login(page: Page, name: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(name);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台" }).click();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
}
test("submit-only enterprise staff can submit without editing, uploading or withdrawing", async ({
  browser,
}) => {
  const station = fixture("app.browser_fixture", ["create"]),
    submitter = fixture("app.permission_fixture", [
      "create",
      "--module",
      "applications",
      "--kind",
      "submit",
    ]);
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
  ]);
  try {
    const owner = await contexts[0].newPage(),
      limited = await contexts[1].newPage();
    await login(owner, "operator_a");
    await login(limited, submitter.username);
    const identity = await (await owner.request.get("/api/v1/auth/me")).json();
    const created = await owner.request.post("/api/v1/applications", {
      headers: { "x-csrf-token": identity.csrf_token },
      data: {
        station_id: station.station_id,
        kind: "access",
        payload: { note: "申请准备人员已填写" },
      },
    });
    expect(created.status()).toBe(201);
    const record = await created.json();
    await navigateWorkspace(limited, /^(场站申请|接入管理)$/);
    await expect(
      limited.getByRole("button", { name: "新建草稿", exact: true }),
    ).toHaveCount(0);
    await limited.getByLabel("检索申请编号").fill(String(record.id));
    await expect(
      limited.getByRole("button", { name: "查询申请", exact: true }),
    ).toBeEnabled();
    await limited
      .getByRole("button", { name: "查询申请", exact: true })
      .click();
    const row = limited.getByRole("row").filter({
      has: limited.getByRole("cell", {
        name: `APP-${record.id}`,
        exact: true,
      }),
    });
    await row.getByRole("button", { name: "查看处理", exact: true }).click();
    await expect(
      limited.getByRole("button", { name: "保存申请信息", exact: true }),
    ).toHaveCount(0);
    await expect(
      limited.getByLabel("上传申请材料", { exact: true }),
    ).toHaveCount(0);
    await expect(
      limited.getByRole("button", { name: "撤回申请", exact: true }),
    ).toHaveCount(0);
    const posted = limited.waitForResponse((r) =>
      r.url().endsWith(`/applications/${record.id}/actions`),
    );
    await limited
      .getByRole("button", { name: "提交审核", exact: true })
      .click();
    expect((await posted).status()).toBe(200);
    await expect(
      limited.getByRole("region", { name: "申请详情工作区", exact: true }),
    ).toContainText("待审核");
    await expect(limited.getByLabel("处理意见", { exact: true })).toHaveCount(
      0,
    );
    await expect(
      limited.getByText("当前记录无可执行操作，可查看材料和处理历史。", {
        exact: true,
      }),
    ).toBeVisible();
    await limited.screenshot({
      path: "test-results/application-submit-only.png",
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
        "app.browser_fixture",
        "clean",
        "--station",
        String(station.station_id),
      ],
      { cwd: "../server", stdio: "pipe" },
    );
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.permission_fixture",
        "clean",
        "--user",
        String(submitter.id),
      ],
      { cwd: "../server", stdio: "pipe" },
    );
  }
});
