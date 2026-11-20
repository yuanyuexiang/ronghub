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
  const response = page.waitForResponse(
    (r) => r.url().endsWith("/auth/login") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "登录平台" }).click();
  expect((await response).status()).toBe(200);
  await expect(page.getByRole("heading", { name: "运营总览" })).toBeVisible();
}
test("three roles use scoped map, clustering, filters and unlocated station details", async ({
  browser,
}) => {
  const located = JSON.parse(
    execFileSync(
      "uv",
      [
        "run",
        "python",
        "-m",
        "app.browser_fixture",
        "create",
        "--located",
        "--count",
        "2",
      ],
      { cwd: "../server", encoding: "utf8" },
    ),
  ).items;
  const missing = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.browser_fixture", "create"],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
    browser.newContext(),
    browser.newContext(),
  ]);
  try {
    const [op, other, assoc, reg] = await Promise.all(
      contexts.map((c) => c.newPage()),
    );
    await login(op, "operator_a");
    await login(other, "operator_b");
    await login(assoc, "association");
    await login(reg, "regulator");
    await op.getByRole("button", { name: "场站地图", exact: true }).click();
    await expect(
      op.getByRole("region", { name: "场站交互地图" }),
    ).toBeVisible();
    await expect(
      op.getByRole("button", { name: /聚合\d+个场站，点击放大/ }).first(),
    ).toBeVisible();
    await op
      .getByRole("button", { name: /聚合\d+个场站，点击放大/ })
      .first()
      .click();
    await op.getByLabel("地图场站名称").fill(located[0].name);
    await op.getByRole("button", { name: "筛选地图", exact: true }).click();
    const station = op.getByRole("button", {
      name: `查看地图场站：${located[0].name}`,
      exact: true,
    });
    await expect(station).toBeVisible();
    await station.click();
    await expect(
      op.getByRole("heading", {
        name: `${located[0].name} · 场站摘要`,
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      op.getByRole("heading", { name: "地图演示设备 60.00 kW", exact: true }),
    ).toBeVisible();
    await expect(op.getByText(/已观察 1 \/ 台账 1 个接口/)).toBeVisible();
    await op.getByLabel("地图运行快照").selectOption("available");
    await op
      .getByText("更多筛选（用途、场所、功率、星级）", { exact: true })
      .click();
    await op.getByLabel("地图用途").selectOption("public");
    await op.getByLabel("地图建设场所").selectOption("building_parking");
    await op.getByLabel("地图站点运营状态").selectOption("normal");
    await op.getByLabel("地图来源同步状态").selectOption("active");
    await op.getByRole("button", { name: "筛选地图", exact: true }).click();
    await expect(station).toBeVisible();
    await op.getByLabel("地图最大功率").fill("10");
    await op.getByRole("button", { name: "筛选地图", exact: true }).click();
    await expect(station).toHaveCount(0);
    await op.getByRole("button", { name: "重置筛选", exact: true }).click();
    await op.getByLabel("地图场站名称").fill(missing.name);
    await op.getByRole("button", { name: "筛选地图", exact: true }).click();
    await op
      .getByRole("button", { name: "待定位 / 待适配 1", exact: true })
      .click();
    const unknown = op.getByRole("button", {
      name: `查看地图场站：${missing.name}`,
      exact: true,
    });
    await expect(unknown).toContainText("来源未提供坐标");
    await unknown.click();
    await expect(
      op.getByText("尚无设备快照，不能据此推断设备离线。", { exact: true }),
    ).toBeVisible();
    await op.screenshot({
      path: "test-results/station-map.png",
      fullPage: true,
    });
    await other.getByRole("button", { name: "场站地图", exact: true }).click();
    await other.getByLabel("地图场站名称").fill(located[0].name);
    await other.getByRole("button", { name: "筛选地图", exact: true }).click();
    await expect(
      other.getByRole("button", {
        name: `查看地图场站：${located[0].name}`,
        exact: true,
      }),
    ).toHaveCount(0);
    await assoc.getByRole("button", { name: "场站地图", exact: true }).click();
    await assoc.getByLabel("地图场站名称").fill(located[0].name);
    await assoc.getByRole("button", { name: "筛选地图", exact: true }).click();
    await expect(
      assoc.getByRole("button", {
        name: `查看地图场站：${located[0].name}`,
        exact: true,
      }),
    ).toBeVisible();
    await reg
      .getByRole("button", { name: "可视化一张图", exact: true })
      .click();
    await reg.getByLabel("地图场站名称").fill(located[0].name);
    await reg.getByRole("button", { name: "筛选地图", exact: true }).click();
    await expect(
      reg.getByRole("button", {
        name: `查看地图场站：${located[0].name}`,
        exact: true,
      }),
    ).toBeVisible();
    await reg.setViewportSize({ width: 800, height: 1000 });
    await expect(
      reg.getByRole("region", { name: "场站交互地图" }),
    ).toBeVisible();
    expect(
      await reg.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await reg.screenshot({
      path: "test-results/regulator-map-tablet.png",
      fullPage: true,
    });
    await assoc.screenshot({
      path: "test-results/association-map.png",
      fullPage: true,
    });
  } finally {
    await Promise.allSettled(contexts.map((c) => c.close()));
    for (const fixture of [...located, missing])
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
