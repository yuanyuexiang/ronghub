"""Current authorized map observations; never infers missing historical facts."""

import csv
import io
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import Response

from app.config import settings
from app.query_contracts import MapStation
from app.routers.statistics import csv_cell
from app.station_metadata import OPTIONS

BUSINESS = {
    "pending": "待接入",
    "access_approved": "接入通过",
    "accepted": "验收通过",
    "operating": "运营中",
    "decommissioned": "已核销",
}
KINDS = {"charging": "充电站", "swapping": "换电站", "mixed": "充换电混合", "unknown": "类型未提供"}
RUNTIME = {
    "available": "有空闲接口",
    "charging": "充电中",
    "occupied": "接口占用",
    "offline": "全部快照离线",
    "fault": "有故障接口",
    "unknown": "快照不足 / 未知",
}
REASONS = {
    "missing_coordinates": "来源未提供坐标",
    "unsupported_coordinate_system": "坐标系尚未适配",
    "outside_projection": "超出当前投影视图范围",
}


def beijing(value):
    if value is None:
        return None
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        return value + "（历史时区未留存）"
    return stamp.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S%z")


def csv_response(values):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(
        [
            "场站编号",
            "场站名称",
            "运营商",
            "区域",
            "详细地址",
            "来源首次接入时间（北京时间）",
            "业务状态（投运/核销）",
            "场站种类",
            "用途",
            "建设场所",
            "站点运营状态",
            "来源同步状态",
            "场站功率kW",
            "台账接口数",
            "已观察接口数",
            "缺少快照接口数",
            "运行快照状态",
            "当前有效星级",
            "经度（来源原值）",
            "纬度（来源原值）",
            "来源坐标系",
            "未落点原因",
            "最近同步时间（北京时间）",
            "数据环境",
        ]
    )
    for value in values:
        item = MapStation.model_validate(value)
        writer.writerow(
            [
                csv_cell(field)
                for field in [
                    item.id,
                    item.name,
                    item.operator_name,
                    item.district,
                    item.address,
                    beijing(item.source_first_connected_at),
                    BUSINESS.get(item.status, item.status),
                    KINDS.get(item.station_type, item.station_type),
                    OPTIONS["usage_type"].get(item.usage_type, item.usage_type),
                    OPTIONS["construction_site"].get(
                        item.construction_site, item.construction_site
                    ),
                    OPTIONS["site_state"].get(item.site_state, item.site_state),
                    OPTIONS["source_sync_state"].get(
                        item.source_sync_state, item.source_sync_state
                    ),
                    item.power_kw,
                    item.connectors,
                    item.observed_connectors,
                    item.missing_connector_snapshots,
                    RUNTIME.get(item.runtime_state, item.runtime_state),
                    item.effective_stars,
                    item.longitude,
                    item.latitude,
                    item.coordinate_system,
                    REASONS.get(item.unplottable_reason, item.unplottable_reason),
                    beijing(item.synced_at),
                    "演示环境" if settings.demo_mode else "接口观测数据",
                ]
            ]
        )
    return Response(
        "\ufeff" + stream.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="ronghub-map-stations.csv"',
            "Cache-Control": "no-store",
        },
    )
