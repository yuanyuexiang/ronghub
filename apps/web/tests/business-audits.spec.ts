import { subsidyZip } from "./subsidy-fixture";
import { test, expect, Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, name: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(name);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台" }).click();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
  return (await (await page.request.get("/api/v1/auth/me")).json()).csrf_token;
}
test("committed subsidy adjustment exposes exact before and after amounts in audit search", async ({
  browser,
}) => {
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.browser_fixture", "create", "--operating"],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
  ]);
  try {
    const [operator, reg] = await Promise.all(contexts.map((c) => c.newPage()));
    const opHeaders = { "x-csrf-token": await login(operator, "operator_a") };
    const regHeaders = { "x-csrf-token": await login(reg, "regulator") };
    const policies = await (
      await operator.request.get("/api/v1/subsidy/policies")
    ).json();
    const policy = policies.items.find(
      (p: { code: string; version: number }) =>
        p.code === "DEMO-KWH" && p.version === 1,
    );
    const created = await operator.request.post("/api/v1/subsidy/claims", {
      headers: opHeaders,
      data: {
        station_id: fixture.station_id,
        policy_id: policy.id,
        start_date: "2026-11-01",
        end_date: "2026-11-01",
        application_note: "审计样例申报说明",
      },
    });
    expect(created.status()).toBe(201);
    const claim = await created.json();
    expect(claim.adjusted_amount).toBe("4.20");
    const uploaded = await operator.request.post(
      `/api/v1/subsidy/claims/${claim.id}/attachments`,
      {
        headers: opHeaders,
        multipart: {
          version: "1",
          file: {
            name: "审计样例资料.zip",
            mimeType: "application/zip",
            buffer: subsidyZip,
          },
        },
      },
    );
    expect(uploaded.status()).toBe(201);
    const url = `/api/v1/subsidy/claims/${claim.id}/actions`;
    expect(
      (
        await operator.request.post(url, {
          headers: opHeaders,
          data: { action: "submit", version: 2 },
        })
      ).status(),
    ).toBe(200);
    const adjustment = {
      action: "adjust",
      version: 3,
      comment: "private-business-audit-note",
      delta: "0.03",
    };
    expect(
      (
        await reg.request.post(url, { headers: regHeaders, data: adjustment })
      ).status(),
    ).toBe(200);
    expect(
      (
        await reg.request.post(url, { headers: regHeaders, data: adjustment })
      ).status(),
    ).toBe(409);
    await reg
      .getByRole("button", { name: "系统操作日志", exact: true })
      .click();
    await reg.getByLabel("动作代码", { exact: true }).fill("subsidy.adjust");
    await reg
      .getByLabel("业务对象编号", { exact: true })
      .fill(String(claim.id));
    await expect(
      reg.getByRole("button", { name: "查询日志", exact: true }),
    ).toBeEnabled();
    await reg.getByRole("button", { name: "查询日志", exact: true }).click();
    await expect(reg.getByText("第1页 · 共1条", { exact: true })).toBeVisible();
    await reg.getByText("查看结构化详情", { exact: true }).click();
    const details = reg.locator("pre");
    await expect(details).toContainText('"adjusted_amount": "4.20"');
    await expect(details).toContainText('"adjusted_amount": "4.23"');
    await expect(details).toContainText('"object_type": "subsidy_claim"');
    await expect(details).not.toContainText("private-business-audit-note");
    await reg.screenshot({
      path: "test-results/business-audit-amount.png",
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
        String(fixture.station_id),
      ],
      { cwd: "../server", stdio: "pipe" },
    );
  }
});
