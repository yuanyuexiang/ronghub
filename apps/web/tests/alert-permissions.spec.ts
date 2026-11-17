import { test, expect, Page } from "./fixtures";
import { pickStation } from "./station";
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
  await page.getByRole("button", { name: "告警中心", exact: true }).click();
}
test("alert confirmation staff cannot configure rules or inject observations", async ({
  browser,
}) => {
  const station = fixture("app.browser_fixture", ["create"]);
  const staff = ["samples", "acknowledge"].map((kind) =>
    fixture("app.permission_fixture", [
      "create",
      "--module",
      "alerts",
      "--kind",
      kind,
    ]),
  );
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
  ]);
  try {
    const [observer, confirmer] = await Promise.all(
      contexts.map((c) => c.newPage()),
    );
    await login(observer, staff[0].username);
    await login(confirmer, staff[1].username);
    for (const page of [observer, confirmer]) {
      await page
        .getByRole("navigation", { name: "告警工作区" })
        .getByRole("button", { name: "规则配置", exact: true })
        .click();
      await expect(
        page.getByRole("button", { name: "新增规则版本", exact: true }),
      ).toHaveCount(0);
    }
    await expect(
      confirmer.getByRole("button", { name: "演示样本验证", exact: true }),
    ).toHaveCount(0);
    await observer
      .getByRole("button", { name: "演示样本验证", exact: true })
      .click();
    await pickStation(observer, "告警演示场站", station);
    await observer
      .getByLabel("告警监测对象")
      .fill("independent-target-" + station.station_id);
    await confirmer
      .getByRole("navigation", { name: "告警工作区" })
      .getByRole("button", { name: "事件跟踪", exact: true })
      .click();
    async function sample(value: string) {
      await observer
        .getByRole("navigation", { name: "告警工作区" })
        .getByRole("button", { name: "规则配置", exact: true })
        .click();
      await observer.getByLabel("告警样本值").fill(value);
      const response = observer.waitForResponse(
        (r) =>
          r.url().endsWith("/alerts/samples") &&
          r.request().method() === "POST",
      );
      await observer
        .getByRole("button", { name: "提交演示样本", exact: true })
        .click();
      const done = await response;
      expect(done.status()).toBe(200);
      await observer
        .getByRole("navigation", { name: "告警工作区" })
        .getByRole("button", { name: "事件跟踪", exact: true })
        .click();
      return done.json();
    }
    const opened = await sample("40");
    expect(opened.outcome).toBe("opened");
    const row = (page: Page) =>
      page.getByRole("row").filter({
        has: page.getByRole("cell", {
          name: `ALERT-${opened.event_id}`,
          exact: true,
        }),
      });
    await row(observer)
      .getByRole("button", { name: "告警详情", exact: true })
      .click();
    await expect(
      observer.getByLabel("告警确认依据", { exact: true }),
    ).toHaveCount(0);
    await expect(
      observer.getByRole("button", { name: "确认已查看告警", exact: true }),
    ).toHaveCount(0);
    await confirmer
      .getByRole("button", { name: "刷新告警", exact: true })
      .click();
    await row(confirmer)
      .getByRole("button", { name: "告警详情", exact: true })
      .click();
    await confirmer
      .getByLabel("告警确认依据", { exact: true })
      .fill("已人工核查，设备尚未恢复");
    await confirmer
      .getByRole("button", { name: "确认已查看告警", exact: true })
      .click();
    await expect(row(confirmer)).toContainText("已人工确认");
    await expect(row(confirmer)).toContainText("持续异常");
    await expect(
      confirmer.getByLabel("告警确认依据", { exact: true }),
    ).toHaveCount(0);
    await confirmer.screenshot({
      path: "test-results/alert-acknowledge-only.png",
      fullPage: true,
    });
    expect((await sample("0")).outcome).toBe("recovered");
    await confirmer
      .getByRole("button", { name: "刷新告警", exact: true })
      .click();
    await expect(row(confirmer)).toContainText("已恢复");
    await expect(row(confirmer)).toContainText("已人工确认");
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
    for (const user of staff)
      execFileSync(
        "uv",
        [
          "run",
          "python",
          "-m",
          "app.permission_fixture",
          "clean",
          "--user",
          String(user.id),
        ],
        { cwd: "../server", stdio: "pipe" },
      );
  }
});
