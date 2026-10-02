"""压力表检定业务规则：保存校验、状态流转、排期与重判全部走统一判定。

判定细节见 app.services.gauge_rules，本类只负责存取与动作编排，
任何页面都不允许各自再算一遍结论。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.services import gauge_rules as rules
from app.store import store

MODULE = "gauge"

# 登记时必须填写的基础字段；量程与精度等级的取值合法性在 rules.validate_spec 里硬校验。
REQUIRED_FIELDS = ["压力表编号", "所属设备", "量程范围", "精度等级"]
EDITABLE_FIELDS = ["压力表编号", "所属设备", "量程范围", "精度等级", "检定周期"]

ACTION_SCHEDULE = "安排检定"
ACTION_PASS = "登记合格"
ACTION_FAIL = "登记不合格"
ACTION_STOP = "办理停用"
ACTION_RULES = {ACTION_SCHEDULE, ACTION_PASS, ACTION_FAIL, ACTION_STOP}


class GaugeService:
    # ---- 查询 -------------------------------------------------------------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        today: date | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [self._present(row, today=today) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("压力表编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def schedule(self, *, today: date | None = None) -> list[dict[str, Any]]:
        """待检定排期：已停用的表不参与，不合规的表也不能直接安排检定。"""
        rows = [self._present(row, today=today) for row in store.rows(MODULE)]
        return [row for row in rows if row["可排期"]]

    def stats(self, *, today: date | None = None) -> list[dict[str, int | str]]:
        rows = [self._present(row, today=today) for row in store.rows(MODULE)]
        return [
            {"label": "合格仪表", "value": sum(1 for row in rows if row["status"] == rules.STATUS_QUALIFIED)},
            {"label": "待检定仪表", "value": sum(1 for row in rows if row["status"] == rules.STATUS_DUE)},
            {"label": "即将到期", "value": sum(1 for row in rows if row["status"] == rules.STATUS_EXPIRING)},
            {"label": "停用仪表", "value": sum(1 for row in rows if row["status"] == rules.STATUS_STOPPED)},
            {"label": "不合规", "value": sum(1 for row in rows if row["status"] == rules.STATUS_NONCOMPLIANT)},
        ]

    def get_entry(self, entry_id: int, *, today: date | None = None) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._present(row, today=today) if row is not None else None

    # ---- 登记 / 修改 ------------------------------------------------------

    def create_entry(
        self, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, [f"缺少必填字段：{field}" for field in missing]

        errors = rules.validate_spec(values)
        if errors:
            # 量程或精度越过允许范围：不许保存，并逐项说明超了哪一项。
            return None, errors

        rows = store.rows(MODULE)
        duplicate = next((row for row in rows if str(row.get("压力表编号")) == str(values["压力表编号"]).strip()), None)
        if duplicate is not None:
            return None, [f"压力表编号「{values['压力表编号']}」已存在，不能重复登记"]

        cycle_months, _ = rules.parse_cycle_months(values.get("检定周期"))
        entry: dict[str, Any] = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "停用": False,
            "检定记录": [],
        }
        entry.update({field: str(values.get(field) or "").strip() for field in REQUIRED_FIELDS})
        entry["检定周期"] = cycle_months
        rows.append(entry)
        return self._present(entry), []

    def update_entry(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[str]]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, [f"压力表 {entry_id} 不存在或已归档"]
        if entry.get("停用"):
            return None, ["仪表已停用，档案只读，不能再修改；如需变更请先办理启用"]

        merged = {**entry, **{key: values[key] for key in EDITABLE_FIELDS if key in values}}
        errors = rules.validate_spec(merged)
        if errors:
            return None, errors

        for field in REQUIRED_FIELDS:
            if field in values:
                entry[field] = str(values[field] or "").strip()
        if "检定周期" in values:
            cycle_months, cycle_error = rules.parse_cycle_months(values.get("检定周期"))
            if cycle_error:
                return None, [cycle_error]
            entry["检定周期"] = cycle_months
        return self._present(entry), []

    # ---- 动作流转 ---------------------------------------------------------

    def run_action(
        self,
        entry_id: int,
        action: str,
        payload: dict[str, Any] | None = None,
        *,
        today: date | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"压力表 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于压力表检定可执行范围"
        payload = payload or {}

        if action == ACTION_STOP:
            if entry.get("停用"):
                return None, "该压力表已是停用状态"
            entry["停用"] = True
            return self._present(entry, today=today), "压力表已办理停用，已从检定排期中剔除"

        # 停用的表不参与排期，任何检定动作都要拦下。
        if entry.get("停用"):
            return None, "该压力表已停用，不参与检定排期；如需检定请先办理启用"

        if action == ACTION_SCHEDULE:
            check_date = rules.parse_date(payload.get("检定日期")) or (today or date.today())
            entry.setdefault("排期", {})["计划检定日"] = check_date.isoformat()
            return self._present(entry, today=today), f"压力表已排入 {check_date.isoformat()} 检定计划"

        result = "合格" if action == ACTION_PASS else "不合格"
        check_date = rules.parse_date(payload.get("检定日期"))
        if check_date is None:
            return None, "登记检定结论必须填写检定日期（YYYY-MM-DD）"

        # 登记前再次硬校验量程/精度，防止规则收紧后旧数据混进合格结论。
        spec_errors = rules.validate_spec(entry)
        if spec_errors:
            return None, "无法登记检定结论：" + "；".join(spec_errors)

        records = entry.setdefault("检定记录", [])
        seq = max((int(item.get("seq", 0)) for item in records), default=0) + 1
        records.append({
            "seq": seq,
            "检定日期": check_date.isoformat(),
            "检定周期": rules.parse_cycle_months(entry.get("检定周期"))[0],
            "检定结论": result,
            "规则版本": rules.RULE_VERSION,
        })
        entry.pop("排期", None)
        # 下次检定日不当输入收，统一在 derive 里按「上次检定日期 + 检定周期」重算。
        return self._present(entry, today=today), f"第 {seq} 次检定结论「{result}」已发布"

    # ---- 规则重判 ---------------------------------------------------------

    def rejudge(self, *, today: date | None = None) -> dict[str, Any]:
        """判定标准改过之后，把历史数据按当前规则重新判一遍。

        已发布的检定记录保留（含发布时的规则版本），但当前展示结论、
        状态与排期一律以最新规则与最后发布的检定为准。
        与行内保存的上次判定快照对比，只汇报真正发生变化的记录。
        """
        changes: list[dict[str, Any]] = []
        for row in store.rows(MODULE):
            presented = self._present(row, today=today)
            after = {key: presented[key] for key in ("status", "检定结论", "下次检定日")}
            snapshot = row.get("判定快照")
            # 首次重判且行里带旧版结论（种子/历史落库字段）时，也按旧值比对一次。
            before = snapshot
            if before is None and ("status" in row or "检定结论" in row):
                before = {
                    "status": row.get("status"),
                    "检定结论": row.get("检定结论"),
                    "下次检定日": row.get("下次检定日"),
                }
            if before is not None and before != after:
                changes.append({
                    "id": row["id"],
                    "压力表编号": row.get("压力表编号"),
                    "before": before,
                    "after": after,
                })
            row["判定快照"] = after
            row["规则版本"] = rules.RULE_VERSION
        return {
            "规则版本": rules.RULE_VERSION,
            "重判数量": len(store.rows(MODULE)),
            "变更数量": len(changes),
            "变更明细": changes,
        }

    # ---- 内部：统一出参 ---------------------------------------------------

    def _present(self, entry: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
        """给存储行叠上统一判定结果，页面拿到的结论只有这一个来源。"""
        verdict = rules.derive(entry, today=today)
        cycle_months, _ = rules.parse_cycle_months(entry.get("检定周期"))
        presented: dict[str, Any] = {
            "id": entry["id"],
            "压力表编号": entry.get("压力表编号", ""),
            "所属设备": entry.get("所属设备", ""),
            "量程范围": entry.get("量程范围", ""),
            "精度等级": entry.get("精度等级", ""),
            "检定周期": cycle_months,
            "检定日期": self._last_check_date(entry),
            "下次检定日": verdict["下次检定日"],
            "检定结论": verdict["检定结论"],
            "仪表状态": self._display_state(verdict["status"]),
            "status": verdict["status"],
            "pending": verdict["待处理"],
            "abnormal": verdict["status"] in (rules.STATUS_DUE, rules.STATUS_EXPIRING, rules.STATUS_NONCOMPLIANT),
            "停用": verdict["停用"],
            "合规": verdict["合规"],
            "可排期": verdict["可排期"],
            "判定说明": verdict["判定说明"],
            "规则版本": verdict["规则版本"],
            "检定记录": rules.latest_verdicts(entry),
        }
        return presented

    @staticmethod
    def _last_check_date(entry: dict[str, Any]) -> str:
        records = rules.latest_verdicts(entry)
        return str(records[-1]["检定日期"]) if records else "—"

    @staticmethod
    def _display_state(status: str) -> str:
        return {
            rules.STATUS_QUALIFIED: "合格在用",
            rules.STATUS_EXPIRING: "即将到期",
            rules.STATUS_DUE: "待检定",
            rules.STATUS_NONCOMPLIANT: "规格不合规",
            rules.STATUS_STOPPED: "已停用",
        }[status]
