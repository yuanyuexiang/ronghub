"""Immutable indicator configuration and bounded, explicitly observed inventory evidence."""

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from sqlalchemy import func, select

from app.models import Connector, Equipment, Station, now
from app.rating_results import local_day

FieldName = Literal["expert_score", "station_power_kw", "equipment_count", "connector_count"]
Positive = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=6)]
FIELD_CATALOG = [
    {
        "code": "expert_score",
        "name": "专家/材料评分原值",
        "source": "manual",
        "unit": "分",
        "description": "人工输入0–100原值，按本指标分档映射；不是已验证平台数据",
    },
    {
        "code": "station_power_kw",
        "name": "当前台账场站总功率",
        "source": "runtime",
        "unit": "kW",
        "description": "授权场站当前台账字段，不代表历史功率或实时运行指标",
    },
    {
        "code": "equipment_count",
        "name": "当前库中设备数量",
        "source": "runtime",
        "unit": "台",
        "description": "当前设备记录数；零表示当前记录为零，源数据完整性尚未验证",
    },
    {
        "code": "connector_count",
        "name": "当前库中枪口数量",
        "source": "runtime",
        "unit": "个",
        "description": "当前枪口记录数，不采用场站自报终端数；源数据完整性尚未验证",
    },
]


class IndicatorBand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lower: Positive
    upper: Positive | None
    score: Positive
    label: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.upper is not None and self.upper <= self.lower:
            raise ValueError("阈值上界必须大于下界")
        return self


class IndicatorInput(BaseModel):
    enabled: StrictBool
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    weight: Decimal = Field(gt=0, max_digits=10, decimal_places=4)
    max_score: Decimal = Field(gt=0, max_digits=10, decimal_places=4)
    source: Literal["manual", "runtime"]
    category: str = Field(default="general", min_length=1, max_length=50)
    description: str = Field(min_length=1, max_length=2000)
    effective_from: date
    field_name: FieldName
    bands: list[IndicatorBand] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def valid_bands(self):
        if any(
            not value.strip() or "\x00" in value
            for value in [
                self.code,
                self.name,
                self.description,
                *[band.label for band in self.bands],
            ]
        ):
            raise ValueError("指标及档位文字须非空且不能包含NUL")
        if (self.field_name == "expert_score") != (self.source == "manual"):
            raise ValueError("人工字段和平台字段必须匹配数据来源")
        if self.bands[0].lower != 0 or self.bands[-1].upper is not None:
            raise ValueError("阈值须从0开始并覆盖至无上限；使用左闭右开区间")
        for index, band in enumerate(self.bands):
            if band.score > self.max_score:
                raise ValueError("档位得分不能超过指标满分")
            if index < len(self.bands) - 1 and band.upper != self.bands[index + 1].lower:
                raise ValueError("阈值必须有序连续，不能重叠、留空或中途无上限")
        return self


def active_indicators(rules):
    marker = rules.get("indicator_status_version")
    if marker is None:
        return rules["indicators"]
    if marker != 1 or any(type(item.get("enabled")) is not bool for item in rules["indicators"]):
        raise HTTPException(409, "指标状态版本未知或未完整，不能推断参与范围")
    active = [item for item in rules["indicators"] if item["enabled"]]
    if not active:
        raise HTTPException(422, "至少保留一个启用指标")
    return active


def weight_plan(rules):
    if rules.get("indicator_status_version") is None:
        return None
    active = active_indicators(rules)
    categories = rules.get("categories") or [{"code": "general", "name": "综合", "weight": "1"}]
    categorized = bool(rules.get("categories"))
    category_total = sum((Decimal(item["weight"]) for item in categories), Decimal(0))
    if category_total <= 0:
        raise HTTPException(422, "分类权重总和必须大于零")
    items, groups = [], []
    for category in categories:
        members = [
            item for item in active if not categorized or item.get("category") == category["code"]
        ]
        denominator = sum((Decimal(item["weight"]) for item in members), Decimal(0))
        if not members or denominator <= 0:
            raise HTTPException(422, "每个分类必须至少保留一个启用指标，不能静默移除分类权重")
        groups.append(
            {
                "code": category["code"],
                "name": category["name"],
                "weight": str(category["weight"]),
                "indicator_weight_sum": str(denominator),
            }
        )
        for item in rules["indicators"]:
            if categorized and item.get("category") != category["code"]:
                continue
            enabled = item["enabled"]
            percentage = (
                Decimal(item["weight"])
                / denominator
                * Decimal(category["weight"])
                / category_total
                * 100
                if enabled
                else Decimal(0)
            )
            items.append(
                {
                    "code": item["code"],
                    "name": item["name"],
                    "category": category["code"],
                    "enabled": enabled,
                    "configured_weight": str(item["weight"]),
                    "normalization_weight": str(denominator),
                    "effective_weight_percent": str(percentage),
                }
            )
    return {
        "items": items,
        "categories": groups,
        "excluded": [item["code"] for item in rules["indicators"] if not item["enabled"]],
        "basis": "仅启用指标参与分类内归一；分类权重不变，旧版本不重算",
    }


