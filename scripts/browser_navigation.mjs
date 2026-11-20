// Shared navigation actions for standalone browser verification.
import { createRequire } from 'node:module';
const { expect } = createRequire(new URL('../apps/web/package.json', import.meta.url))('@playwright/test');
export async function openWorkspaceNavigation(page) {
    const toggle = page.getByRole("button", {
        name: "展开或收起功能导航",
        exact: true,
    });
    if ((await toggle.isVisible()) &&
        (await toggle.getAttribute("aria-expanded")) === "false") {
        await toggle.click();
    }
}
export async function navigateWorkspace(page, name) {
    await expect(page.getByRole("button", { name: "退出登录", exact: true })).toBeVisible();
    const aliases = {
        场站地图: ["可视化一张图"],
        全域地图: ["可视化一张图"],
        场站台账: ["场站数据同步与场站信息查询", "信息推送", "多维度数据筛选"],
        场站信息查询: ["场站数据同步与场站信息查询"],
        星级查看: ["企业申报查看"],
        星级评价: ["企业申报管理"],
        星级申报: ["评定进度查询"],
        企业申报查看: ["企业申报管理", "评定进度查询"],
        统计分析: ["充电量统计"],
        补贴申报: ["补贴申请管理"],
        补贴管理: ["补贴申请记录"],
        巡检督办: ["巡检任务管理"],
        整改任务: ["巡检任务"],
        场站申请: ["接入场站管理"],
        公告与通知: ["新增发布资讯"],
        评价结果查看: ["评审结果查看"],
        评价结果与公示: ["公示查询查看", "公示查询服务"],
        公示查询查看: ["公示查询服务"],
        数据支撑查看: ["数据支撑评价"],
        评审过程查看: ["评审过程管理"],
        场站数据同步与场站信息查询: ["信息推送", "多维度数据筛选"],
        观测电量分析: ["充电量统计", "统计分析"],
        充电与补贴报表: ["运营商充电及补贴统计分析", "充电与补贴数据统计"],
        系统操作日志: ["操作日志"],
        监管协议: ["监管协议开发"],
        充电时段: ["充电时段统计"],
        巡检计划创建: ["巡检计划"],
    };
    const names = [
        name,
        ...(typeof name === "string"
            ? (aliases[name] ?? [])
            : Object.entries(aliases)
                .filter(([key]) => name.test(key))
                .flatMap(([, values]) => values)),
    ];
    const nav = page.getByRole("navigation", {
        name: "业务模块导航",
        exact: true,
        includeHidden: true,
    });
    const extra = page.getByRole("navigation", {
        name: "辅助功能导航",
        exact: true,
        includeHidden: true,
    });
    // Exact auxiliary names retain their original module semantics (e.g. operator energy analysis).
    for (const candidate of names) {
        const local = page.locator("main.content").getByRole("button", {
            name: candidate,
            exact: true,
            includeHidden: true,
        });
        for (const detail of await local
            .first()
            .locator("xpath=ancestor::details")
            .all()) {
            if ((await detail.getAttribute("open")) === null &&
                (await detail.isVisible()))
                await detail.locator(":scope > summary").click();
        }
        if ((await local.count()) && (await local.first().isVisible())) {
            await local.first().click();
            return;
        }
    }
    for (const candidate of names) {
        const target = nav.getByRole("button", {
            name: candidate,
            exact: true,
            includeHidden: true,
        });
        if (await target.count()) {
            await openWorkspaceNavigation(page);
            const button = target.first();
            const group = button.locator('xpath=ancestor::section[contains(@class,"navigation-group")]');
            const trigger = group.locator(":scope > button[aria-expanded]");
            if ((await trigger.count()) &&
                (await trigger.getAttribute("aria-expanded")) === "false")
                await trigger.click();
            for (const entry of await button
                .locator('xpath=ancestor::div[contains(@class,"navigation-entry")]')
                .all()) {
                const parent = entry.locator(":scope > button[aria-expanded]");
                if ((await parent.count()) &&
                    (await parent.getAttribute("aria-expanded")) === "false")
                    await parent.click();
            }
            await button.click();
            return;
        }
        const auxiliary = extra.getByRole("button", {
            name: candidate,
            exact: true,
            includeHidden: true,
        });
        if (await auxiliary.count()) {
            const more = page.getByRole("button", { name: "更多功能", exact: true });
            if ((await more.getAttribute("aria-expanded")) === "false")
                await more.click();
            await auxiliary.first().click();
            return;
        }
    }
    for (const candidate of names) {
        const local = page.getByRole("button", { name: candidate, exact: true });
        if (await local.count()) {
            await local.first().click();
            return;
        }
    }
    await page.getByRole("button", { name, exact: true }).click();
}
