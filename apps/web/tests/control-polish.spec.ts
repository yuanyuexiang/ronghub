import { test, expect, navigateWorkspace } from "./fixtures";
import { readFileSync } from "node:fs";
const password = JSON.parse(
  readFileSync("../server/.demo-credentials.json", "utf8"),
).password;

test("material picker keeps native keyboard selection, removal, reselect and local validation without premature uploads", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("operator_a");
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录平台", exact: true }).click();
  await navigateWorkspace(page, "申报材料填报");
  const file = page.getByLabel("填报-site_plan", { exact: true });
  const card = page.locator(".rating-material-card").filter({ has: file });
  const writes: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname.startsWith("/api/v1/ratings/")
    )
      writes.push(request.url());
  });
  const payload = {
    name: "同一份-站点平面图.zip",
    mimeType: "application/zip",
    buffer: Buffer.from([0x50, 0x4b, 0x03, 0x04, 0x01]),
  };
  const picker = page.waitForEvent("filechooser");
  await card.locator(".rong-file-picker").click();
  await (await picker).setFiles(payload);
  await expect(card.locator(".rong-file-name")).toHaveText(payload.name);
  await expect(card.getByText("已选择，待保存", { exact: true })).toBeVisible();
  await card.getByRole("button", { name: "移除选择", exact: true }).click();
  await expect(file).toHaveValue("");
  await expect(card.getByText("未选择", { exact: true })).toBeVisible();
  await file.focus();
  const keyboardPicker = page.waitForEvent("filechooser");
  await page.keyboard.press("Enter");
  await (await keyboardPicker).setFiles(payload);
  await expect(card.locator(".rong-file-name")).toHaveText(payload.name);
  await card.getByRole("button", { name: "移除选择", exact: true }).click();
  await file.setInputFiles({
    name: "错误.exe",
    mimeType: "application/octet-stream",
    buffer: Buffer.from("invalid"),
  });
  await expect(
    page.getByRole("alert").filter({ hasText: "请选择非空ZIP" }),
  ).toBeVisible();
  await expect(file).toHaveValue("");
  await expect(card.getByText("未选择", { exact: true })).toBeVisible();
  await file.setInputFiles(payload);
  await expect(card.locator(".rong-file-name")).toHaveText(payload.name);
  expect(writes).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await expect(card.locator(".rong-file-trigger")).toBeVisible();
});