def capture(db, station_id, rules):
    if rules.get("configuration_version") != 2:
        return None  # Legacy facts stay unknown; no dates or evidence are backfilled.
    day = local_day()
    if any(
        date.fromisoformat(indicator["effective_from"]) > day
        for indicator in active_indicators(rules)
    ):
        raise HTTPException(409, "标准仍含尚未生效指标，请等待整版生效或选择其他标准")
    equipment = (
        select(func.count())
        .select_from(Equipment)
        .where(Equipment.station_id == station_id)
        .scalar_subquery()
    )
    connectors = (
        select(func.count())
        .select_from(Connector)
        .join(Equipment, Equipment.id == Connector.equipment_id)
        .where(Equipment.station_id == station_id)
        .scalar_subquery()
    )
    # A single PG statement gives all observed fields one MVCC snapshot.
    power, devices, guns = db.execute(
        select(Station.power_kw, equipment, connectors).where(Station.id == station_id)
    ).one()
    return {
        "evaluated_on": day.isoformat(),
        "captured_at": now().isoformat(),
        "station_id": station_id,
        "basis": "current_database_inventory_observation",
        "completeness_verified": False,
        "fields": {
            "station_power_kw": str(power) if power is not None else None,
            "equipment_count": str(devices),
            "connector_count": str(guns),
        },
    }


def reject_platform_overrides(rules, scores):
    if rules.get("configuration_version") == 2 and any(
        indicator["source"] == "runtime" and scores.get(indicator["code"]) is not None
        for indicator in rules["indicators"]
    ):
        raise HTTPException(422, "平台字段使用冻结观测，不允许人工覆盖")


def mapped_values(rules, scores):
    if rules.get("configuration_version") != 2:
        return scores, {}
    context = rules.get("evaluation_context") or {}
    values, evidence = {}, {}
    for indicator in rules["indicators"]:
        code = indicator["code"]
        if rules.get("indicator_status_version") == 1 and not indicator["enabled"]:
            values[code] = None
            continue
        raw = (
            scores.get(code)
            if indicator["source"] == "manual"
            else context.get("fields", {}).get(indicator["field_name"])
        )
        meta = {
            "field_name": indicator["field_name"],
            "effective_from": indicator["effective_from"],
            "raw_value": None,
            "band_label": None,
            "missing_reason": None,
        }
        if raw is None:
            values[code] = None
            meta["missing_reason"] = (
                "人工原值未填写"
                if indicator["source"] == "manual"
                else "缺少冻结平台观测，不以0或人工值补齐"
            )
        else:
            try:
                number = Decimal(str(raw))
            except (InvalidOperation, ValueError) as error:
                raise HTTPException(422, "字段原值必须为有限数值") from error
            if (
                not number.is_finite()
                or number < 0
                or (indicator["source"] == "manual" and number > 100)
            ):
                raise HTTPException(422, "字段原值超出范围；人工评分原值须为0–100")
            meta["raw_value"] = str(number)
            match = next(
                (
                    band
                    for band in indicator["bands"]
                    if number >= Decimal(band["lower"])
                    and (band["upper"] is None or number < Decimal(band["upper"]))
                ),
                None,
            )
            if match is None:
                values[code] = None
                meta["missing_reason"] = "原值未匹配档位，不能制造得分"
            else:
                values[code] = match["score"]
                meta["band_label"] = match["label"]
        evidence[code] = meta
    return values, evidence
