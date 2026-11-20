import { test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, name: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(name);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "运营总览", exact: true }),
  ).toBeVisible();
}

test("complete device policy versions persist precise rules and remain outside legacy claim selection", async ({
  browser,
}) => {
  const code = "browser-policy-" + randomBytes(8).toString("hex");
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
  ]);
  try {
    const manager = await contexts[0].newPage(),
      owner = await contexts[1].newPage();
    await login(manager, "regulator");
    await manager
      .getByRole("button", { name: "补贴申请记录", exact: true })
      .click();
    await manager
      .getByRole("button", { name: "补贴规则配置", exact: true })
      .click();
    for (const version of [1, 2]) {
      await manager
        .getByRole("button", { name: "新增政策版本", exact: true })
        .click();
      await expect(
        manager.getByLabel("补贴规则口径", { exact: true }),
      ).toHaveValue("device");
      for (const [label, value] of [
        ["政策代码", code],
        ["政策名称", code],
        ["版本号", String(version)],
        ["充电补贴标准（元/度）", "0.100001"],
        ["生效日期", "2026-01-01"],
        ["结束日期", "2026-12-31"],
        ["换电补贴标准（元/度）", "0.200001"],
        ["故障率门槛（%）", "10.2500"],
        ["离线率门槛（%）", "20.0000"],
      ])
        await manager.getByLabel(label, { exact: true }).fill(value);
      for (let star = 0; star <= 5; star++)
        await manager
          .getByLabel(star === 0 ? "无星系数" : `${star}星系数`, {
            exact: true,
          })
          .fill(
            star === 5 ? (version === 1 ? "1.200001" : "1.500001") : "1.000000",
          );
      await manager
        .getByLabel("补贴生成周期", { exact: true })
        .selectOption(version === 1 ? "day" : "month");
      if (version === 1) {
        const reread = manager.waitForResponse(
          (response) =>
            new URL(response.url()).pathname === "/api/v1/subsidy/policies" &&
            response.request().method() === "GET",
        );
        await manager
          .getByRole("button", { name: "刷新数据", exact: true })
          .click();
        expect((await reread).status()).toBe(200);
        await expect(
          manager.getByLabel("5星系数", { exact: true }),
        ).toHaveValue("1.200001");
        await expect(
          manager.getByLabel("故障率门槛（%）", { exact: true }),
        ).toHaveValue("10.2500");
      }
      const saved = manager.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === "/api/v1/subsidy/policies" &&
          response.request().method() === "POST",
      );
      await manager
        .getByRole("button", { name: "保存不可变政策版本", exact: true })
        .click();
      const response = await saved;
      expect(response.status()).toBe(201);
      const data = await response.json();
      expect(data.claim_supported).toBe(false);
      expect(data.device_rules.star_coefficients["5"]).toBe(
        version === 1 ? "1.200001" : "1.500001",
      );
      expect(data.device_rules.swap_rate).toBe("0.200001");
      await manager
        .getByRole("button", {
          name: `查看设备规则 ${code} V${version}`,
          exact: true,
        })
        .click();
      await expect(
        manager.getByRole("row").filter({
          has: manager.getByRole("cell", {
            name: `${code} / V${version}`,
            exact: true,
          }),
        }),
      ).toContainText("设备规则 · 暂不可申报");
    }
    await manager.reload();
    const policies = (
      await (await manager.request.get("/api/v1/subsidy/policies")).json()
    ).items.filter((row: { code: string }) => row.code === code);
    expect(policies).toHaveLength(2);
    expect(
      policies.find((row: { version: number }) => row.version === 1)
        .device_rules.star_coefficients["5"],
    ).toBe("1.200001");
    await manager
      .getByRole("button", { name: "补贴申请记录", exact: true })
      .click();
    await manager
      .getByRole("button", { name: "补贴规则配置", exact: true })
      .click();
    await manager
      .getByRole("button", { name: `查看设备规则 ${code} V2`, exact: true })
      .click();
    await expect(
      manager.getByRole("region", { name: "补贴规则详情", exact: true }),
    ).toContainText("1.500001");
    expect(
      await manager
        .locator(".subsidy-policy-table")
        .evaluate(
          (table) =>
            table.getBoundingClientRect().width <=
            table.parentElement!.clientWidth + 1,
        ),
    ).toBe(true);
    await manager.screenshot({
      path: "../../.backups/subsidy-device-rules.png",
      fullPage: true,
    });
    await login(owner, "operator_a");
    await owner.getByRole("button", { name: "补贴申报", exact: true }).click();
    await expect(
      owner
        .getByLabel("补贴政策", { exact: true })
        .locator("option")
        .filter({ hasText: code }),
    ).toHaveCount(0);
    await owner
      .getByRole("button", { name: "年度多站申报", exact: true })
      .click();
    await owner.getByLabel("补贴年度", { exact: true }).fill("2026");
    await expect(
      owner
        .getByLabel("年度政策版本", { exact: true })
        .locator("option")
        .filter({ hasText: code }),
    ).toHaveCount(0);
  } finally {
    await Promise.allSettled(contexts.map((context) => context.close()));
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.subsidy_policy_fixture", "--code", code],
      { cwd: "../server", stdio: "pipe" },
    );
  }
});
