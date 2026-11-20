import { test, expect, openWorkspaceNavigation, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
function fixture(...extra: string[]) {
  const result = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.browser_fixture", "create", ...extra],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  return (result.items?.[0] ?? result) as { station_id: number; name: string };
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
async function detail(page: Page, name: string) {
  await openWorkspaceNavigation(page);
  await page
    .getByRole("button", {
      name: /^(场站台账|场站信息查询|场站数据同步与场站信息查询)$/,
      exact: true,
    })
    .click();
  await page.getByLabel("搜索场站", { exact: true }).fill(name);
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await page
    .getByRole("button", { name: `查看详情 ${name}`, exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(
    dialog.getByRole("heading", { name: "供电配套", exact: true }),
  ).toBeVisible();
  return dialog;
}
test("station profile preserves source unit, false and zero with scoped private contacts and unknown legacy fields", async ({
  browser,
}) => {
  const known = fixture("--located", "--facilities"),
    unknown = fixture();
  const contexts = await Promise.all(
    [0, 1, 2].map(() =>
      browser.newContext({
        timezoneId: "America/New_York",
        viewport: { width: 1440, height: 1400 },
      }),
    ),
  );
  try {
    const [owner, regulator, association] = await Promise.all(
      contexts.map((c) => c.newPage()),
    );
    await login(owner, "operator_a");
    const dialog = await detail(owner, known.name);
    await expect(dialog.getByLabel("场站供电配套")).toContainText("转供电");
    await expect(dialog.getByLabel("场站供电配套")).toContainText(
      "160.125 （来源未注明单位）",
    );
    await expect(
      dialog
        .getByLabel("场站供电配套")
        .locator("div")
        .filter({ has: owner.locator("dt", { hasText: "是否独立报装" }) }),
    ).toContainText("否");
    await expect(dialog.getByLabel("场站运营与停车")).toContainText("0 个");
    await expect(dialog.getByLabel("场站运营与停车")).toContainText(
      "2024-02-29",
    );
    await expect(dialog.getByLabel("场站联系方式")).toContainText(
      "DEMO-NOT-A-REAL-NUMBER",
    );
    await dialog.getByRole("button", { name: "设备信息", exact: true }).click();
    await expect(dialog.getByLabel("场站设备明细")).toContainText(
      "地图演示设备",
    );
    await dialog.screenshot({
      path: "test-results/station-facilities-desktop.png",
    });
    await dialog.getByRole("button", { name: "基本信息", exact: true }).click();
    await owner.setViewportSize({ width: 390, height: 900 });
    await dialog.getByLabel("场站供电配套").scrollIntoViewIfNeeded();
    await expect(dialog.getByLabel("场站供电配套")).toBeVisible();
    expect(
      await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
    ).toBe(true);
    await expect(
      dialog.getByRole("button", { name: "关闭详情", exact: true }),
    ).toBeInViewport();
    await dialog.screenshot({
      path: "test-results/station-facilities-narrow.png",
    });
    await owner.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    const empty = await detail(owner, unknown.name);
    await expect(empty.getByLabel("场站供电配套")).toContainText("来源未提供");
    await expect(empty.getByLabel("场站运营与停车")).not.toContainText("0 个");
    await expect(empty.getByLabel("场站供电配套")).not.toContainText("kVA");
    const unknownResponse = await owner.request.get(
      `/api/v1/stations/${unknown.station_id}`,
    );
    const unknownData = await unknownResponse.json();
    expect(unknownData.facilities.parking_spaces).toBeNull();
    expect(unknownData.facilities.independent_connection).toBeNull();
    expect(unknownData.facilities.commissioned_on).toBeNull();
    await owner.keyboard.press("Escape");
    await login(regulator, "regulator");
    const reg = await detail(regulator, known.name);
    await expect(reg.getByLabel("场站联系方式")).toContainText(
      "演示场站联系人",
    );
    await regulator.keyboard.press("Escape");
    await login(association, "association");
    const assoc = await detail(association, known.name);
    await expect(assoc.getByLabel("场站联系方式")).toContainText(
      "当前岗位未授权",
    );
    await expect(assoc).not.toContainText("DEMO-NOT-A-REAL-NUMBER");
    const restricted = await association.request.get(
      `/api/v1/stations/${known.station_id}`,
    );
    const restrictedData = await restricted.json();
    expect(restrictedData.contact_accessible).toBe(false);
    expect(restrictedData.contact).toBeNull();
  } finally {
    await Promise.all(contexts.map((c) => c.close()));
    for (const item of [known, unknown])
      execFileSync(
        "uv",
        [
          "run",
          "python",
          "-m",
          "app.browser_fixture",
          "clean",
          "--station",
          String(item.station_id),
        ],
        { cwd: "../server", stdio: "pipe" },
      );
  }
});
