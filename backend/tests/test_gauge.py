"""压力表统一判定的回归测试。

只依赖标准库，直接 python3 -m unittest discover -s backend/tests 即可运行。
覆盖六条收口规则：
1. 量程/精度越界不许保存，并逐项指出越界项；
2. 下次检定日一律按「上次检定日期 + 检定周期」计算；
3. 已停用的表不进排期；
4. 结论冲突时以最后发布的检定为准；
5. 判定标准改过后，历史数据按新规则重判；
6. 列表与详情拿到的是同一份判定结论。
"""
from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from app.services import gauge_rules as rules
from app.services.gauge import GaugeService
from app.store import Store

TODAY = date(2026, 10, 2)


def fresh_service() -> GaugeService:
    # 每个用例用全新的内存仓库，避免种子数据与用例之间互相污染。
    store = Store()
    service = GaugeService()
    return service


def make_entry(
    store_rows: list,
    *,
    entry_id: int = 1,
    code: str = "GAUG-T1",
    scale: str = "0~1.6 MPa",
    grade: str = "1.6",
    cycle: int = 6,
    stopped: bool = False,
    records: list | None = None,
) -> dict:
    row = {
        "id": entry_id,
        "停用": stopped,
        "压力表编号": code,
        "所属设备": "测试设备",
        "量程范围": scale,
        "精度等级": grade,
        "检定周期": cycle,
        "检定记录": records or [],
    }
    store_rows.append(row)
    return row


class GaugeRulesTests(unittest.TestCase):
    def test_range_validation(self):
        ok, err = rules.parse_range("0~1.6 MPa")
        self.assertEqual(ok, (0.0, 1.6))
        self.assertIsNone(err)

        for bad, fragment in [
            ("", "未填写"),
            ("abc", "格式无法识别"),
            ("5~3", "下限必须小于上限"),
            ("0~101", "超出允许范围"),
            ("-1~1", "超出允许范围"),
        ]:
            with self.subTest(bad=bad):
                _, error = rules.parse_range(bad)
                self.assertIn(fragment, error)

    def test_accuracy_validation(self):
        for value, normalized in [("1.6", "1.6"), ("2.5级", "2.5"),("4．0", "4.0")]:
            grade, err = rules.parse_accuracy_grade(value)
            self.assertEqual(grade, normalized)
            self.assertIsNone(err)

        _, err = rules.parse_accuracy_grade("5.0")
        self.assertIn("超出允许范围", err)
        self.assertIn("精度等级", err)

    def test_next_date_by_last_date_plus_cycle(self):
        self.assertEqual(rules.next_calibration_date(date(2026, 3, 31), 1), "2026-04-30")
        self.assertEqual(rules.next_calibration_date(date(2026, 10, 2), 6), "2027-04-02")
        self.assertIsNone(rules.next_calibration_date(None, 6))


class GaugeServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = fresh_service()
        self.rows = Store().rows  # 不用这个，service 用全局 store；下方直接注入全局 store
        from app.store import store
        store._tables["gauge"] = []
        self.store = store

    def _entry(self, **kwargs):
        return make_entry(self.store.rows("gauge"), **kwargs)

    def test_create_blocks_out_of_range_and_grade(self):
        entry, errors = self.service.create_entry({
            "压力表编号": "GAUG-B1", "所属设备": "d", "量程范围": "", "精度等级": "",
        })
        self.assertIsNone(entry)
        self.assertTrue(any("缺少必填字段：量程范围" in e for e in errors), errors)
        self.assertTrue(any("缺少必填字段：精度等级" in e for e in errors), errors)

        _, errors = self.service.create_entry({
            "压力表编号": "GAUG-B2", "所属设备": "d",
            "量程范围": "0~200 MPa", "精度等级": "9.9",
        })
        self.assertEqual(len(errors), 2, errors)
        self.assertTrue(any("量程范围" in e and "超出允许范围" in e for e in errors))
        self.assertTrue(any("精度等级" in e and "超出允许范围" in e for e in errors))

        entry, errors = self.service.create_entry({
            "压力表编号": "GAUG-OK", "所属设备": "d",
            "量程范围": "0~1.6 MPa", "精度等级": "1.6",
        })
        self.assertEqual(errors, [])
        self.assertEqual(entry["status"], rules.STATUS_DUE)
        self.assertTrue(entry["可排期"])

    def test_next_date_derived_only_from_last_verdict_and_cycle(self):
        self._entry(records=[
            {"seq": 1, "检定日期": "2026-01-31", "检定周期": 6, "检定结论": "合格", "规则版本": 1},
        ])
        entry = self.service.get_entry(1, today=TODAY)
        # 31 日加 6 个月落到 7 月 31 日，不接受手填的下次检定日。
        self.assertEqual(entry["下次检定日"], "2026-07-31")
        self.assertEqual(entry["status"], rules.STATUS_DUE)
        self.assertNotIn("下次检定日", self.store.rows("gauge")[0])

    def test_stopped_gauge_excluded_from_schedule(self):
        # 停用且早已过期：仍不允许进入排期。
        self._entry(
            entry_id=1, code="GAUG-S1", stopped=True,
            records=[{"seq": 1, "检定日期": "2024-01-01", "检定周期": 6, "检定结论": "合格", "规则版本": 1}],
        )
        self._entry(
            entry_id=2, code="GAUG-S2", stopped=False,
            records=[{"seq": 1, "检定日期": "2024-01-01", "检定周期": 6, "检定结论": "合格", "规则版本": 1}],
        )
        scheduled = [row["压力表编号"] for row in self.service.schedule(today=TODAY)]
        self.assertEqual(scheduled, ["GAUG-S2"])

        entry, message = self.service.run_action(1, "安排检定", {}, today=TODAY)
        self.assertIsNone(entry)
        self.assertIn("已停用", message)

        # 停用动作执行后立刻从排期消失。
        self.service.run_action(2, "办理停用", {}, today=TODAY)
        self.assertEqual(self.service.schedule(today=TODAY), [])

    def test_last_published_verdict_wins(self):
        self._entry(records=[{"seq": 1, "检定日期": "2026-09-01", "检定周期": 6, "检定结论": "合格", "规则版本": 1}])
        entry, msg = self.service.run_action(1, "登记不合格", {"检定日期": "2026-09-15"}, today=TODAY)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["检定结论"], "检定不合格")
        self.assertEqual(entry["status"], rules.STATUS_DUE)

        entry, _ = self.service.run_action(1, "登记合格", {"检定日期": "2026-10-01"}, today=TODAY)
        self.assertEqual(entry["检定结论"], "检定合格")
        self.assertEqual(entry["status"], rules.STATUS_QUALIFIED)
        self.assertEqual(entry["下次检定日"], "2027-04-01")
        self.assertIn("最后发布的第 3 次检定", entry["判定说明"])

        # 缺检定日期不允许发布结论。
        entry, msg = self.service.run_action(1, "登记合格", {}, today=TODAY)
        self.assertIsNone(entry)
        self.assertIn("检定日期", msg)

    def test_cannot_publish_pass_when_spec_out_of_range(self):
        self._entry(code="GAUG-BAD", grade="5.0")
        entry, msg = self.service.run_action(1, "登记合格", {"检定日期": "2026-10-01"}, today=TODAY)
        self.assertIsNone(entry)
        self.assertIn("精度等级", msg)

    @patch.object(rules, "RULE_VERSION", 2)
    def test_rejudge_history_under_new_rules(self):
        with patch.object(rules, "ALLOWED_ACCURACY_GRADES", {"0.25", "0.4", "0.6", "1.0", "1.6", "4.0"}):
            # 2.5 级在旧规则下合格，规则收紧后必须重判为不合规。
            row = self._entry(
                code="GAUG-R1", grade="2.5",
                records=[{"seq": 1, "检定日期": "2026-09-20", "检定周期": 6, "检定结论": "合格", "规则版本": 1}],
            )
            row["status"] = "检定合格"  # 模拟旧规则落库的状态
            row["检定结论"] = "检定合格"

            presented = self.service.get_entry(1, today=TODAY)
            self.assertEqual(presented["status"], rules.STATUS_NONCOMPLIANT)
            self.assertFalse(presented["可排期"])

            report = self.service.rejudge(today=TODAY)
            self.assertEqual(report["规则版本"], 2)
            changed = [c for c in report["变更明细"] if c["压力表编号"] == "GAUG-R1"]
            self.assertEqual(len(changed), 1)
            self.assertEqual(changed[0]["after"]["status"], rules.STATUS_NONCOMPLIANT)

    def test_list_and_detail_share_one_judgment(self):
        self._entry(records=[{"seq": 1, "检定日期": "2026-09-25", "检定周期": 6, "检定结论": "合格", "规则版本": 1}])
        listed, total = self.service.list_entries(today=TODAY)
        detail = self.service.get_entry(1, today=TODAY)
        self.assertEqual(total, 1)
        for key in ("status", "检定结论", "下次检定日", "判定说明", "规则版本"):
            self.assertEqual(listed[0][key], detail[key], key)

    def test_expiring_soon_window(self):
        # 检定日 2026-04-10 加 6 个月为 2026-10-10，距今 8 天（30 天预警窗口内）-> 即将到期。
        self._entry(records=[{"seq": 1, "检定日期": "2026-04-10", "检定周期": 6, "检定结论": "合格", "规则版本": 1}])
        entry = self.service.get_entry(1, today=TODAY)
        self.assertEqual(entry["下次检定日"], "2026-10-10")
        self.assertEqual(entry["status"], rules.STATUS_EXPIRING)
        self.assertTrue(entry["可排期"])


if __name__ == "__main__":
    unittest.main()
