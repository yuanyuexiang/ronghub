import { navigateWorkspace, test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("regulator");
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "退出登录", exact: true }),
  ).toBeVisible();
  await navigateWorkspace(page, /^(公告与通知|新增发布资讯)$/);
}
async function draft(page: Page, title: string, publicly: boolean) {
  const back = page.getByRole("button", { name: "关闭内容详情", exact: true });
  if (await back.isVisible()) await back.click();
  await page.getByRole("button", { name: "新建内容草稿", exact: true }).click();
  const consent = page.getByLabel("向匿名门户公开", { exact: true });
  await expect(consent).not.toBeChecked();
  await page.getByLabel("内容标题", { exact: true }).fill(title);
  await page
    .getByLabel("内容正文", { exact: true })
    .fill(
      publicly
        ? "已获许可的公开资讯；<script>window.portalUnsafe=true</script>。演示内容，不是正式政策。"
        : "内部业务资料，不应出现在匿名门户",
    );
  if (publicly) await consent.check();
  const created = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/content") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "保存内容草稿", exact: true }).click();
  const response = await created;
  expect(response.status()).toBe(201);
  const article = await response.json();
  expect(article.portal_public).toBe(publicly);
  if (publicly)
    await expect(page.locator(".application-detail")).toContainText(
      "已选择门户公开",
    );
  return article;
}
async function publish(page: Page) {
  await expect(
    page.getByRole("button", { name: "发布内容", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "发布内容", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "撤下内容", exact: true }),
  ).toBeVisible();
}

test("anonymous portal requires explicit public consent, serves frozen images and files and revokes withdrawn content", async ({
  browser,
}) => {
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.browser_fixture", "create"],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
  ]);
  try {
    const [reg, anonymous] = await Promise.all(
      contexts.map((context) => context.newPage()),
    );
    await login(reg);
    const privateArticle = await draft(reg, fixture.name, false);
    await publish(reg);
    expect((await anonymous.request.get("/api/v1/auth/me")).status()).toBe(401);
    await anonymous.goto("/portal?q=" + encodeURIComponent(fixture.name));
    await expect(
      anonymous.getByRole("heading", { name: "暂无公开资讯", exact: true }),
    ).toBeVisible();
    await anonymous.goto(`/portal?article=${privateArticle.id}`);
    await expect(
      anonymous
        .getByRole("region", { name: "公开资讯详情", exact: true })
        .getByRole("alert"),
    ).toContainText("资讯未公开或已撤下");
    await expect(
      anonymous.getByText("内部业务资料，不应出现在匿名门户", { exact: true }),
    ).toHaveCount(0);
    const publicArticle = await draft(reg, fixture.name, true);
    await reg
      .getByRole("button", { name: "编辑草稿正文", exact: true })
      .click();
    const png = Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jfFoAAAAASUVORK5CYII=",
      "base64",
    );
    await reg.getByLabel("上传内容附件", { exact: true }).setInputFiles({
      name: "公开图示.png",
      mimeType: "image/png",
      buffer: png,
    });
    await reg
      .getByRole("button", { name: "插入正文图片 公开图示.png", exact: true })
      .click();
    await reg.getByLabel("上传内容附件", { exact: true }).setInputFiles({
      name: "公开原件.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4\nExplicitly approved public test fixture"),
    });
    await expect(
      reg
        .locator(".article-assets")
        .filter({ has: reg.getByLabel("上传内容附件", { exact: true }) }),
    ).toContainText("公开原件.pdf");
    await reg
      .getByRole("button", { name: "保存内容草稿", exact: true })
      .click();
    await publish(reg);
    await anonymous.goto("/portal?q=" + encodeURIComponent(fixture.name));
    await expect(
      anonymous.getByText("公开资讯第1页 · 共1条", { exact: true }),
    ).toBeVisible();
    await expect(
      anonymous.getByText("内部业务资料，不应出现在匿名门户", { exact: true }),
    ).toHaveCount(0);
    await anonymous.screenshot({
      path: "test-results/public-portal-desktop.png",
      fullPage: true,
    });
    await anonymous
      .getByLabel("公开资讯关键词", { exact: true })
      .fill("未应用的公开资讯关键词");
    await anonymous
      .getByRole("link", { name: "阅读全文", exact: true })
      .click();
    const detail = anonymous.getByRole("region", {
      name: "公开资讯详情",
      exact: true,
    });
    await expect(detail).toContainText("已获许可的公开资讯");
    await expect(detail.locator("script")).toHaveCount(0);
    expect(
      await anonymous.evaluate(() => Object.hasOwn(window, "portalUnsafe")),
    ).toBe(false);
    const image = detail.locator("img");
    await expect(image).toHaveJSProperty("complete", true);
    const source = (await image.getAttribute("src"))!;
    expect(source).toContain("/api/v1/public/articles/");
    const imageResponse = await anonymous.request.get(source);
    expect(imageResponse.status()).toBe(200);
    expect(imageResponse.headers()["content-type"]).toBe("image/png");
    const file = detail.getByRole("link", { name: /公开原件.pdf/ });
    const downloadPath = (await file.getAttribute("href"))!;
    const downloading = anonymous.waitForEvent("download");
    await file.click();
    const downloaded = await downloading;
    expect(downloaded.suggestedFilename()).toBe("公开原件.pdf");
    expect(readFileSync((await downloaded.path())!, "utf8")).toBe(
      "%PDF-1.4\nExplicitly approved public test fixture",
    );
    await anonymous.setViewportSize({ width: 390, height: 844 });
    expect(
      await anonymous.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await anonymous.screenshot({
      path: "test-results/public-article-390.png",
      fullPage: true,
    });
    await anonymous
      .getByRole("link", { name: "返回资讯列表", exact: true })
      .click();
    await expect(
      anonymous.getByLabel("公开资讯关键词", { exact: true }),
    ).toHaveValue("未应用的公开资讯关键词");
    await anonymous.screenshot({
      path: "test-results/public-portal-390.png",
      fullPage: true,
    });
    await anonymous
      .getByRole("link", { name: "管理员登录", exact: true })
      .click();
    await expect(
      anonymous.getByRole("heading", { name: "选择您的业务入口", exact: true }),
    ).toBeVisible();
    await anonymous.getByRole("link", { name: /进入协会端登录/ }).click();
    await expect(
      anonymous.getByRole("heading", { name: "登录工作台", exact: true }),
    ).toBeVisible();
    await anonymous
      .getByRole("link", { name: "查看公开资讯公告", exact: true })
      .click();
    await expect(
      anonymous.getByRole("heading", { name: "充电资讯与公告", exact: true }),
    ).toBeVisible();
    await reg.getByRole("button", { name: "撤下内容", exact: true }).click();
    await expect(reg.locator(".application-detail")).toContainText("已撤下");
    await anonymous.goto(`/portal?article=${publicArticle.id}`);
    await expect(
      anonymous
        .getByRole("region", { name: "公开资讯详情", exact: true })
        .getByRole("alert"),
    ).toContainText("资讯未公开或已撤下");
    expect((await anonymous.request.get(downloadPath)).status()).toBe(404);
    expect((await anonymous.request.get(source)).status()).toBe(404);
  } finally {
    await Promise.allSettled(contexts.map((context) => context.close()));
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
