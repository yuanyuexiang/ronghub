import { test, expect, type Page } from "./fixtures";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;
async function login(page: Page, username: string) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台" }).click();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
}
async function open(page: Page, title: string) {
  await page.getByRole("button", { name: "公告与通知", exact: true }).click();
  await page.getByLabel("搜索内容标题", { exact: true }).fill(title);
  await page.getByRole("button", { name: "查询内容", exact: true }).click();
  await page
    .getByRole("row")
    .filter({ has: page.getByRole("cell", { name: title, exact: true }) })
    .getByRole("button", { name: "查看内容", exact: true })
    .click();
}
test("rich article headings, marks, links, tables and private assets publish consistently to three roles", async ({
  browser,
}) => {
  const fixture = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.browser_fixture", "create"],
      { cwd: "../server", encoding: "utf8" },
    ),
  );
  const contexts = await Promise.all([0, 1, 2].map(() => browser.newContext()));
  try {
    const [reg, own, association] = await Promise.all(
      contexts.map((context) => context.newPage()),
    );
    await login(reg, "regulator");
    await login(own, "operator_a");
    await login(association, "association");
    await reg.getByRole("button", { name: "公告与通知", exact: true }).click();
    await reg
      .getByRole("button", { name: "新建内容草稿", exact: true })
      .click();
    await reg.getByLabel("内容标题", { exact: true }).fill(fixture.name);
    const editor = reg.getByLabel("内容正文", { exact: true });
    await editor.fill("充电服务升级通知");
    await reg.getByLabel("正文段落格式", { exact: true }).selectOption("2");
    await editor.press("ArrowRight");
    await editor.press("Enter");
    await reg.getByRole("button", { name: "加粗", exact: true }).click();
    await reg.keyboard.insertText("请各运营企业按期完成设备核查。");
    await reg.getByRole("button", { name: "加粗", exact: true }).click();
    await editor.press("Enter");
    await reg.keyboard.insertText("官方说明");
    for (let i = 0; i < 4; i++) await editor.press("Shift+ArrowLeft");
    await expect
      .poll(() =>
        editor.evaluate((el) => el.ownerDocument.getSelection()?.toString()),
      )
      .toBe("官方说明");
    await reg.getByRole("button", { name: "链接", exact: true }).click();
    await reg
      .getByLabel("正文链接地址", { exact: true })
      .fill("https://example.com/regulator");
    await reg.getByRole("button", { name: "应用链接", exact: true }).click();
    await expect(editor.locator("a")).toHaveText("官方说明");
    await reg.getByRole("button", { name: "刷新数据", exact: true }).click();
    await expect(reg.getByLabel("内容标题", { exact: true })).toHaveValue(
      fixture.name,
    );
    await expect(editor).toContainText("请各运营企业按期完成设备核查。");
    await expect(editor.locator("a")).toHaveAttribute(
      "href",
      "https://example.com/regulator",
    );
    await editor.press("ArrowRight");
    await editor.press("Enter");
    await expect(editor.locator("a")).toHaveText("官方说明");
    await reg.getByRole("button", { name: "插入表格", exact: true }).click();
    await expect(editor.locator("a")).toHaveText("官方说明");
    await editor.locator("th").first().click();
    await reg.keyboard.insertText("核查项目");
    await editor.locator("td").first().click();
    await reg.keyboard.insertText(
      "设备安全 <script>window.__unsafe=1</script>",
    );
    const created = reg.waitForResponse(
      (r) => r.url().endsWith("/content") && r.request().method() === "POST",
    );
    await reg
      .getByRole("button", { name: "保存内容草稿", exact: true })
      .click();
    const response = await created;
    expect(response.status(), await response.text()).toBe(201);
    const article = await response.json();
    await expect(
      reg.getByRole("heading", { name: "充电服务升级通知", exact: true }),
    ).toBeVisible();
    expect(
      (await own.request.get(`/api/v1/content/${article.id}`)).status(),
    ).toBe(404);
    await reg
      .getByRole("button", { name: "编辑草稿正文", exact: true })
      .click();
    const png = Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jfFoAAAAASUVORK5CYII=",
      "base64",
    );
    await reg.getByLabel("上传内容附件", { exact: true }).setInputFiles({
      name: "图文示例.png",
      mimeType: "image/png",
      buffer: png,
    });
    await reg
      .getByRole("button", { name: "插入正文图片 图文示例.png", exact: true })
      .click();
    await expect(editor.locator("img")).toHaveCount(1);
    await reg.getByLabel("上传内容附件", { exact: true }).setInputFiles({
      name: "核查说明.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.concat([
        Buffer.from("%PDF-1.4\n"),
        Buffer.alloc(6 * 1024 * 1024, 32),
      ]),
    });
    await expect(
      reg
        .locator(".article-assets")
        .filter({ has: reg.getByLabel("上传内容附件", { exact: true }) }),
    ).toContainText("核查说明.pdf");
    await expect(
      reg.getByRole("button", { name: "发布内容", exact: true }),
    ).toBeDisabled();
    await reg
      .getByRole("button", { name: "保存内容草稿", exact: true })
      .click();
    await expect(reg.getByLabel("内容正文", { exact: true })).toHaveCount(0);
    await expect(
      reg.getByRole("button", { name: "发布内容", exact: true }),
    ).toBeEnabled();
    const published = reg.waitForResponse(
      (r) =>
        r.url().endsWith(`/content/${article.id}/actions`) &&
        r.request().method() === "POST",
    );
    await reg.getByRole("button", { name: "发布内容", exact: true }).click();
    expect((await published).status()).toBe(200);
    await expect(
      reg.getByRole("button", { name: "撤下内容", exact: true }),
    ).toBeVisible();
    for (const page of [own, association]) {
      await open(page, fixture.name);
      await expect(
        page.getByRole("heading", { name: "充电服务升级通知", exact: true }),
      ).toBeVisible();
      const body = page.getByLabel("富文本正文", { exact: true });
      await expect(body.locator("strong")).not.toHaveCount(0);
      await expect(
        body.getByRole("link", { name: "官方说明", exact: true }),
      ).toHaveAttribute("href", "https://example.com/regulator");
      await expect(body.locator("table")).toContainText("设备安全");
      await expect(body.locator("img")).toHaveCount(1);
      const image = body.locator("img");
      await expect(image).toHaveJSProperty("complete", true);
      expect(
        (await page.request.get((await image.getAttribute("src"))!)).status(),
      ).toBe(200);
      await expect(body.locator("script")).toHaveCount(0);
      expect(await page.evaluate(() => Object.hasOwn(window, "__unsafe"))).toBe(
        false,
      );
      const pdf = page.getByRole("link", { name: "核查说明.pdf", exact: true });
      const downloaded = await page.request.get(
        (await pdf.getAttribute("href"))!,
      );
      expect(downloaded.status()).toBe(200);
      expect((await downloaded.body()).byteLength).toBeGreaterThan(
        5 * 1024 * 1024,
      );
      expect(downloaded.headers()["content-disposition"]).toContain(
        "attachment",
      );
      await expect(
        page.getByRole("button", { name: "编辑草稿正文", exact: true }),
      ).toHaveCount(0);
    }
    await own.screenshot({
      path: "test-results/rich-content-reader.png",
      fullPage: true,
    });
    await reg.getByRole("button", { name: "撤下内容", exact: true }).click();
    await expect(reg.locator(".application-detail")).toContainText("已撤下");
    expect(
      (await own.request.get(`/api/v1/content/${article.id}`)).status(),
    ).toBe(404);
  } finally {
    for (const context of contexts) await context.close();
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
      { cwd: "../server" },
    );
  }
});
