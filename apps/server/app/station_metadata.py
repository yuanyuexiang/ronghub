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
    result = location.values(record)
    for key, options in OPTIONS.items():
        if key in record:
            if record[key] not in options:
                raise ValueError("Unknown station classification")
            result[key] = record[key]
    if "source_first_connected_at" in record:
        value = record["source_first_connected_at"]
        if value is not None:
            if not isinstance(value, datetime):
                try:
                    value = datetime.fromisoformat(value)
                except (TypeError, ValueError) as error:
                    raise ValueError("Invalid first-connection timestamp") from error
            if value.tzinfo is None:
                raise ValueError("First-connection timestamp requires timezone")
        result["source_first_connected_at"] = value
    return result
