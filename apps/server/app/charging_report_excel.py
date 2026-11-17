"""Server report export. Exact API decimal values are never coerced beyond Excel precision."""

import io
import math
from datetime import date, datetime, time
from decimal import Decimal, localcontext

import xlsxwriter

from app.charging_report import ChargingReport

MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def workbook(report: ChargingReport):
    payload = io.BytesIO()
    with xlsxwriter.Workbook(
        payload, {"in_memory": True, "strings_to_formulas": False, "strings_to_urls": False}
    ) as book:
        book.set_properties({"title": "RongHub充电与补贴数据统计", "author": "RongHub"})
        title = book.add_format(
            {"font_name": "Microsoft YaHei", "font_size": 18, "font_color": "#173957", "bold": True}
        )
        note = book.add_format(
            {
                "font_name": "Microsoft YaHei",
                "font_size": 10,
                "font_color": "#65788F",
                "text_wrap": True,
                "valign": "top",
            }
        )
        header = book.add_format(
            {
                "font_name": "Microsoft YaHei",
                "font_size": 10,
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": "#234E75",
                "text_wrap": True,
                "valign": "vcenter",
            }
        )
        text = book.add_format(
            {
                "font_name": "Microsoft YaHei",
                "font_size": 10,
                "valign": "top",
                "num_format": "@",
                "text_wrap": True,
            }
        )
        number = book.add_format(
            {
                "font_name": "Microsoft YaHei",
                "font_size": 10,
                "num_format": "#,##0.000000",
                "valign": "top",
            }
        )
        integer = book.add_format(
            {
                "font_name": "Microsoft YaHei",
                "font_size": 10,
                "num_format": "#,##0",
                "valign": "top",
            }
        )
        money = book.add_format(
            {
                "font_name": "Microsoft YaHei",
                "font_size": 10,
                "num_format": "#,##0.00",
                "valign": "top",
            }
        )
        date_format = book.add_format(
            {"font_name": "Microsoft YaHei", "font_size": 10, "num_format": "yyyy-mm-dd"}
        )
        missing = book.add_format(
            {
                "font_name": "Microsoft YaHei",
                "font_size": 10,
                "font_color": "#8A734E",
                "align": "right",
            }
        )

        def table(name, labels, rows):
            sheet = book.add_worksheet(name)
            sheet.hide_gridlines(2)
            sheet.merge_range(0, 0, 1, len(labels) - 1, "充电与补贴数据统计", title)
            sheet.merge_range(
                2,
                0,
                2,
                len(labels) - 1,
                f"{report.start_date} 至 {report.end_date}（北京）　{'场站' if report.dimension == 'station' else '电桩'}汇总　场站筛选：{report.q or '全部授权'}",
                note,
            )
            sheet.merge_range(
                3,
                0,
                5,
                len(labels) - 1,
                report.notice + " Excel超过15位有效数字的数值以精确文本保留。",
                note,
            )
            for col, label in enumerate(labels):
                sheet.write_string(7, col, label, header)
                sheet.set_column(col, col, 22 if "名称" in label or "说明" in label else 24)
            sheet.set_row(0, 28)
            sheet.set_row(1, 20)
            sheet.set_row(2, 80 if len(report.q) > 50 else 32)
            if len(labels) == 4:
                sheet.set_row(3, 65)
                sheet.set_row(4, 20)
                sheet.set_row(5, 20)
            sheet.set_row(7, 42)
            for index, values in enumerate(rows, 8):
                max_units = max(
                    (
                        sum(2 if ord(char) > 127 else 1 for char in str(value))
                        for value in values
                        if value is not None
                    ),
                    default=0,
                )
                sheet.set_row(index, max(20, min(210, math.ceil(max_units / 18) * 15)))
                for col, value in enumerate(values):
                    if value is None:
                        sheet.write_string(index, col, "暂无数据", missing)
                    elif isinstance(value, date):
                        sheet.write_datetime(
                            index, col, datetime.combine(value, time.min), date_format
                        )
                    elif isinstance(value, Decimal):
                        if not value.is_finite():
                            raise ValueError("Nonfinite report decimal")
                        if len(value.normalize().as_tuple().digits) <= 15:
                            sheet.write_number(
                                index,
                                col,
                                float(value),
                                money
                                if "元" in labels[col]
                                or "金额" in labels[col]
                                or (col == 2 and values[-1] == "元")
                                else number,
                            )
                        else:
                            sheet.write_string(index, col, format(value, "f"), text)
                    elif isinstance(value, int):
                        sheet.write_number(index, col, value, integer)
                    else:
                        sheet.write_string(index, col, str(value), text)
            sheet.freeze_panes(8, min(3, len(labels)))
            sheet.autofilter(7, 0, max(7, 7 + len(rows)), len(labels) - 1)
            sheet.set_landscape()
            sheet.repeat_rows(7)
            sheet.set_zoom(90)
            return sheet

        def decimal(value):
            return Decimal(value) if value is not None else None

        def hours(value):
            with localcontext() as context:
                context.prec = 50
                return (
                    (Decimal(value) / 3600).quantize(Decimal("0.000001"))
                    if value is not None
                    else None
                )

        summary = [
            ["当前台账", "授权场站数", report.selected_station_count, "站"],
            ["当前台账", "设备记录数", report.current_equipment_count, "台"],
            ["补贴范围", "跨周期未计入申请", report.cross_period_claim_count, "份"],
            ["补贴范围", "未提供设备分摊的旧申请", report.legacy_claim_count, "份"],
        ]
        for stream in report.orders:
            identity = stream.source + ("（演示）" if stream.is_demo else "（源报）")
            summary.extend(
                [
                    [identity, "观测订单", stream.observed_orders, "单"],
                    [identity, "已完成订单", stream.completed_orders, "单"],
                    [identity, "已知完成订单电量", decimal(stream.kwh.value), "kWh"],
                    [identity, "已知源报时长", hours(stream.duration_seconds.value), "小时"],
                    [identity, "已知源报消费", decimal(stream.amount.value), "元"],
                ]
            )
        for stream in report.subsidies:
            identity = "补贴" + ("（演示）" if stream.is_demo else "（登记）")
            summary.extend(
                [
                    [identity, "批准或登记发放申请", stream.claim_count, "份"],
                    [identity, "系数前汇算", decimal(stream.base_amount), "元"],
                    [identity, "调整后核算金额", decimal(stream.adjusted_amount), "元"],
                    [
                        identity,
                        "登记发放金额（非银行到账）",
                        decimal(stream.disbursed_amount),
                        "元",
                    ],
                ]
            )
        table("统计汇总", ["数据类别", "指标", "数值", "单位"], summary)
        charging = []
        subsidy = []
        for item in report.items:
            identity = [
                str(item.station_id),
                item.station_name,
                item.operator_name,
                item.district,
                str(item.equipment_id) if item.equipment_id else None,
                item.equipment_external_id,
                item.equipment_name,
                "未匹配源桩引用"
                if item.kind == "source_reference"
                else "场站"
                if item.kind == "station"
                else "台账设备",
            ]
            if not item.orders:
                charging.append([*identity, None, "无订单观测", *([None] * 8)])
            for stream in item.orders:
                charging.append(
                    [
                        *identity,
                        stream.source,
                        "演示" if stream.is_demo else "源报",
                        stream.observed_orders,
                        stream.completed_orders,
                        decimal(stream.kwh.value),
                        stream.kwh.missing_orders,
                        hours(stream.duration_seconds.value),
                        stream.duration_seconds.missing_orders,
                        decimal(stream.amount.value),
                        stream.amount.missing_orders,
                    ]
                )
            for stream in item.subsidies:
                subsidy.append(
                    [
                        *identity,
                        "演示" if stream.is_demo else "登记",
                        stream.claim_count,
                        decimal(stream.base_amount),
                        decimal(stream.adjusted_amount),
                        stream.disbursed_claim_count,
                        decimal(stream.disbursed_amount),
                    ]
                )
        common = [
            "场站编号",
            "场站名称",
            "运营商名称",
            "当前区域",
            "设备内部编号",
            "源桩编码",
            "设备名称",
            "身份类型",
        ]
        table(
            "充电业务",
            [
                *common,
                "订单来源",
                "数据环境",
                "观测订单数",
                "已完成数",
                "已知完成电量(kWh)",
                "缺电量完成订单数",
                "已知源报时长(小时)",
                "缺时长完成订单数",
                "已知源报消费(元)",
                "缺消费完成订单数",
            ],
            charging,
        )
        table(
            "补贴核算",
            [
                *common,
                "数据环境",
                "申请数",
                "系数前汇算(元)",
                "调整后核算(元)",
                "登记发放申请数",
                "登记发放金额(非银行到账)",
            ],
            subsidy,
        )
        if report.dimension == "equipment":
            station_rows = [
                [
                    str(item.station_id),
                    item.station_name,
                    item.operator_name,
                    "演示" if stream.is_demo else "登记",
                    stream.claim_count,
                    decimal(stream.base_amount),
                    decimal(stream.adjusted_amount),
                    stream.disbursed_claim_count,
                    decimal(stream.disbursed_amount),
                ]
                for item in report.station_subsidies
                for stream in item.streams
            ]
            table(
                "场站补贴",
                [
                    "场站编号",
                    "场站名称",
                    "运营商名称",
                    "数据环境",
                    "申请数",
                    "系数前汇算(元)",
                    "调整后核算(元)",
                    "登记发放申请数",
                    "登记发放金额(非银行到账)",
                ],
                station_rows,
            )
    return payload.getvalue()
