import { test, expect, type Page } from "./fixtures";
async function noOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
}
test("portal puts the applied query in the bulletin masthead and keeps identity selection separate", async ({
  page,
}) => {
  const first = page.waitForResponse(
    (r) =>
      r.url().includes("/public/articles?") &&
      new URL(r.url()).searchParams.get("page_size") === "10",
  );
  await page.goto("/portal?page=999999");
  await expect(page).toHaveURL(/\/portal$/);
  expect((await first).status()).toBe(200);
  const hero = page.getByRole("region", { name: "资讯公告查询", exact: true });
  await expect(
    hero.getByRole("search", { name: "公开资讯查询" }),
  ).toBeVisible();
  await expect(page.getByLabel("三端工作台入口", { exact: true })).toHaveCount(
    0,
  );
  await expect(page.getByLabel("公开资讯每页条数")).toHaveValue("10");
  await hero.getByLabel("公开资讯关键词").fill("未应用的门户检索");
  const sized = page.waitForResponse(
    (r) =>
      r.url().includes("/public/articles?") &&
      new URL(r.url()).searchParams.get("page_size") === "20",
  );
  await page.getByLabel("公开资讯每页条数").selectOption("20");
  expect((await sized).status()).toBe(200);
  expect(new URL(page.url()).searchParams.has("q")).toBe(false);
  await expect(hero.getByLabel("公开资讯关键词")).toHaveValue(
    "未应用的门户检索",
  );
  await hero.getByRole("button", { name: "查询资讯", exact: true }).click();
  await expect(page).toHaveURL(/q=/);
  await expect(
    hero.getByRole("button", { name: "查询资讯", exact: true }),
  ).toBeEnabled();
  expect(new URL(page.url()).searchParams.get("page_size")).toBe("20");
  await page.screenshot({
    path: "../../.backups/portal-corrected-home-1440.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 900 });
  await noOverflow(page);
  await page.screenshot({
    path: "../../.backups/portal-corrected-home-390.png",
    fullPage: true,
  });
  await page
    .getByRole("link", { name: "站点公示 · 登录查看", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText("站点公示需登录后查看");
  const cards = page.locator(".portal-login-choices > div > a");
  expect(
    await cards.evaluateAll((nodes) =>
      nodes.map((n) =>
        new URL((n as HTMLAnchorElement).href).searchParams.get("entry"),
      ),
    ),
  ).toEqual(["regulator", "operator", "association"]);
  await noOverflow(page);
  await page.screenshot({
    path: "../../.backups/portal-corrected-entry-390.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({
    path: "../../.backups/portal-corrected-entry-1440.png",
    fullPage: true,
  });
});
test("all three identity choices retain login context and return navigation without granting identity", async ({
  page,
}) => {
  for (const name of ["监管", "运营商", "协会"]) {
    await page.goto("/portal/login");
    await page
      .getByRole("link", { name: `进入${name}端登录`, exact: false })
      .click();
    await expect(
      page.getByRole("heading", { name: "登录工作台", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(`${name}端登录 · 登录后按账号实际授权进入工作台`, {
        exact: true,
      }),
    ).toBeVisible();
    await page.setViewportSize({ width: 390, height: 900 });
    await noOverflow(page);
    await page.setViewportSize({ width: 1440, height: 1000 });
    expect((await page.request.get("/api/v1/auth/me")).status()).toBe(401);
    await page
      .getByRole("navigation", { name: "登录返回导航" })
      .getByRole("link", { name: "返回门户", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "充电资讯与公告", exact: true }),
    ).toBeVisible();
  }
  await page.goto("/portal?page_size=11");
  await expect(
    page.getByRole("heading", { name: "页面参数无效", exact: true }),
  ).toBeVisible();
});
