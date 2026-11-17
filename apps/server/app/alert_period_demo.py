"""Explicit synthetic historical cohorts, independent of other demo measurements."""

from datetime import datetime, time, timedelta
from decimal import Decimal

from app.alert_period_contracts import BEIJING


def records(operator_id):
    today = datetime.now(BEIJING).date()
    week_end = today - timedelta(days=today.weekday())
    month_end = today.replace(day=1)
    periods = [
        ("natural_week", week_end - timedelta(days=7), week_end),
        ("natural_month", (month_end - timedelta(days=1)).replace(day=1), month_end),
    ]
    result = []
    for period, start, end in periods:
        seconds = Decimal((end - start).days * 86400)
        for metric, percent in [
            ("station_utilization", 25),
            ("station_fault", 6),
            ("station_offline", 12),
        ]:
            result.append(
                {
                    "external_id": f"demo-alert-{operator_id}-{metric}-{period}-{start}",
                    "metric": metric,
                    "period": period,
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                    "basis": "source_declared_equipment_service_seconds_v1",
                    "equipment_external_ids": ["equipment-1"],
                    "members_complete": True,
                    "members": [
                        {
                            "equipment_external_id": "equipment-1",
                            "equipment_name": "演示历史设备集合·桩1",
                            "expected_seconds": str(seconds),
                            "observed_seconds": str(seconds),
                            "unknown_seconds": "0",
                            "affected_seconds": str(seconds * percent / 100),
                        }
                    ],
                    "source_updated_at": datetime.combine(end, time.min, BEIJING).isoformat(),
                }
            )
    return result
