import {
  test as base,
  expect,
  type BrowserContext,
  type Page,
} from "@playwright/test";
import { execFileSync } from "node:child_process";
export * from "@playwright/test";
// Browser scenarios share a localhost gateway. Security rate limits have separate
// PG tests with isolated IPs; do not let unrelated scenarios share throttle state.
export async function prepareCaptcha(page: Page) {
  const image = page.getByRole("img", { name: "登录图片验证码", exact: true });
  await expect(image).toHaveAttribute("data-captcha-id", /.+/);
  const token = (await image.getAttribute("data-captcha-id"))!;
  if ((await image.getAttribute("data-fixture-captcha-id")) === token) {
    await page.getByLabel("图片验证码", { exact: true }).fill("ABCDE");
    return;
  }
  const seeded = JSON.parse(
    execFileSync(
      "uv",
      ["run", "python", "-m", "app.captcha_fixture", "--", token],
      {
        cwd: "../server",
        encoding: "utf8",
      },
    ),
  );
  await image.evaluate(
    (node, values) => {
      (node as HTMLImageElement).src = values.image;
      node.setAttribute("data-fixture-captcha-id", values.token);
    },
    { image: seeded.image, token },
  );
  await page.getByLabel("图片验证码", { exact: true }).fill(seeded.answer);
}
export const test = base.extend<{
  localAuthState: void;
  automaticCaptcha: boolean;
}>({
  automaticCaptcha: [true, { option: true }],
  localAuthState: [
    async ({ browser, automaticCaptcha }, use) => {
      execFileSync(
        "uv",
        ["run", "python", "-m", "app.browser_fixture", "prepare"],
        {
          cwd: "../server",
          stdio: "pipe",
        },
      );
      const original = browser.newContext.bind(browser);
      function install(context: BrowserContext) {
        context.on("page", (page) => {
          if (automaticCaptcha)
            void page.addLocatorHandler(
              page.locator('input[aria-label="图片验证码"][value=""]'),
              () => prepareCaptcha(page),
              { noWaitAfter: true },
            );
        });
      }
      browser.newContext = async (...args) => {
        const context = await original(...args);
        install(context);
        return context;
      };
      try {
        await use();
      } finally {
        browser.newContext = original;
      }
    },
    { auto: true },
  ],
});
