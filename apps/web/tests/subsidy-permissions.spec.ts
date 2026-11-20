import { subsidyZip } from "./subsidy-fixture";
import { navigateWorkspace, test, expect, Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { pickStation } from "./station";
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
test("review-only staff can approve but cannot adjust, edit policy or record disbursement", async ({
  browser,
}) => {
  const station = fixture("app.browser_fixture", ["create", "--operating"]),
    reviewer = fixture("app.permission_fixture", [
      "create",
      "--kind",
      "review",
    ]);
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
  ]);
  try {
    const owner = await contexts[0].newPage(),
      reg = await contexts[1].newPage();
    await login(owner, "operator_a");
    await login(reg, reviewer.username);
    await navigateWorkspace(owner, "补贴申报");
    await pickStation(owner, "补贴场站", station);
    await owner
      .getByLabel("补贴政策")
      .selectOption({ label: "演示按电量补贴（非正式政策） V1" });
    await owner.getByLabel("补贴开始日期").fill("2026-11-01");
    await owner.getByLabel("补贴结束日期").fill("2026-11-30");
    await owner
      .getByLabel("补贴申请说明", { exact: true })
      .fill("演示申报说明与核验材料");
    const created = owner.waitForResponse(
      (r) =>
        r.url().endsWith("/subsidy/claims") && r.request().method() === "POST",
    );
    await owner
      .getByRole("button", { name: "核算并创建草稿", exact: true })
      .click();
    const record = await (await created).json();
    await expect(
      owner.getByLabel("上传补贴ZIP材料", { exact: true }),
    ).toBeEnabled();
    await owner.getByLabel("上传补贴ZIP材料", { exact: true }).setInputFiles({
      name: "申报资料.zip",
      mimeType: "application/zip",
      buffer: subsidyZip,
    });
    await expect(
      owner.getByRole("button", { name: "提交补贴审核", exact: true }),
    ).toBeEnabled();
    const submitted = owner.waitForResponse((r) =>
      r.url().endsWith(`/claims/${record.id}/actions`),
    );
    await owner
      .getByRole("button", { name: "提交补贴审核", exact: true })
      .click();
    expect((await submitted).status()).toBe(200);
    await navigateWorkspace(reg, "补贴申请记录");
    await expect(
      reg.getByRole("button", { name: "新增政策版本", exact: true }),
    ).toHaveCount(0);
    await reg.getByLabel("补贴检索编号").fill(String(record.id));
    await expect(
      reg.getByRole("button", { name: "查询补贴记录", exact: true }),
    ).toBeEnabled();
    await reg
      .getByRole("button", { name: "查询补贴记录", exact: true })
      .click();
    await reg
      .getByRole("row")
      .filter({
        has: reg.getByRole("cell", { name: `SUB-${record.id}`, exact: true }),
      })
      .getByRole("button", { name: "补贴详情", exact: true })
      .click();
    await expect(
      reg.getByRole("button", { name: "登记金额调整", exact: true }),
    ).toHaveCount(0);
    await expect(reg.getByLabel("补贴调整差额")).toHaveCount(0);
    await reg.getByLabel("补贴处理意见").fill("仅审核权限核验，按原金额审定");
    const approved = reg.waitForResponse((r) =>
      r.url().endsWith(`/claims/${record.id}/actions`),
    );
    await reg
      .getByRole("button", { name: "通过补贴审核", exact: true })
      .click();
    expect((await approved).status()).toBe(200);
    await expect(
      reg.getByRole("button", { name: "登记发放状态（不付款）", exact: true }),
    ).toHaveCount(0);
    await reg.screenshot({
      path: "test-results/subsidy-review-only.png",
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
        String(reviewer.id),
      ],
      { cwd: "../server", stdio: "pipe" },
    );
  }
});
