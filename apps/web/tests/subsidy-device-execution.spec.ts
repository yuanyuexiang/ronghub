import { returnToBusinessList } from "./business-workspace";
import { test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { randomBytes } from "node:crypto";
import { pickStation } from "./station";
import { subsidyZip } from "./subsidy-fixture";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
function fixture(module: string, args: string[]) {
  return execFileSync("uv", ["run", "python", "-m", module, ...args], {
    cwd: "../server",
    encoding: "utf8",
  });
}
async function login(page: Page, name: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(name);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
}
test("device claim freezes historical evidence through materials review and disbursement registration", async ({
  browser,
}) => {
  const station = JSON.parse(
    fixture("app.browser_fixture", ["create", "--operating"]),
  );
  const code = "browser-policy-" + randomBytes(8).toString("hex");
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
  ]);
  try {
    fixture("app.device_execution_fixture", [
      "--station",
      String(station.station_id),
    ]);
    const reg = await contexts[0].newPage(),
      owner = await contexts[1].newPage();
    await login(reg, "regulator");
    await reg
      .getByRole("button", { name: "补贴申请记录", exact: true })
      .click();
    await reg
      .getByRole("button", { name: "补贴规则配置", exact: true })
      .click();
    await reg
      .getByRole("button", { name: "新增政策版本", exact: true })
      .click();
    await reg
      .getByLabel("补贴规则口径", { exact: true })
      .selectOption("execute");
    for (const [label, value] of [
      ["政策代码", code],
      ["政策名称", code],
      ["版本号", "1"],
      ["充电补贴标准（元/度）", "0.100001"],
      ["换电补贴标准（元/度）", "0.200001"],
      ["生效日期", "2026-01-01"],
      ["结束日期", "2026-12-31"],
      ["故障率门槛（%）", "10"],
      ["离线率门槛（%）", "20"],
    ])
      await reg.getByLabel(label, { exact: true }).fill(value);
    for (let i = 0; i <= 5; i++)
      await reg
        .getByLabel(i === 0 ? "无星系数" : `${i}星系数`, { exact: true })
        .fill("1.200001");
    await reg.getByLabel("确认设备日演示核算口径", { exact: true }).check();
    const saving = reg.waitForResponse(
      (r) =>
        r.url().endsWith("/subsidy/policies") &&
        r.request().method() === "POST",
    );
    await reg
      .getByRole("button", { name: "保存不可变政策版本", exact: true })
      .click();
    const saved = await saving;
    expect(saved.status(), await saved.text()).toBe(201);
    const policy = await saved.json();
    expect(policy.claim_supported).toBe(true);
    await login(owner, "operator_a");
    await owner.getByRole("button", { name: "补贴申报", exact: true }).click();
    await pickStation(owner, "补贴场站", station);
    await owner
      .getByLabel("补贴政策", { exact: true })
      .selectOption(String(policy.id));
    await owner.getByLabel("补贴开始日期").fill("2026-11-01");
    await owner.getByLabel("补贴结束日期").fill("2026-11-02");
    await owner
      .getByLabel("补贴申请说明", { exact: true })
      .fill("设备日演示核算及合成历史依据验证");
    const created = owner.waitForResponse(
      (r) =>
        r.url().endsWith("/subsidy/claims") && r.request().method() === "POST",
    );
    await owner
      .getByRole("button", { name: "核算并创建草稿", exact: true })
      .click();
    const response = await created;
    expect(response.status(), await response.text()).toBe(201);
    const claim = await response.json();
    const original = await (
      await owner.request.get(`/api/v1/subsidy/claims/${claim.id}`)
    ).json();
    expect(claim.base_amount).toBe("4.21");
    expect(claim.adjusted_amount).toBe("5.05");
    const evidence = owner.getByRole("region", {
      name: "冻结设备核算依据",
      exact: true,
    });
    await expect(evidence).toContainText("2026-11-02");
    await expect(evidence).toContainText("42.125");
    await expect(evidence.getByRole("row")).toHaveCount(3);
    await owner.getByLabel("上传补贴ZIP材料", { exact: true }).setInputFiles({
      name: "设备材料.zip",
      mimeType: "application/zip",
      buffer: subsidyZip,
    });
    await expect(
      owner.getByRole("button", { name: "提交补贴审核", exact: true }),
    ).toBeEnabled();
    const submitted = owner.waitForResponse((r) =>
      r.url().endsWith(`/claims/${claim.id}/actions`),
    );
    await owner
      .getByRole("button", { name: "提交补贴审核", exact: true })
      .click();
    expect((await submitted).status()).toBe(200);
    await reg
      .getByRole("button", { name: "补贴申请记录", exact: true })
      .click();
    await returnToBusinessList(reg, "补贴");
    await reg.getByLabel("补贴检索编号").fill(String(claim.id));
    await reg
      .getByRole("button", { name: "查询补贴记录", exact: true })
      .click();
    await reg
      .getByRole("row")
      .filter({
        has: reg.getByRole("cell", { name: `SUB-${claim.id}`, exact: true }),
      })
      .getByRole("button", { name: "补贴详情", exact: true })
      .click();
    for (const button of ["通过补贴审核", "登记发放状态（不付款）"]) {
      await reg
        .getByLabel("补贴处理意见")
        .fill("核对冻结设备及历史公示依据，仅登记状态");
      const changed = reg.waitForResponse((r) =>
        r.url().endsWith(`/claims/${claim.id}/actions`),
      );
      await reg.getByRole("button", { name: button, exact: true }).click();
      expect((await changed).status()).toBe(200);
    }
    const final = await (
      await reg.request.get(`/api/v1/subsidy/claims/${claim.id}`)
    ).json();
    expect(final.status).toBe("disbursed");
    expect(final.snapshot.device_calculation).toEqual(
      original.snapshot.device_calculation,
    );
    await returnToBusinessList(reg, "补贴");
    await reg.getByRole("button", { name: "补贴统计", exact: true }).click();
    const statistics = reg.getByRole("region", {
      name: "补贴统计",
      exact: true,
    });
    await pickStation(reg, "补贴统计场站", station);
    await statistics
      .getByLabel("补贴统计开始日期", { exact: true })
      .fill("2026-11-01");
    await statistics
      .getByLabel("补贴统计结束日期", { exact: true })
      .fill("2026-11-02");
    for (const dimension of [
      "operator",
      "station",
      "equipment",
      "device_day",
    ]) {
      await statistics
        .getByLabel("补贴统计维度", { exact: true })
        .selectOption(dimension);
      await statistics
        .getByRole("button", { name: "查询补贴统计", exact: true })
        .click();
      await expect(statistics.getByRole("table")).toContainText("4.21");
      await expect(statistics).toContainText("匹配申请 1 份");
      await expect(statistics.getByRole("row")).toHaveCount(
        dimension === "device_day" ? 3 : 2,
      );
    }
    await statistics
      .getByLabel("补贴统计维度", { exact: true })
      .selectOption("operator");
    await reg.getByRole("button", { name: "刷新数据", exact: true }).click();
    await expect(statistics).toContainText("设备日明细");
    const downloading = reg.waitForEvent("download");
    await statistics
      .getByRole("button", { name: "导出补贴统计CSV", exact: true })
      .click();
    const download = await downloading;
    const stream = await download.createReadStream();
    const chunks: Buffer[] = [];
    for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
    const csv = Buffer.concat(chunks).toString("utf8");
    expect(csv).toContain("device_day");
    expect(csv).toContain("browser-device");
    await statistics.screenshot({
      path: "../../.backups/subsidy-statistics-browser.png",
    });
    await reg.screenshot({
      path: "../../.backups/device-execution-browser.png",
      fullPage: true,
    });
  } finally {
    await Promise.allSettled(contexts.map((c) => c.close()));
    fixture("app.browser_fixture", [
      "clean",
      "--station",
      String(station.station_id),
    ]);
    fixture("app.subsidy_policy_fixture", ["--code", code]);
  }
});
