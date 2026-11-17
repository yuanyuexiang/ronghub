"""Prototype classifications are distinct from workflow state and connector runtime snapshots."""

from datetime import datetime

from app import location

OPTIONS = {
    "usage_type": {
        "public": "公共",
        "personal": "个人",
        "bus": "公交专用",
        "sanitation": "环卫专用",
        "logistics": "物流专用",
        "taxi": "出租车专用",
        "car_sharing": "分时租赁专用",
        "community_shared": "小区共享",
        "workplace": "单位专用",
        "private_shared": "私人共享桩",
        "other": "其他",
        "unknown": "用途未提供",
    },
    "construction_site": {
        "public_institution": "公共机构",
        "residential": "居民区",
        "enterprise": "企事业单位",
        "office": "写字楼",
        "industrial": "工业园区",
        "transit": "交通枢纽",
        "culture_sports": "大型文体设施",
        "highway": "城际高速服务区",
        "green_space": "城市绿地",
        "roadside": "路边停车位",
        "building_parking": "大型建筑配建停车场",
        "scenic_area": "风景区",
        "bus_depot": "公交场站",
        "fuel_station": "加油加气站",
        "taxi_site": "出租车场所",
        "other": "其他有充电条件场地",
        "unknown": "建设场所未提供",
    },
    "site_state": {
        "constructing": "建设中",
        "offline": "关闭下线",
        "maintenance": "维护中",
        "normal": "正常使用",
        "unknown": "站点运营状态未知",
    },
    "source_sync_state": {
        "active": "来源正常同步",
        "removed": "来源已下架",
        "unknown": "同步状态未提供",
    },
}


def values(record):
    from app.station_facilities import values as facility_values

    result = {**location.values(record), **facility_values(record)}
    for key, options in OPTIONS.items():
        if key in record:
            if record[key] not in options:
                raise ValueError("Unknown station classification")
            result[key] = record[key]
    for key in ["source_first_connected_at", "source_first_reported_at"]:
        if key not in record:
            continue
        value = record[key]
        if value is not None:
            if not isinstance(value, datetime):
                try:
                    value = datetime.fromisoformat(value)
                except (TypeError, ValueError) as error:
                    raise ValueError("Invalid source timestamp") from error
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Source timestamp requires timezone")
        result[key] = value
    return result
