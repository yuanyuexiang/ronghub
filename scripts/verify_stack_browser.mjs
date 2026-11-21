import { navigateWorkspace } from './browser_navigation.mjs';
// Read local credentials in-process; never print them or capture login forms.
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire(new URL('../apps/web/package.json', import.meta.url));
const { chromium, expect } = require('@playwright/test');
const env = Object.fromEntries(readFileSync(new URL('../deploy/.env.stack', import.meta.url), 'utf8')
  .split('\n').filter(line => line && !line.startsWith('#')).map(line => {
    const index = line.indexOf('='); return [line.slice(0,index),line.slice(index+1)];
  }));
if (env.PUBLIC_ORIGIN !== 'http://127.0.0.1:3080' || env.DEMO_MODE !== 'true') {
  throw new Error('Browser smoke restricted to default local demo stack');
}
const browser = await chromium.launch();
try {
  for (const username of ['regulator','association','operator_a']) {
    const context = await browser.newContext({viewport:{width:1440,height:1000}});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(env.PUBLIC_ORIGIN);
    await page.getByLabel('账号',{exact:true}).fill(username);
    await page.getByLabel('密码',{exact:true}).fill(env.DEMO_PASSWORD);
    const captchaImage=page.getByRole('img',{name:'登录图片验证码',exact:true});
    await expect(captchaImage).toHaveAttribute('data-captcha-id',/.+/);
    const captchaId=await captchaImage.getAttribute('data-captcha-id');
    const captchaSeed=JSON.parse(execFileSync('python3',['-c', 'from pathlib import Path; import sys; from scripts.stack_ops import Stack; result=Stack(Path("deploy/.env.stack")).run("exec","-T","api","python","-m","app.captcha_fixture","--container-preview","--",sys.argv[1],capture=True); sys.stdout.buffer.write(result.stdout)',captchaId],{cwd:new URL('..',import.meta.url),encoding:'utf8'}));
    await page.getByLabel('图片验证码',{exact:true}).fill(captchaSeed.answer);
    await page.getByRole('button',{name:'登录平台'}).click();
    await expect(page.getByRole('button',{name:'退出登录'})).toBeVisible();
    const phoneStateResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/auth/phone');
    const phoneState=await phoneStateResponse.json();
    if(phoneStateResponse.status()!==200 || typeof phoneState.verified!=='boolean' || phoneState.sms_mode!=='demo' || phoneState.phone!==undefined) throw new Error('Personal masked phone contract failed');
    await navigateWorkspace(page, '账号安全');
    await expect(page.getByRole('heading',{name:'账号手机验证',exact:true})).toBeVisible();
    const staffQueue=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/staff-applications');
    if(staffQueue.status()!==(username==='regulator'?200:403)) throw new Error('Staff application manager scope failed');
    if(username==='regulator') {
      await navigateWorkspace(page, '人员账号申请');
      await expect(page.getByRole('heading',{name:'工作人员账号申请审核',exact:true})).toBeVisible();
    }
    const identityResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/auth/me');
    if(identityResponse.status()!==200) throw new Error('Production identity failed');
    const identity=await identityResponse.json();
    if(!identity.permissions.includes('stations.export')) throw new Error('Default role missing independent map export grant');
    const mapCsv=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/map/export?location=all');
    if(mapCsv.status()!==200 || mapCsv.headers()['cache-control']!=='no-store' || !mapCsv.headers()['content-type'].startsWith('text/csv') || !(await mapCsv.text()).includes('来源首次接入时间')) throw new Error('Production scoped map CSV failed');
    const facilitiesListResponse = await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/stations?page_size=1');
    const facilitiesList = await facilitiesListResponse.json();
    if(facilitiesList.items.length) {
      const facilitiesResponse = await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/stations/'+facilitiesList.items[0].id);
      const facilities = await facilitiesResponse.json();
      if(facilitiesResponse.status()!==200 || !facilities.facilities || !('independent_connection' in facilities.facilities) || !('supply_capacity_unit' in facilities.facilities)) throw new Error('Station source profile contract failed');
      const historyResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/stations/'+facilitiesList.items[0].id+'/energy-history?start_date=2026-11-01&end_date=2026-11-03');
      const history=await historyResponse.json();
      if(historyResponse.status()!==200 || !Array.isArray(history.streams) || history.source_completeness_verified!==false) throw new Error('Station energy history production contract failed');
      const flowResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/stations/'+facilitiesList.items[0].id+'/flow?page_size=1');
      if(username==='association') { if(flowResponse.status()!==403) throw new Error('Station flow association privacy widened'); }
      else { const flow=await flowResponse.json(); if(flowResponse.status()!==200 || !Number.isInteger(flow.total) || flow.reporting_state!==null || !('source_first_reported_at' in flow)) throw new Error('Station flow production contract failed'); }
      const devicePageResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/stations/'+facilitiesList.items[0].id+'/equipment/query?page_size=1');
      const devicePage=await devicePageResponse.json();
      if(devicePageResponse.status()!==200 || !Number.isInteger(devicePage.total) || !Number.isInteger(devicePage.station_connector_count)) throw new Error('Device inventory production page failed');
      if(devicePage.items.length) {
        const terminals=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/stations/'+facilitiesList.items[0].id+'/equipment/'+devicePage.items[0].id+'/connectors?page_size=1');
        if(terminals.status()!==200 || !Number.isInteger((await terminals.json()).inventory_total)) throw new Error('Connector production page failed');
      }
      if(username==='association' && (facilities.contact_accessible!==false || facilities.contact!==null)) throw new Error('Station contact privacy widened');
      if(username!=='association' && facilities.contact_accessible!==true) throw new Error('Station contact capability lost');
    }
    const operationsResponse = await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/dashboard/operations?period=month&period_date=2026-11-15');
    if(username==='association') {
      if(operationsResponse.status()!==403) throw new Error('Operations role ceiling widened');
    } else {
      const operations = await operationsResponse.json();
      if(operationsResponse.status()!==200 || !Number.isInteger(operations.current_equipment) || typeof operations.current_equipment_power_kw!=='string' || !Array.isArray(operations.statuses) || !Array.isArray(operations.energy_trend) || operations.source_completeness_verified!==false || operations.approvals.approval_rate_pct!==null) throw new Error('Production operations snapshot failed');
    }
    const combinedPath = '/api/v1/statistics/charging-subsidy?period_date=2026-11-15';
    const combinedResponse = await page.request.get(env.PUBLIC_ORIGIN + combinedPath);
    const combinedFile = await page.request.get(env.PUBLIC_ORIGIN + combinedPath.replace('charging-subsidy?', 'charging-subsidy/export?'));
    if(username==='association') {
      if(combinedResponse.status()!==403 || combinedFile.status()!==403) throw new Error('Combined report role ceiling widened');
    } else {
      const combined = await combinedResponse.json();
      if(combinedResponse.status()!==200 || !Array.isArray(combined.items) || !Array.isArray(combined.orders) || !Array.isArray(combined.subsidies) || combined.source_completeness_verified!==false) throw new Error('Combined report production contract failed');
      if(combinedFile.status()!==200 || combinedFile.headers()['cache-control']!=='no-store' || combinedFile.headers()['content-type']!=='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' || (await combinedFile.body()).subarray(0,2).toString()!=='PK') throw new Error('Combined Excel production export failed');
    }
    for(const path of ['/api/v1/roles','/api/v1/accounts/options','/api/v1/accounts?q=production-smoke-no-matching-fixture']) {
      const response = await page.request.get(env.PUBLIC_ORIGIN+path);
      if(username!=='regulator') {
        if(response.status()!==403) throw new Error('Authorization management role ceiling widened');
      } else {
        if(response.status()!==200) throw new Error('Authorization management read failed');
        const data = await response.json();
        if(!Array.isArray(data.items)) throw new Error('Authorization management response contract failed');
        if(path.startsWith('/api/v1/accounts?')) {
          if(data.total!==0 || data.items.length!==0) throw new Error('Managed account applied query failed');
        } else if(data.items.some(profile=>!Number.isInteger(profile.version) || !Array.isArray(profile.permissions) || profile.permissions.some(code=>!identity.permissions.includes(code)))) throw new Error('Role options escaped issuer grant boundary');
        if(path==='/api/v1/roles' && (!Array.isArray(data.catalog) || !data.base_roles)) throw new Error('Role catalog response failed');
      }
    }
    if(username==='regulator') {
      await navigateWorkspace(page, '账号管理');
      await page.getByRole('button',{name:'新建账号',exact:true}).click();
      await expect(page.getByText('至少12个字符。新账号首次登录必须修改临时密码，改密后重新登录。',{exact:true})).toBeVisible();
      await page.screenshot({path:'.backups/initial-password-account-form.png',fullPage:true});
      await page.getByRole('button',{name:'返回账号列表',exact:true}).click();
    }
    const batchGuard=await page.request.post(env.PUBLIC_ORIGIN+'/api/v1/subsidy/claims/batch-actions',{headers:{'x-csrf-token':identity.csrf_token,'Origin':env.PUBLIC_ORIGIN},data:{action:'approve',items:[],comment:'Empty-batch validation smoke'}});
    if(batchGuard.status()!==(username==='regulator'?422:403)) throw new Error('Subsidy batch permission/input guard failed');

    if(identity.permissions.includes('subsidy.write')) throw new Error('Legacy subsidy wildcard still active');
    if(identity.permissions.includes('applications.write')) throw new Error('Legacy application wildcard still active');
    if(identity.permissions.includes('ratings.write')) throw new Error('Legacy rating wildcard still active');
    const operatorRatings=['create','edit','materials','submit','withdraw'];
    const associationRatings=['review','publish','delist','standards'];
    const requiredRatings=username==='regulator' ? [...operatorRatings,...associationRatings] : username==='operator_a' ? operatorRatings : associationRatings;
    for(const action of requiredRatings) if(!identity.permissions.includes('ratings.'+action)) throw new Error('Missing explicit rating action grant');
    const forbiddenRatings=username==='operator_a' ? associationRatings : username==='association' ? operatorRatings : [];
    for(const action of forbiddenRatings) if(identity.permissions.includes('ratings.'+action)) throw new Error('Rating role ceiling widened');
    if(identity.permissions.includes('inspections.write')) throw new Error('Legacy inspection wildcard still active');
    const requiredInspections=username==='regulator' ? ['assign','materials','reply','return','close'] : username==='operator_a' ? ['materials','reply'] : [];
    for(const action of requiredInspections) if(!identity.permissions.includes('inspections.'+action)) throw new Error('Missing explicit inspection action grant');
    if(username!=='regulator' && identity.permissions.some(code=>['inspections.assign','inspections.return','inspections.close'].includes(code))) throw new Error('Inspection role ceiling widened');
    if(identity.permissions.includes('alerts.write')) throw new Error('Legacy alert wildcard still active');
    const alertActions=['rules','samples','acknowledge'];
    for(const action of alertActions) {
      const allowed=identity.permissions.includes('alerts.'+action);
      if(allowed !== (username==='regulator')) throw new Error('Alert action role ceiling mismatch');
    }
    if(identity.permissions.includes('content.write')) throw new Error('Legacy content wildcard still active');
    for(const action of ['manage','create','edit','publish','withdraw','delete','export']) {
      if(identity.permissions.includes('content.'+action) !== (username==='regulator')) throw new Error('Content action role ceiling mismatch');
    }
    const contentContractResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/content');
    const contentContract=await contentContractResponse.json();
    if(contentContractResponse.status()!==200 || contentContract.page_size!==20 || !Array.isArray(contentContract.items)) throw new Error('Content list contract failed');
    if(username==='regulator') {
      await navigateWorkspace(page, username==='regulator'?'新增发布资讯':'公告与通知');
      await page.getByRole('button',{name:'新建内容草稿',exact:true}).click();
      await expect(page.getByRole('toolbar',{name:'正文格式工具',exact:true})).toBeVisible();
      await expect(page.getByLabel('内容正文',{exact:true})).toBeVisible();
      await page.getByRole('button',{name:'取消正文编辑',exact:true}).click();
    }
    const noticeRecords = await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/content/notice-records'+(username==='regulator' ? '?manage=TRUE' : ''));
    if(noticeRecords.status() !== (username==='association' ? 403 : 200)) throw new Error('Notice record role scope failed');
    if(username!=='association') {
      const noticePage = await noticeRecords.json();
      if(noticePage.page_size!==20 || !Array.isArray(noticePage.items)) throw new Error('Notice record contract failed');
      await navigateWorkspace(page, username==='regulator'?'自定义通知发布':'公告与通知');
      if(await page.getByRole('button',{name:'展开通知记录',exact:true}).isVisible()) await page.getByRole('button',{name:'展开通知记录',exact:true}).click();
      await expect(page.getByRole('button',{name:'查询通知记录',exact:true})).toBeEnabled();
    }
    const noticeOptions = await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/content/operator-options');
    if(noticeOptions.status() !== (username==='regulator' ? 200 : 403)) throw new Error('Notice operator options scope failed');
    for(const exportPath of ['/api/v1/content/export','/api/v1/content/notice-records/export']) {
      const contentExport = await page.request.get(env.PUBLIC_ORIGIN+exportPath);
      if(contentExport.status() !== (username==='regulator' ? 200 : 403)) throw new Error('Content export capability scope failed');
      if(username==='regulator' && !contentExport.headers()['content-type'].includes('text/csv')) throw new Error('Content export media type failed');
    }
    const managedContent=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/content?manage=TRUE');
    if(managedContent.status() !== (username==='regulator' ? 200 : 403)) throw new Error('Content draft management scope failed');
    const requiredApplications=username==='regulator' ? ['create','edit','materials','submit','withdraw','review'] : username==='operator_a' ? ['create','edit','materials','submit','withdraw'] : [];
    for(const action of requiredApplications) if(!identity.permissions.includes('applications.'+action)) throw new Error('Missing explicit application action grant');
    if(username!=='regulator' && identity.permissions.includes('applications.review')) throw new Error('Enterprise application review ceiling widened');

    const requiredSubsidy = username==='regulator' ? ['create','edit','materials','submit','withdraw','adjust','review','disburse','policies'] : username==='operator_a' ? ['create','edit','materials','submit','withdraw'] : [];
    for(const action of requiredSubsidy) if(!identity.permissions.includes('subsidy.'+action)) throw new Error('Missing explicit subsidy action grant');
    if(username!=='regulator' && identity.permissions.some(code=>['subsidy.adjust','subsidy.review','subsidy.disburse','subsidy.policies'].includes(code))) throw new Error('Subsidy role ceiling widened');

    if(username==='association') {
      if((await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/orders/time-slots')).status()!==403) throw new Error('Association time-slot privacy failed');
      await expect(page.getByRole('button',{name:'充电时段',exact:true})).toHaveCount(0);
    } else {
      const stationList=await (await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/stations?page_size=1')).json();
      const stationId=stationList.items[0].id;
      const slotQuery='?station_id='+stationId+'&source=demo&start_at=2026-11-01T00%3A00%3A00%2B08%3A00&end_at=2026-11-02T00%3A00%3A00%2B08%3A00';
      const slotsResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/orders/time-slots'+slotQuery);
      const slots=await slotsResponse.json();
      if(slotsResponse.status()!==200 || slots.timezone!=='Asia/Shanghai' || slots.source_completeness_verified!==false || !Array.isArray(slots.streams)) throw new Error('Time-slot production contract failed');
      await navigateWorkspace(page, username==='regulator'?'充电时段统计':'充电时段');
      await expect(page.getByRole('heading',{name:'订单开始分布与源报小时电量',exact:true})).toBeVisible();
    }
    const qualitySourcesResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/statistics/quality/sources?page_size=1');
    const qualityQuery='?source=demo&group_by=operator&asset_scope=connector&page_size=1&start_at=2026-11-01T08%3A00%3A00%2B08%3A00&end_at=2026-11-01T12%3A00%3A00%2B08%3A00';
    const qualityResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/statistics/quality'+qualityQuery);
    if(username==='association') {
      if(qualitySourcesResponse.status()!==403 || qualityResponse.status()!==403) throw new Error('Association quality scope failed');
    } else {
      const qualitySources=await qualitySourcesResponse.json(); const quality=await qualityResponse.json();
      if(qualitySourcesResponse.status()!==200 || qualitySources.page_size!==1 || qualityResponse.status()!==200 || quality.page_size!==1 || quality.source_completeness_verified!==false || quality.faults_scope!=='all_station_events_started_in_window' || !Array.isArray(quality.items) || !Number.isInteger(quality.summary?.station_count) || !Number.isInteger(quality.summary?.inventory_equipment_count) || !Array.isArray(quality.summary?.streams)) throw new Error('Quality production contract failed');
      await navigateWorkspace(page, username==='regulator'?'动态监控':'运行状态');
      if(username==='regulator' && await page.getByText('历史运行与质量观测',{exact:true}).locator('..').getAttribute('open')===null) await page.getByText('历史运行与质量观测',{exact:true}).click();
      await page.getByRole('button',{name:'区域与企业质量',exact:true}).click();
      await expect(page.getByRole('heading',{name:'区域与企业质量观测',exact:true})).toBeVisible();
      await expect(page.getByRole('button',{name:'查询质量观测',exact:true})).toBeEnabled();
      await page.getByLabel('质量站企名称筛选',{exact:true}).fill('production-quality-no-matching-fixture');
      await page.getByRole('button',{name:'查询质量观测',exact:true}).click();
      await expect(page.getByText('当前条件下没有质量观测分组',{exact:true})).toBeVisible();
    }
    const runtimeStationsResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/statistics/runtime/stations?page_size=1');
    if(username==='association') {
      if(runtimeStationsResponse.status()!==403) throw new Error('Association runtime station scope failed');
      await expect(page.getByRole('button',{name:username==='regulator'?'动态监控':'运行状态',exact:true})).toHaveCount(0);
    } else {
      const runtimeStations=await runtimeStationsResponse.json();
      if(runtimeStationsResponse.status()!==200 || runtimeStations.page_size!==1 || !runtimeStations.items[0]) throw new Error('Runtime scoped station contract failed');
      const runtimeStation=runtimeStations.items[0];
      const runtimeQuery='?station_id='+runtimeStation.id+'&source=demo&equipment_external_id=equipment-1&connector_external_id=connector-1&start_at=2026-11-01T08%3A00%3A00%2B08%3A00&end_at=2026-11-01T12%3A00%3A00%2B08%3A00';
      const runtimeResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/statistics/runtime'+runtimeQuery);
      const runtime=await runtimeResponse.json();
      if(runtimeResponse.status()!==200 || runtime.source_completeness_verified!==false || runtime.window_seconds!=='14400' || !Array.isArray(runtime.streams)) throw new Error('Runtime production report contract failed');
      const runtimeObjects=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/statistics/runtime/objects?station_id='+runtimeStation.id+'&source=demo&page_size=1');
      if(runtimeObjects.status()!==200 || (await runtimeObjects.json()).page_size!==1) throw new Error('Runtime object pagination failed');
      await navigateWorkspace(page, username==='regulator'?'动态监控':'运行状态');
      if(username==='regulator' && await page.getByText('历史运行与质量观测',{exact:true}).locator('..').getAttribute('open')===null) await page.getByText('历史运行与质量观测',{exact:true}).click();
      await page.getByRole('button',{name:'单对象区间',exact:true}).click();
      await expect(page.getByRole('heading',{name:'历史运行状态与观测覆盖',exact:true})).toBeVisible();
    }
    const faultResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/statistics/faults?page_size=1');
    const faultSummaryResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/statistics/faults/summary');
    if(username==='association') {
      if(faultResponse.status()!==403 || faultSummaryResponse.status()!==403) throw new Error('Association fault privacy failed');
      await expect(page.getByRole('button',{name:username==='regulator'?'告警分析':'故障分析',exact:true})).toHaveCount(0);
    } else {
      const faults=await faultResponse.json(); const faultSummary=await faultSummaryResponse.json();
      if(faultResponse.status()!==200 || faults.page_size!==1 || faults.source_completeness_verified!==false || faultSummaryResponse.status()!==200 || !Array.isArray(faultSummary.streams)) throw new Error('Source fault production contract failed');
      await navigateWorkspace(page, username==='regulator'?'告警分析':'故障分析');
      if(username==='regulator') await page.getByText('源报故障事件明细',{exact:true}).click();
      await expect(page.getByRole('heading',{name:'源报故障事件',exact:true})).toBeVisible();
      await expect(page.getByRole('button',{name:'查询故障',exact:true})).toBeEnabled();
      await page.getByLabel('故障检索名称',{exact:true}).fill('production-fault-no-matching-fixture');
      await page.getByRole('button',{name:'查询故障',exact:true}).click();
      await expect(page.getByText('当前条件下没有故障记录',{exact:true})).toBeVisible();
    }
    const aggregateQuery='?source=demo&start_at=2026-11-01T00%3A00%3A00%2B08%3A00&end_at=2026-11-02T00%3A00%3A00%2B08%3A00';
    const aggregateResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/orders/time-slots/aggregate'+aggregateQuery);
    const sourcesResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/orders/time-slots/aggregate/sources?page_size=1');
    if(username==='association') {
      if(aggregateResponse.status()!==403 || sourcesResponse.status()!==403) throw new Error('Association multi-station privacy failed');
    } else {
      const aggregate=await aggregateResponse.json(); const sources=await sourcesResponse.json();
      if(aggregateResponse.status()!==200 || aggregate.source_completeness_verified!==false || !Number.isInteger(aggregate.station_count) || sourcesResponse.status()!==200 || sources.page_size!==1) throw new Error('Aggregate production contract failed');
      await navigateWorkspace(page, username==='regulator'?'充电时段统计':'充电时段');
      await page.getByRole('button',{name:'多站汇总',exact:true}).click();
      await expect(page.getByRole('heading',{name:'授权范围多站时段汇总',exact:true})).toBeVisible();
    }
    const summaryQuery='?start_at=2026-11-01T00%3A00%3A00Z&end_at=2026-12-01T00%3A00%3A00Z&dimension=equipment&page_size=1';
    const summaryResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/orders/summary'+summaryQuery);
    if(username==='association') {
      if(summaryResponse.status()!==403) throw new Error('Association summary privacy failed');
      await expect(page.getByRole('button',{name:'订单汇总',exact:true})).toHaveCount(0);
    } else {
      const summary=await summaryResponse.json();
      if(summaryResponse.status()!==200 || summary.page_size!==1 || summary.source_completeness_verified!==false || summary.timezone!=='Asia/Shanghai') throw new Error('Order summary production contract failed');
      await navigateWorkspace(page, '订单汇总');
      await page.getByLabel('汇总检索名称',{exact:true}).fill('production-summary-no-matching-fixture');
      await expect(page.getByRole('button',{name:'查询订单汇总',exact:true})).toBeEnabled();
      await page.getByRole('button',{name:'查询订单汇总',exact:true}).click();
      await expect(page.getByText('当前条件下没有订单汇总记录',{exact:true})).toBeVisible();
    }
    const ordersResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/orders?page_size=1');
    if(username==='association') {
      if(ordersResponse.status()!==403) throw new Error('Association order privacy ceiling failed');
      await expect(page.getByRole('button',{name:'充电订单',exact:true})).toHaveCount(0);
    } else {
      if(!identity.permissions.includes('orders.read') || !identity.permissions.includes('orders.export')) throw new Error('Missing explicit order grants');
      const orders=await ordersResponse.json();
      if(ordersResponse.status()!==200 || orders.page_size!==1 || orders.source_completeness_verified!==false) throw new Error('Production order contract failed');
      await navigateWorkspace(page, '充电订单');
      await page.getByLabel('订单检索名称',{exact:true}).fill('production-orders-no-matching-fixture');
      await expect(page.getByRole('button',{name:'查询订单',exact:true})).toBeEnabled();
      await page.getByRole('button',{name:'查询订单',exact:true}).click();
      await expect(page.getByText('当前条件下没有订单记录',{exact:true})).toBeVisible();
    }
    const syncOptions = await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/sync/operators');
    if(syncOptions.status() !== (username==='regulator'?200:403)) throw new Error('Sync options role scope failed');
    if(username==='regulator') {
      if(syncOptions.headers()['cache-control']!=='no-store') throw new Error('Sync options private cache failed');
      const choices=(await syncOptions.json()).items;
      if(!choices.length || choices.some(item=>Object.keys(item).sort().join(',')!=='id,name')) throw new Error('Sync options minimal contract failed');
      await navigateWorkspace(page, '场站数据同步');
      await expect(page.getByRole('heading',{name:'数据同步任务',exact:true})).toBeVisible();
      await expect(page.getByLabel('同步运营商')).toBeEnabled();
      await expect(page.getByRole('button',{name:'发起模拟同步',exact:true})).toBeEnabled();
      await page.screenshot({path:'.backups/operational-sync-production.png'});
    }
    await navigateWorkspace(page, username==='regulator'?'场站数据同步与场站信息查询':'场站台账');
    for(const path of ['/api/v1/map?zoom=19','/api/v1/map/stations?location=all&page_size=1','/api/v1/map/options','/api/v1/ratings/publications?page_size=1']) {
      const response=await page.request.get(env.PUBLIC_ORIGIN+path);
      if(response.status()!==200 || response.headers()['cache-control']!=='no-store') throw new Error('Scoped query/cache failed: '+path);
      const data=await response.json();
      if(path.includes('/map?') && (!Array.isArray(data.items) || !Number.isInteger(data.matching) || data.coordinate_system!=='wgs84' || data.items.some(item=>item.kind==='cluster' && item.bounds.length!==4))) throw new Error('Map discriminated response failed');
      if(path.includes('/stations?') && (!Number.isInteger(data.total) || data.page_size!==1)) throw new Error('Map page response failed');
      if(path.includes('/publications?') && (!Number.isInteger(data.total) || data.timezone!=='Asia/Shanghai' || !data.scope.includes('非匿名'))) throw new Error('Publication scoped response failed');
    }
    const energyPath='/api/v1/statistics?start_date=2026-11-01&end_date=2026-11-03';
    const energyResponse=await page.request.get(env.PUBLIC_ORIGIN+energyPath);
    if(energyResponse.status()!==200 || energyResponse.headers()['cache-control']!=='no-store') throw new Error('Energy private response failed');
    const energy=await energyResponse.json();
    if(energy.timezone!=='Asia/Shanghai' || !Array.isArray(energy.ranking) || !energy.coverage || energy.comparisons.year_over_year.source_completeness_verified!==false || (energy.total_kwh!==null && typeof energy.total_kwh!=='string')) throw new Error('Energy nullable decimal/coverage failed');
    for(const path of ['/api/v1/statistics/export','/api/v1/statistics/analysis/export']) {
      const response=await page.request.get(env.PUBLIC_ORIGIN+path+'?start_date=2026-11-01&end_date=2026-11-03');
      if(response.status()!==(username==='association'?403:200)) throw new Error('Energy export role failed');
      if(username!=='association' && (response.headers()['cache-control']!=='no-store' || !response.headers()['content-type'].startsWith('text/csv'))) throw new Error('Energy CSV metadata failed');
    }
    await expect(page.getByLabel('搜索场站')).toBeVisible();
    await expect(page.getByText(/^(演示·鼓楼公共充电站|鼓楼清和公共充电站)$/).first()).toBeVisible();
    const styled = await page.getByRole('button',{name:'退出登录'}).evaluate(el =>
      getComputedStyle(el).fontFamily.includes('sans'));
    if (!styled || errors.length) throw new Error('Production assets or hydration failed');
    if (username === 'regulator') {
      await navigateWorkspace(page, '系统操作日志');
      await expect(page.getByRole('heading',{name:/^(系统操作日志|操作日志)$/,exact:true}).first()).toBeVisible();
      await expect(page.getByRole('button',{name:'查询日志'})).toBeEnabled();
      const response = await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/audits');
      if (response.status() !== 200) throw new Error('Production audit API failed');
      const audits=await response.json();
      if(!Array.isArray(audits.items) || !audits.notice.includes('不回填旧历史')) throw new Error('Production business audit contract failed');
    } else {
      await expect(page.getByRole('button',{name:'系统操作日志',exact:true})).toHaveCount(0);
      if ((await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/audits')).status() !== 403) throw new Error('Production audit permission failed');
    }
    if (username !== 'association') {
      await navigateWorkspace(page, username==='regulator'?'接入管理':'场站申请');
      await expect(page.getByRole('button',{name:'查询申请',exact:true})).toBeEnabled();
      await page.getByLabel('检索申请场站或企业').fill('production-smoke-no-matching-fixture');
      await page.getByRole('button',{name:'查询申请',exact:true}).click();
      await expect(page.getByText('当前条件下没有申请记录',{exact:true})).toBeVisible();
      const applications = await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/applications?page_size=1');
      if (applications.status() !== 200 || (await applications.json()).page_size !== 1) throw new Error('Production application pagination failed');
      if ((await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/applications?status=invalid')).status() !== 422) throw new Error('Production application filter validation failed');
    }
    const companiesResponse = await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/companies');
    if(username==='association') {
      if(companiesResponse.status()!==403) throw new Error('Association private company ceiling failed');
    } else {
      const companies = await companiesResponse.json();
      if(companiesResponse.status()!==200 || !Number.isInteger(companies.total) || !companies.items.length || companies.items.some(company=>'contacts' in company || 'service_phone' in company)) throw new Error('Company summary contract or privacy failed');
      const detailResponse = await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/companies/'+companies.items[0].id);
      const detail = await detailResponse.json();
      if(detailResponse.status()!==200 || !Array.isArray(detail.contacts) || !Array.isArray(detail.history)) throw new Error('Company private detail contract failed');
      const csv = await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/companies/export?q=production-smoke-no-matching-fixture');
      if(csv.status()!==200 || csv.headers()['cache-control']!=='no-store' || !(await csv.text()).includes('企业名称')) throw new Error('Company CSV boundary failed');
      await navigateWorkspace(page, username==='regulator'?'场站运营商管理':'企业资料');
      await expect(page.getByText(/CSV每次最多导出10000家/)).toBeVisible();
    }
    const ratingMenu = username === 'regulator' ? '企业申报查看' : username === 'association' ? '星级评价' : '星级申报';
    await navigateWorkspace(page, ratingMenu);
    await expect(page.getByRole('button',{name:'查询星级记录',exact:true})).toBeEnabled();
    await page.getByLabel('星级检索名称').fill('production-smoke-no-matching-fixture');
    await page.getByRole('button',{name:'查询星级记录',exact:true}).click();
    await expect(page.getByText('当前条件下没有星级记录',{exact:true})).toBeVisible();
    await expect(page.getByLabel('星级提交开始日期')).toBeVisible();
    if((await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/ratings/applications?submitted_from=2026-11-16&submitted_to=2026-11-15')).status()!==422) throw new Error('Production rating date range accepted');
    if((await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/ratings/applications/export?period_from=2026-11-01')).status()!==422) throw new Error('Rating silently ignored subsidy period');
    const refreshedRating = page.waitForResponse(response => new URL(response.url()).pathname==='/api/v1/ratings/applications');
    await page.getByRole('button',{name:'刷新数据',exact:true}).click();
    const refreshedResponse=await refreshedRating;
    if(refreshedResponse.status()!==200 || new URL(refreshedResponse.url()).searchParams.get('q')!=='production-smoke-no-matching-fixture') throw new Error('Global refresh lost applied role query');
    await expect(page.getByLabel('星级检索名称')).toHaveValue('production-smoke-no-matching-fixture');
    if(!identity.permissions.includes('ratings.export')) throw new Error('Missing rating export grant');
    for(const suffix of ['export','process/export']) {
      const report=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/ratings/applications/'+suffix+'?q=production-smoke-no-matching-fixture');
      if(report.status()!==200 || !(await report.text()).includes('当前场站名称')) throw new Error('Production rating CSV failed');
    }
    await expect(page.getByRole('button',{name:'导出申请清单',exact:true})).toBeVisible();
    const ratingFieldsResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/ratings/fields');
    const ratingFields=await ratingFieldsResponse.json();
    if(ratingFieldsResponse.status()!==200 || ratingFields.items.length!==4 || !ratingFields.items.some(field=>field.code==='equipment_count' && field.source==='runtime')) throw new Error('Rating configured field catalog failed');
    const standardsResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/ratings/standards');
    const standardsPage=await standardsResponse.json();
    const legacyStandard=standardsPage.items.find(standard=>standard.code==='DEMO-RATING' && standard.version===1);
    if(!legacyStandard || legacyStandard.activation_state!=='legacy_unknown' || legacyStandard.effective_on!==null || legacyStandard.rules.indicator_status_version!=null || legacyStandard.rules.indicators.some(indicator=>indicator.enabled!=null)) throw new Error('Legacy indicator dates were invented');
    await navigateWorkspace(page, username==='association'?'评价标准管理':'评价标准查看');
    await page.getByText(`查看指标配置 ${legacyStandard.name} V1`,{exact:true}).click();
    await expect(page.getByText(/整版生效：历史日期未留存/).filter({visible:true}).first()).toBeVisible();
    const standardDrafts=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/ratings/standard-drafts');
    const archivedStandards=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/ratings/standards?include_archived=TRUE');
    if(standardDrafts.status()!==(username==='association'?200:403) || archivedStandards.status()!==(username==='association'?200:403)) throw new Error('Standard draft manager role ceiling failed');
    if(username==='association') {
      const drafts=await standardDrafts.json();
      if(drafts.page_size!==20 || !Array.isArray(drafts.items)) throw new Error('Standard draft paging contract failed');
      await page.getByRole('button',{name:'配置新标准版本',exact:true}).click();
      const workbench=page.getByRole('region',{name:'标准草稿工作区',exact:true});
      await expect(workbench).toBeVisible();
      await expect(workbench.getByLabel('指标1状态',{exact:true})).toHaveValue('enabled');
      await expect(workbench.getByRole('button',{name:'预览参与指标与权重',exact:true})).toBeEnabled();
      await expect(workbench.getByRole('button',{name:'暂存标准草稿',exact:true})).toBeEnabled();
      await expect(workbench.getByRole('button',{name:'查询标准草稿',exact:true})).toBeEnabled();
      await page.getByRole('button',{name:'配置新标准版本',exact:true}).click();
    } else await expect(page.getByRole('region',{name:'标准草稿工作区',exact:true})).toHaveCount(0);
    const ratingPage=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/ratings/applications?page_size=1');
    if(ratingPage.status()!==200 || (await ratingPage.json()).page_size!==1) throw new Error('Production rating pagination failed');
    if(username === 'association') {
      if((await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/subsidy/claims')).status()!==403) throw new Error('Association reached financial queue');
      if((await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/subsidy/statistics?start_date=2026-01-01&end_date=2026-12-31')).status()!==403) throw new Error('Association reached subsidy statistics');
    } else {
      await navigateWorkspace(page, username==='regulator' ? '补贴申请记录' : '补贴申报');
      await expect(page.getByRole('button',{name:'查询补贴记录',exact:true})).toBeEnabled();
      await navigateWorkspace(page, '补贴统计');
      const subsidyStatistics=page.getByRole('region',{name:'补贴统计',exact:true});
      await expect(subsidyStatistics).toBeVisible();
      await expect(subsidyStatistics.getByRole('button',{name:'导出补贴统计CSV',exact:true})).toBeDisabled();
      const statisticsOptions=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/subsidy/statistics/operators');
      if(statisticsOptions.status()!==200 || !Array.isArray((await statisticsOptions.json()).items)) throw new Error('Subsidy scoped operator selector failed');
      const statisticsQuery='start_date=2026-01-01&end_date=2026-12-31&station_id=2147483647';
      const statisticsResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/subsidy/statistics?'+statisticsQuery);
      const statisticsResult=await statisticsResponse.json();
      if(statisticsResponse.status()!==200 || statisticsResult.total!==0 || statisticsResult.totals.claim_count!==0 || statisticsResponse.headers()['cache-control']!=='no-store') throw new Error('Subsidy aggregate/no-store contract failed');
      const statisticsCsv=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/subsidy/statistics/export?'+statisticsQuery);
      if(statisticsCsv.status()!==200 || statisticsCsv.headers()['cache-control']!=='no-store') throw new Error('Subsidy aggregate CSV failed');
      await navigateWorkspace(page, username==='regulator'?'电量数据':'设备电量数据');
      await page.getByText('设备日电量与核算证据',{exact:true}).click();
      const observationRegion=page.getByRole('region',{name:'设备日观测',exact:true});
      await expect(observationRegion).toBeVisible();
      await expect(observationRegion.getByText('选择条件后查询；未上报的设备或日期不按零值补齐。',{exact:true})).toBeVisible();
      await expect(observationRegion.getByLabel('设备日观测场站',{exact:true})).toHaveValue('');
      const observations=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/subsidy/device-days?start_date=2026-11-01&end_date=2026-11-03');
      const observationsPage=await observations.json();
      if(observations.status()!==200 || !Array.isArray(observationsPage.items) || observationsPage.items.some(row=>row.settlement_verified!==false)) throw new Error('Source device-day observation contract failed');
      const policyResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/subsidy/policies');
      const policyCatalog=await policyResponse.json();
      if(policyResponse.status()!==200 || !policyCatalog.items.length || policyCatalog.items.some(policy=>policy.device_rules===null ? !policy.claim_supported || policy.calculation_basis!=='legacy_station_energy' : policy.device_rules.configuration_version===2 ? !policy.claim_supported || policy.calculation_basis!=='demo_device_day_v2' : policy.claim_supported || policy.calculation_basis!=='device_rules_pending')) throw new Error('Subsidy policy calculation boundary contract failed');
      if(username==='regulator') {
        await navigateWorkspace(page, '补贴规则配置');
        await page.getByRole('button',{name:'新增政策版本',exact:true}).click();
        await expect(page.getByLabel('补贴规则口径',{exact:true})).toHaveValue('device');
        await expect(page.getByLabel('换电补贴标准（元/度）',{exact:true})).toBeVisible();
        await expect(page.getByLabel('5星系数',{exact:true})).toHaveValue('');
        await expect(page.getByLabel('故障率门槛（%）',{exact:true})).toBeVisible();
        await expect(page.getByLabel('补贴生成周期',{exact:true})).toHaveValue('day');
        await page.getByRole('button',{name:'新增政策版本',exact:true}).click();
      }
      const annualRegion=page.getByRole('region',{name:'年度多场站申报',exact:true});
      const annualChoices=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/subsidy/annual-stations?page_size=1');
      if(username==='operator_a') {
        const annualDefaults=await annualChoices.json();
        if(annualChoices.status()!==200 || annualDefaults.default_year!==annualDefaults.current_year-1) throw new Error('Annual production defaults failed');
        await page.getByRole('button',{name:'年度多站申报',exact:true}).click();
        await expect(annualRegion).toBeVisible();
        await expect(annualRegion.getByLabel('补贴年度',{exact:true})).toHaveValue(String(annualDefaults.default_year));
        await expect(annualRegion.getByRole('button',{name:'暂存年度草稿',exact:true})).toBeDisabled();
      } else {
        if(annualChoices.status()!==403) throw new Error('Regulator reached operator annual picker');
        await expect(annualRegion).toHaveCount(0);
      }
      await navigateWorkspace(page, '补贴申请记录');
      const batchRegion=page.getByRole('region',{name:'补贴批量审核',exact:true});
      if(username==='regulator') {
        await expect(batchRegion).toBeVisible();
        await expect(batchRegion).toContainText('已选0/100条');
        await expect(page.getByRole('button',{name:'批量通过补贴审核',exact:true})).toBeDisabled();
      } else await expect(batchRegion).toHaveCount(0);

      await page.getByLabel('补贴检索名称').fill('production-smoke-no-matching-fixture');
      await page.getByRole('button',{name:'查询补贴记录',exact:true}).click();
      await expect(page.getByText('当前条件下没有补贴记录',{exact:true})).toBeVisible();
      await expect(page.getByLabel('补贴提交开始日期')).toBeVisible();
      await expect(page.getByLabel('补贴周期开始日期')).toBeVisible();
      if((await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/subsidy/claims/export?submitted_to=2026-02-30')).status()!==422) throw new Error('Production subsidy date validation failed');
      if(!identity.permissions.includes('subsidy.export')) throw new Error('Missing subsidy export grant');
      for(const suffix of ['export','process/export']) {
        const report=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/subsidy/claims/'+suffix+'?q=production-smoke-no-matching-fixture');
        if(report.status()!==200 || !(await report.text()).includes('当前场站名称')) throw new Error('Production subsidy CSV failed');
      }
      const financialPage=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/subsidy/claims?page_size=1');
      if(financialPage.status()!==200 || (await financialPage.json()).page_size!==1) throw new Error('Production subsidy pagination failed');
    }
    if(username === 'association') {
      if((await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/inspections')).status()!==403) throw new Error('Association reached inspection queue');
    } else {
      await navigateWorkspace(page, username==='regulator' ? '巡检任务管理' : '整改任务');
      await expect(page.getByRole('button',{name:'查询巡检记录',exact:true})).toBeEnabled();
      await page.getByLabel('巡检检索名称').fill('production-smoke-no-matching-fixture');
      await page.getByLabel('巡检筛选逾期').selectOption('true');
      await page.getByRole('button',{name:'查询巡检记录',exact:true}).click();
      await expect(page.getByText('当前条件下没有巡检记录',{exact:true})).toBeVisible();
      const inspectionPage=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/inspections?page_size=1');
      if(inspectionPage.status()!==200) throw new Error('Production inspection request failed');
      const inspectionData=await inspectionPage.json();
      if(inspectionData.page_size!==1 || !inspectionData.query_at) throw new Error('Production inspection pagination failed');
    }
    if(username === 'association') {
      if((await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/hazards')).status()!==403) throw new Error('Association reached independent hazard supervision');
      await expect(page.getByRole('button',{name:username==='regulator'?'隐患督办管理':'隐患督办',exact:true})).toHaveCount(0);
    } else {
      await navigateWorkspace(page, username==='regulator'?'隐患督办管理':'隐患督办');
      const hazardRegion=page.getByRole('region',{name:'隐患督办工作区',exact:true});
      await expect(hazardRegion.getByLabel('督办整改进度筛选',{exact:true})).toBeVisible();
      await expect(hazardRegion.getByLabel('督办跟踪状态筛选',{exact:true})).toBeVisible();
      await hazardRegion.getByLabel('督办检索名称',{exact:true}).fill('production-smoke-no-matching-fixture');
      await hazardRegion.getByRole('button',{name:'查询督办',exact:true}).click();
      await expect(hazardRegion.getByText('当前查询没有隐患督办记录',{exact:true})).toBeVisible();
      const hazardPage=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/hazards?page_size=1');
      const hazardData=await hazardPage.json();
      if(hazardPage.status()!==200 || hazardPage.headers()['cache-control']!=='no-store' || hazardData.page_size!==1 || !hazardData.query_at) throw new Error('Independent hazard page contract failed');
      const hazardCsv=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/hazards/export?q=production-smoke-no-matching-fixture');
      if(hazardCsv.status()!==200 || hazardCsv.headers()['cache-control']!=='no-store' || !hazardCsv.headers()['content-type'].startsWith('text/csv') || !(await hazardCsv.text()).includes('整改进度,跟踪状态')) throw new Error('Independent hazard full-query export failed');
    }
    await navigateWorkspace(page, '监管协议');
    const protocolRegion=page.getByRole('region',{name:'监管协议阅读区',exact:true});
    await protocolRegion.getByLabel('监管协议检索',{exact:true}).fill('production-smoke-no-matching-fixture');
    await protocolRegion.getByRole('button',{name:'查询协议',exact:true}).click();
    await expect(protocolRegion.getByText('暂无已发布的监管协议',{exact:true})).toBeVisible();
    const protocolPage=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/protocols?q=production-smoke-no-matching-fixture');
    if(protocolPage.status()!==200 || protocolPage.headers()['cache-control']!=='no-store' || (await protocolPage.json()).total!==0) throw new Error('Protocol publication reader contract failed');
    await expect(protocolRegion.getByRole('button',{name:'管理协议内容',exact:true})).toHaveCount(username==='regulator'?1:0);
    if(username === 'association') {
      if((await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/alerts')).status()!==403) throw new Error('Association reached alerts');
      if((await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/alerts/configured-rules')).status()!==403) throw new Error('Association reached configured alerts');
      if((await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/alerts/configured-observations')).status()!==403) throw new Error('Association reached configured observations');
      if((await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/alerts/notifications/preferences')).status()!==403) throw new Error('Association reached private alert notification settings');
    } else {
      await navigateWorkspace(page, username==='regulator' ? '告警数据' : '企业告警');
      await expect(page.getByRole('button',{name:'查询告警记录',exact:true})).toBeEnabled();
      const notificationRegion=page.getByRole('region',{name:'告警通知配置',exact:true});
      if(username==='regulator') {
        if(!identity.permissions.includes('alerts.notifications')) throw new Error('Default regulator missing independent notification management capability');
        await page.getByRole('navigation',{name:'告警工作区'}).getByRole('button',{name:'通知设置',exact:true}).click();
        await notificationRegion.getByRole('button',{name:'管理通知与接收人',exact:true}).click();
        await expect(notificationRegion.getByRole('button',{name:'新增告警接收人',exact:true})).toBeEnabled();
        const notificationPrefs=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/alerts/notifications/preferences');
        const prefData=await notificationPrefs.json();
        if(notificationPrefs.status()!==200 || prefData.items.length!==6 || prefData.sms_mode!=='demo' || notificationPrefs.headers()['cache-control']!=='no-store') throw new Error('Private notification preference contract failed');
        const noContacts=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/alerts/notifications/recipients?q=production-smoke-no-matching-recipient');
        if(noContacts.status()!==200 || (await noContacts.json()).total!==0 || noContacts.headers()['cache-control']!=='no-store') throw new Error('Private notification recipient query failed');
        await notificationRegion.getByRole('button',{name:'收起通知配置',exact:true}).click();
      } else {
        await expect(notificationRegion).toHaveCount(0);
        for(const suffix of ['preferences','recipients','routings','deliveries']) {
          if((await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/alerts/notifications/'+suffix)).status()!==403) throw new Error('Operator reached private notification data');
        }
      }
      await page.getByRole('navigation',{name:'告警工作区'}).getByRole('button',{name:'规则配置',exact:true}).click();
      const configuredRegion=page.getByRole('region',{name:'分级与周期告警配置',exact:true});
      await expect(configuredRegion).toBeVisible();
      await page.getByRole('navigation',{name:'告警工作区'}).getByRole('button',{name:'来源与观测',exact:true}).click();
      const observationPanel=page.getByRole('region',{name:'独立分级告警观测',exact:true});
      await expect(observationPanel).toBeVisible();
      const observationResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/alerts/configured-observations?station_id=2147483647');
      if(observationResponse.status()!==200 || (await observationResponse.json()).total!==0 || observationResponse.headers()['cache-control']!=='no-store') throw new Error('Configured observation scoped read failed');
      const stationChoices=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/alerts/configured-observations/stations?page_size=1');
      if(stationChoices.status()!==200 || (await stationChoices.json()).page_size!==1) throw new Error('Alert station selector failed');
      if(username==='regulator') await expect(observationPanel.getByRole('button',{name:'提交独立演示观测',exact:true})).toBeDisabled();
      else await expect(observationPanel.getByRole('button',{name:'提交独立演示观测',exact:true})).toHaveCount(0);

      await page.getByRole('navigation',{name:'告警工作区'}).getByRole('button',{name:'规则配置',exact:true}).click();
      const configuredResponse=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/alerts/configured-rules?page_size=1');
      const configuredPage=await configuredResponse.json();
      if(configuredResponse.status()!==200 || configuredResponse.headers()['cache-control']!=='no-store' || !Array.isArray(configuredPage.items) || configuredPage.page_size!==1) throw new Error('Configured alert read contract failed');
      if(username==='regulator') {
        await configuredRegion.getByRole('button',{name:'新增分级规则',exact:true}).click();
        const configuredForm=configuredRegion.getByRole('form',{name:'分级规则表单',exact:true});
        await expect(configuredForm.getByLabel('分级general阈值',{exact:true})).toHaveValue('');
        await expect(configuredForm.getByRole('button',{name:'预览分级判断',exact:true})).toBeEnabled();
        await configuredForm.getByRole('button',{name:'取消新增分级规则',exact:true}).click();
      } else await expect(configuredRegion.getByRole('button',{name:'新增分级规则',exact:true})).toHaveCount(0);

      await page.getByRole('navigation',{name:'告警工作区'}).getByRole('button',{name:'事件跟踪',exact:true}).click();
      await page.getByLabel('告警检索名称').fill('production-smoke-no-matching-fixture');
      await page.getByLabel('告警筛选状态').selectOption('active');
      await page.getByLabel('告警筛选确认').selectOption('false');
      await page.getByRole('button',{name:'查询告警记录',exact:true}).click();
      await expect(page.getByText('当前条件下没有告警记录',{exact:true})).toBeVisible();
      await expect(page.getByLabel('告警筛选类型',{exact:true})).toBeVisible();
      await expect(page.getByRole('button',{name:'导出告警CSV',exact:true})).toBeVisible();
      const exportQuery=new URLSearchParams({q:'production-smoke-no-matching-fixture',category:'device_charging',start_at:'2026-11-01T00:00:00+08:00',end_at:'2026-12-01T00:00:00+08:00'});
      const eventExport=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/alerts/export?'+exportQuery);
      if(eventExport.status()!==200 || eventExport.headers()['cache-control']!=='no-store' || !(await eventExport.text()).includes('事件编号')) throw new Error('Alert CSV export contract failed');
      const periodFacts=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/alerts/period-facts?station_id=2147483647');
      if(periodFacts.status()!==200 || (await periodFacts.json()).total!==0 || periodFacts.headers()['cache-control']!=='no-store') throw new Error('Independent period source contract failed');
      const alertPage=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/alerts?page_size=1&status=active&acknowledged=false');
      if(alertPage.status()!==200 || (await alertPage.json()).page_size!==1) throw new Error('Production alert pagination failed');
    }
    await page.screenshot({path:`apps/web/test-results/stack-${username}.png`,fullPage:true});
    await page.getByRole('button',{name:'退出登录'}).click();
    await expect(page.getByRole('heading',{name:'登录工作台'})).toBeVisible();
    const smsOptions=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/auth/sms/options');
    if(smsOptions.status()!==200 || (await smsOptions.json()).sms_mode!=='demo') throw new Error('SMS public options contract failed');
    const staffOptions=await page.request.get(env.PUBLIC_ORIGIN+'/api/v1/auth/staff-enrollment/options');
    if(staffOptions.status()!==200 || !(await staffOptions.json()).enabled) throw new Error('Staff enrollment demo boundary failed');
    await page.goto(env.PUBLIC_ORIGIN+'/staff-register');
    await expect(page.getByRole('heading',{name:'工作人员账号申请',exact:true})).toBeVisible();
    await page.goto(env.PUBLIC_ORIGIN+'/recover');
    await expect(page.getByRole('heading',{name:'找回密码',exact:true})).toBeVisible();
    await expect(page.getByLabel('手机验证账号或手机号',{exact:true})).toBeVisible();
    await page.goto(env.PUBLIC_ORIGIN+'/sms-login');
    await expect(page.getByRole('heading',{name:'短信验证码登录',exact:true})).toBeVisible();

    await context.close();
    console.log(`${username}: standalone production UI, station list, audit permissions, application/subsidy/rating/inspection/alert queues and logout passed`);
  }
  const publicContext=await browser.newContext({viewport:{width:390,height:844}});
  try {
    const portal=await publicContext.newPage();
    const portalErrors=[];
    portal.on('pageerror',error=>portalErrors.push(error.message));
    await portal.goto(env.PUBLIC_ORIGIN+'/portal?q=production-smoke-no-matching-fixture');
    await expect(portal.getByRole('heading',{name:'充电资讯与公告',exact:true})).toBeVisible();
    await expect(portal.getByRole('heading',{name:'暂无公开资讯',exact:true})).toBeVisible();
    const publicResponse=await portal.request.get(env.PUBLIC_ORIGIN+'/api/v1/public/articles?q=production-smoke-no-matching-fixture');
    if(publicResponse.status()!==200 || publicResponse.headers()['cache-control']!=='no-store' || (await publicResponse.json()).total!==0) throw new Error('Anonymous public projection failed');
    if((await portal.request.get(env.PUBLIC_ORIGIN+'/api/v1/auth/me')).status()!==401) throw new Error('Anonymous portal unexpectedly acquired private identity');
    if(!await portal.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)) throw new Error('Public portal narrow layout overflow');
    await portal.getByRole('link',{name:'管理员登录',exact:true}).click();
    await expect(portal.getByRole('heading',{name:'选择您的业务入口',exact:true})).toBeVisible();
    await portal.getByRole('link',{name:/进入协会端登录/}).click();
    await expect(portal.getByRole('heading',{name:'登录工作台',exact:true})).toBeVisible();
    await portal.getByRole('link',{name:'查看公开资讯公告',exact:true}).click();
    await expect(portal.getByRole('heading',{name:'充电资讯与公告',exact:true})).toBeVisible();
    if(portalErrors.length) throw new Error('Public portal hydration failed');
    console.log('anonymous: public portal, omitted identity, narrow layout and unified login entry passed; no existing private content auto-published');
  } finally { await publicContext.close(); }
} finally { await browser.close(); }
