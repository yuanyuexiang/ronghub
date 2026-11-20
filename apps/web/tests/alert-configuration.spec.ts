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
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", {
      name: name === "operator_a" ? "企业告警" : "告警数据",
      exact: true,
    })
    .click();
  await page
    .getByRole("navigation", { name: "告警工作区" })
    .getByRole("button", { name: "规则配置", exact: true })
    .click();
}
test("multi-level rule form previews exact evidence, preserves drafts, copies immutable versions and archives safely", async ({
  browser,
}) => {
  const code = "browser-alertcfg-" + randomBytes(8).toString("hex"),
    contexts = await Promise.all([browser.newContext(), browser.newContext()]);
  try {
    const manager = await contexts[0].newPage(),
      owner = await contexts[1].newPage();
    await login(manager, "regulator");
    const panel = manager.getByRole("region", {
      name: "分级与周期告警配置",
      exact: true,
    });
    await panel
      .getByRole("button", { name: "新增分级规则", exact: true })
      .click();
    const form = panel.getByRole("form", { name: "分级规则表单", exact: true });
    await form.getByLabel("分级规则代码", { exact: true }).fill(code);
    await form.getByLabel("分级规则名称", { exact: true }).fill("演示过流分级");
    await form
      .getByLabel("分级告警类型", { exact: true })
      .selectOption("device_current");
    await form
      .getByLabel("分级general阈值", { exact: true })
      .fill("100.000001");
    await form.getByLabel("分级critical阈值", { exact: true }).fill("120");
    await form.getByLabel("分级恢复阈值", { exact: true }).fill("90");
    await form.getByLabel("分级预览观测值", { exact: true }).fill("1");
    await form.getByLabel("分级预览额定电流", { exact: true }).fill(".999999");
    await form
      .getByRole("button", { name: "预览分级判断", exact: true })
      .click();
    await expect(form.getByRole("status")).toContainText("覆盖未知");
    await form
      .getByText("预览观测覆盖完整（仅演示声明）", { exact: true })
      .check();
    await form
      .getByRole("button", { name: "预览分级判断", exact: true })
      .click();
    await expect(form.getByRole("status")).toContainText("100.000100");
    await manager
      .getByRole("button", { name: "刷新数据", exact: true })
      .click();
    await expect(
      form.getByLabel("分级general阈值", { exact: true }),
    ).toHaveValue("100.000001");
    await manager
      .getByRole("navigation", { name: "告警工作区" })
      .getByRole("button", { name: "事件跟踪", exact: true })
      .click();
    await manager
      .getByRole("navigation", { name: "告警工作区" })
      .getByRole("button", { name: "规则配置", exact: true })
      .click();
    await expect(
      form.getByLabel("分级general阈值", { exact: true }),
    ).toHaveValue("100.000001");
    const save = manager.waitForResponse(
      (r) =>
        r.url().endsWith("/alerts/configured-rules") &&
        r.request().method() === "POST",
    );
    await form
      .getByRole("button", { name: "保存分级规则版本", exact: true })
      .click();
    const saved = await save;
    expect(saved.status(), await saved.text()).toBe(201);
    const first = await saved.json();
    await panel.getByLabel("分级规则名称或代码", { exact: true }).fill(code);
    await panel
      .getByRole("button", { name: "查询分级规则", exact: true })
      .click();
    const row = panel.getByRole("row").filter({ hasText: code });
    await expect(row).toHaveCount(1);
    await row
      .getByRole("button", { name: "复制为新版本", exact: true })
      .click();
    await expect(form.getByLabel("分级规则版本", { exact: true })).toHaveValue(
      "2",
    );
    await form
      .getByLabel("分级告警类型", { exact: true })
      .selectOption("station_offline");
    await form.getByLabel("分级阈值方向", { exact: true }).selectOption("lte");
    await form
      .getByLabel("分级监控周期", { exact: true })
      .selectOption("natural_month");
    await form.getByLabel("分级general阈值", { exact: true }).fill("30");
    await form.getByLabel("分级critical阈值", { exact: true }).fill("10");
    await form.getByLabel("分级恢复阈值", { exact: true }).fill("40");
    await form.getByLabel("分级预览观测值", { exact: true }).fill("9");
    await form
      .getByLabel("分级预览周期开始", { exact: true })
      .fill("2026-10-01");
    await form
      .getByLabel("分级预览周期结束", { exact: true })
      .fill("2026-11-01");
    await form
      .getByRole("button", { name: "预览分级判断", exact: true })
      .click();
    await expect(form.getByRole("status")).toContainText("触发重大");
    const secondSave = manager.waitForResponse(
      (r) =>
        r.url().endsWith("/alerts/configured-rules") &&
        r.request().method() === "POST",
    );
    await form
      .getByRole("button", { name: "保存分级规则版本", exact: true })
      .click();
    expect((await secondSave).status()).toBe(201);
    await expect(row).toHaveCount(2);
    const firstRow = row.filter({ hasText: "/ V1" });
    await firstRow
      .getByRole("button", { name: "停用分级规则", exact: true })
      .click();
    await expect(
      firstRow.getByRole("button", { name: "启用分级规则", exact: true }),
    ).toBeEnabled();
    manager.once("dialog", (dialog) => dialog.accept());
    await firstRow
      .getByRole("button", { name: "归档分级规则", exact: true })
      .click();
    await expect(row).toHaveCount(1);
    await panel.getByText("包含已归档", { exact: true }).check();
    await expect(row).toHaveCount(2);
    const result = await (
      await manager.request.get(
        `/api/v1/alerts/configured-rules?q=${code}&include_archived=true`,
      )
    ).json();
    const frozen = result.items.find(
      (item: { id: number }) => item.id === first.id,
    );
    expect(frozen.configuration).toEqual(first.configuration);
    expect(frozen.archived).toBe(true);
    await panel.screenshot({
      path: "../../.backups/alert-configuration-browser.png",
    });
    await login(owner, "operator_a");
    const readOnly = owner.getByRole("region", {
      name: "分级与周期告警配置",
      exact: true,
    });
    await expect(
      readOnly.getByRole("button", { name: "新增分级规则", exact: true }),
    ).toHaveCount(0);
    await readOnly.getByLabel("分级规则名称或代码", { exact: true }).fill(code);
    await readOnly
      .getByRole("button", { name: "查询分级规则", exact: true })
      .click();
    await expect(
      readOnly.getByRole("row").filter({ hasText: code }),
    ).toHaveCount(1);
  } finally {
    await Promise.allSettled(contexts.map((context) => context.close()));
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.alert_configuration_fixture",
        "--code",
        code,
      ],
      { cwd: "../server" },
    );
  }
});
