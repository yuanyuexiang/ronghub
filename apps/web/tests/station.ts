import { Page } from "@playwright/test";
export async function pickStation(
  page: Page,
  label: string,
  fixture: { station_id: number; name: string },
) {
  const picker = page
    .locator(".station-picker")
    .filter({ has: page.getByLabel(label, { exact: true }) });
  await picker.getByRole("textbox").fill(fixture.name);
  await picker.getByRole("button", { name: "筛选", exact: true }).click();
  await page
    .getByLabel(label, { exact: true })
    .selectOption(String(fixture.station_id));
}
