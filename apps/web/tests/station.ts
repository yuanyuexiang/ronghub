import { Page } from "@playwright/test";
export async function pickStation(
  page: Page,
  label: string,
  fixture: { station_id: number; name: string },
) {
  const views: Record<string, string> = {
    补贴场站: "政策与单站申报",
    评星场站: "新增星级申报",
    巡检场站: "单站快捷下发",
  };
  const view = views[label];
  if (view && !(await page.getByLabel(label, { exact: true }).isVisible()))
    await page.getByRole("button", { name: view, exact: true }).click();
  const picker = page
    .locator(".station-picker")
    .filter({ has: page.getByLabel(label, { exact: true }) });
  await picker.getByRole("textbox").fill(fixture.name);
  await picker.getByRole("button", { name: "筛选", exact: true }).click();
  await page
    .getByLabel(label, { exact: true })
    .selectOption(String(fixture.station_id));
}
