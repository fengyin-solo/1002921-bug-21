"""压力表检定收口规则的回归测试。

跑法：backend 目录下 python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest
from datetime import date

from app.services.gauge import (
    STATUS_DUE,
    STATUS_EXPIRING,
    STATUS_PASS,
    STATUS_STOPPED,
    GaugeService,
    add_months,
    validate_values,
)
from app.store import Store


def fresh_service() -> GaugeService:
    # store 是全局单例，测试前替换成只含 gauge 空表的仓库，避免种子数据干扰
    import app.services.gauge as gauge_module

    gauge_module.store = Store()
    gauge_module.store._tables.clear()
    gauge_module.store._tables["gauge"] = []
    return gauge_module.GaugeService()


def make_gauge(service: GaugeService, **overrides):
    values = {
        "压力表编号": "GAUG-T",
        "所属设备": "测试设备",
        "量程范围": "0~1.6 MPa",
        "精度等级": "1.6级",
        "检定周期": 6,
    }
    values.update(overrides)
    entry, errors = service.create_entry(values)
    assert not errors, errors
    return entry


class ValidateTests(unittest.TestCase):
    def test_missing_range_rejected(self) -> None:
        errors = validate_values({"压力表编号": "G", "所属设备": "D", "量程范围": "", "精度等级": "1.6"})
        self.assertTrue(any("缺少必填字段：量程范围" in e for e in errors))

    def test_range_upper_limit(self) -> None:
        errors = validate_values({"压力表编号": "G", "所属设备": "D", "量程范围": "0~999 MPa", "精度等级": "1.6"})
        self.assertEqual(len(errors), 1)
        self.assertIn("量程范围", errors[0])
        self.assertIn("超出允许范围", errors[0])

    def test_range_unparseable_points_out_the_item(self) -> None:
        errors = validate_values({"压力表编号": "G", "所属设备": "D", "量程范围": "没填", "精度等级": "1.6"})
        self.assertIn("量程范围", errors[0])
        self.assertIn("无法识别", errors[0])

    def test_grade_out_of_allowed_set(self) -> None:
        errors = validate_values({"压力表编号": "G", "所属设备": "D", "量程范围": "0~1.6 MPa", "精度等级": "5.0级"})
        self.assertTrue(any("精度等级" in e for e in errors))

    def test_both_violations_reported(self) -> None:
        errors = validate_values({"压力表编号": "G", "所属设备": "D", "量程范围": "0~999 MPa", "精度等级": "5.0级"})
        self.assertEqual(len(errors), 2)

    def test_cycle_must_be_whole_month(self) -> None:
        base = {"压力表编号": "G", "所属设备": "D", "量程范围": "0~1.6 MPa", "精度等级": "1.6"}
        errors = validate_values({**base, "检定周期": "36"})
        self.assertTrue(any("检定周期" in e for e in errors))
        self.assertFalse(validate_values({**base, "检定周期": "12"}))


class NextDateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = fresh_service()

    def test_next_date_equals_last_date_plus_cycle(self) -> None:
        entry = make_gauge(self.service, 检定日期="2026-01-31", 检定周期=1, 检定结论="合格")
        self.assertEqual(entry["下次检定日"], "2026-02-28")

    def test_next_date_recomputed_on_publish(self) -> None:
        entry = make_gauge(self.service, 检定日期="2026-01-10", 检定周期=6, 检定结论="合格")
        self.assertEqual(entry["下次检定日"], "2026-07-10")
        updated, _ = self.service.run_action(entry["id"], "登记合格", {"检定日期": "2026-03-10", "检定周期": "12"})
        self.assertIsNotNone(updated)
        self.assertEqual(updated["下次检定日"], "2027-03-10")

    def test_add_months_clamps_to_month_end(self) -> None:
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))


class StatusAndScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = fresh_service()
        self.today = date.today()

    def _inspect_date(self, months_ago: int) -> str:
        d = add_months(self.today, -months_ago)
        return d.isoformat()

    def test_expiring_window(self) -> None:
        from datetime import timedelta

        # 到期日取 15 天后（落在 30 天窗口内），检定日 = 到期日 - 6 个月
        due = self.today + timedelta(days=15)
        inspect_day = add_months(due, -6)
        entry = make_gauge(self.service, 检定日期=inspect_day.isoformat(), 检定结论="合格")
        self.assertEqual(entry["status"], STATUS_EXPIRING)

    def test_overdue_is_due(self) -> None:
        entry = make_gauge(self.service, 检定日期=self._inspect_date(12), 检定结论="合格")
        self.assertEqual(entry["status"], STATUS_DUE)

    def test_never_inspected_is_due(self) -> None:
        entry = make_gauge(self.service)
        self.assertEqual(entry["status"], STATUS_DUE)
        self.assertEqual(entry["检定结论"], "未检定")

    def test_stopped_excluded_from_scheduling(self) -> None:
        stopped = make_gauge(self.service, 压力表编号="GAUG-S", 检定日期=self._inspect_date(12))
        self.service.run_action(stopped["id"], "办理停用", {})
        active_overdue = make_gauge(self.service, 压力表编号="GAUG-O", 检定日期=self._inspect_date(12))
        overview = self.service.scheduling_overview()
        ids = [row["压力表编号"] for row in overview["items"]]
        self.assertIn("GAUG-O", ids)
        self.assertNotIn("GAUG-S", ids)
        self.assertEqual(overview["已停用"], 1)

    def test_stopped_rejects_schedule_and_publish(self) -> None:
        stopped = make_gauge(self.service, 压力表编号="GAUG-S")
        self.service.run_action(stopped["id"], "办理停用", {})
        entry, message = self.service.run_action(stopped["id"], "安排检定", {})
        self.assertIsNone(entry)
        self.assertIn("停用", message)
        entry, message = self.service.run_action(stopped["id"], "登记合格", {})
        self.assertIsNone(entry)
        self.assertIn("停用", message)


class PublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = fresh_service()

    def test_latest_publication_wins_everywhere(self) -> None:
        entry = make_gauge(self.service, 检定日期="2026-03-01", 检定结论="合格")
        self.service.run_action(entry["id"], "登记不合格", {"检定日期": "2026-06-01"})
        updated, _ = self.service.run_action(entry["id"], "登记合格", {"检定日期": "2026-09-01"})
        # 列表口径
        listed, _ = self.service.list_entries(page=1, size=100)
        row = next(item for item in listed if item["id"] == entry["id"])
        self.assertEqual(row["检定结论"], "合格")
        self.assertEqual(row["status"], STATUS_PASS)
        # 详情口径一致
        detail = self.service.get_entry(entry["id"])
        self.assertEqual(detail["检定结论"], "合格")
        self.assertEqual(detail["publications"][-1]["检定结论"], "合格")
        self.assertEqual(len(detail["publications"]), 3)

    def test_fail_conclusion_keeps_due(self) -> None:
        entry = make_gauge(self.service, 检定日期="2026-09-01", 检定结论="合格")
        updated, _ = self.service.run_action(entry["id"], "登记不合格", {"检定日期": "2026-09-15"})
        self.assertEqual(updated["检定结论"], "不合格")
        self.assertEqual(updated["status"], STATUS_DUE)


class RejudgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = fresh_service()

    def test_rejudge_recomputes_all_history(self) -> None:
        entry = make_gauge(self.service, 检定日期="2020-01-10", 检定周期=6, 检定结论="合格")
        # 手工把历史数据改回旧口径下的“合格”状态，模拟规则变更前的存量数据
        entry["status"] = STATUS_PASS
        entry["下次检定日"] = "人工填写的旧日期"
        summary = self.service.rejudge_all()
        self.assertEqual(summary["total"], 1)
        judged = self.service.get_entry(entry["id"])
        self.assertEqual(judged["status"], STATUS_DUE)
        self.assertEqual(judged["下次检定日"], "2020-07-10")

    def test_dirty_history_flagged_not_dropped(self) -> None:
        entry = make_gauge(self.service)
        entry["量程范围"] = "未知"
        entry["精度等级"] = "9.0级"
        summary = self.service.rejudge_all()
        self.assertEqual(summary["规则异常"], 1)
        judged = self.service.get_entry(entry["id"])
        self.assertTrue(judged["abnormal"])
        self.assertEqual(len(judged["violations"]), 2)
        # 异常数据仍在表里，状态收敛为待检定
        self.assertEqual(judged["status"], STATUS_DUE)


if __name__ == "__main__":
    unittest.main()
