"""压力表检定业务规则：所有判定口径统一收在本模块。

收口的判定规则（任何保存、列表、详情、排期都走同一份逻辑）：

1. 保存校验：量程范围、精度等级越过允许范围一律不许保存，并说明超了哪一项；
   检定周期只接受 1~24 个整月。
2. 下次检定日：一律按「上次检定日期 + 检定周期（月）」计算，不接受手填，
   列表页与详情页看到的下次检定日始终来自同一计算结果。
3. 停用即冻结：已停用的表不参与待检定排期，列表页单独标出。
4. 结论以最后一次发布为准：检定结论只从发布记录里取最新一条，历史发布保留可查，
   详情页不再挂与判定不一致的旧结论。
5. 规则变更后可对历史数据整表重判（rejudge_all），启动时也会自动重判一遍。
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime
from typing import Any

from app.store import store

MODULE = "gauge"
REQUIRED_FIELDS = ["压力表编号", "所属设备", "量程范围", "精度等级"]

STATUS_PASS = "检定合格"
STATUS_EXPIRING = "即将到期"
STATUS_DUE = "待检定"
STATUS_STOPPED = "已停用"
STATUS_ORDER = [STATUS_PASS, STATUS_EXPIRING, STATUS_DUE, STATUS_STOPPED]

# 动作不直接写状态：动作只负责「登记事实」，状态随后由统一判定推导，避免两套结论打架。
ACTION_SCHEDULE = "安排检定"
ACTION_PASS = "登记合格"
ACTION_FAIL = "登记不合格"
ACTION_STOP = "办理停用"
ACTION_RULES = {
    ACTION_SCHEDULE: STATUS_DUE,
    ACTION_PASS: STATUS_PASS,
    ACTION_FAIL: STATUS_DUE,
    ACTION_STOP: STATUS_STOPPED,
}

DEFAULT_CYCLE_MONTHS = 6
MIN_CYCLE_MONTHS = 1
MAX_CYCLE_MONTHS = 24
EXPIRING_WINDOW_DAYS = 30

# 压力表允许的精度等级（级）：精密表 0.25/0.4/0.6，工作用表 1.0/1.6/2.5。
ALLOWED_GRADES = [0.25, 0.4, 0.6, 1.0, 1.6, 2.5]
PRESSURE_MIN_MPA = -0.1
PRESSURE_MAX_MPA = 160.0

CONCLUSION_PASS = "合格"
CONCLUSION_FAIL = "不合格"
CONCLUSION_NONE = "未检定"
VALID_CONCLUSIONS = (CONCLUSION_PASS, CONCLUSION_FAIL)

_RANGE_SPLIT_RE = re.compile(r"[~～]")
_NUMBER_RE = re.compile(r"^[+-]?\d+(\.\d+)?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_date(value: Any) -> date | None:
    """解析 YYYY-MM-DD；解析不了返回 None，调用方决定是否报错。"""
    text = str(value or "").strip()
    if not text or not _DATE_RE.match(text):
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_cycle(value: Any) -> int | None:
    """检定周期只接受 1~24 的整月；空值由调用方回退默认周期。"""
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    try:
        cycle = int(text)
    except (TypeError, ValueError):
        return None
    if cycle < MIN_CYCLE_MONTHS or cycle > MAX_CYCLE_MONTHS:
        return None
    return cycle


def _parse_pressure(value: Any) -> tuple[float, float] | None:
    """把「0~1.6 MPa」解析成 (下限, 上限)；单个正数按 0~该值处理。"""
    text = str(value or "").strip()
    if not text:
        return None
    parts = [p.strip() for p in _RANGE_SPLIT_RE.split(text, maxsplit=1) if p.strip()]
    if not parts:
        return None

    def to_number(part: str) -> float | None:
        token = part.replace(" ", "").removesuffix("MPa").removesuffix("mpa").removesuffix("Mbar")
        if not _NUMBER_RE.match(token):
            return None
        return float(token)

    if len(parts) == 1:
        upper = to_number(parts[0])
        if upper is None:
            return None
        return 0.0, upper
    lower = to_number(parts[0])
    upper = to_number(parts[1])
    if lower is None or upper is None:
        return None
    return lower, upper


def _parse_grade(value: Any) -> float | None:
    """精度等级：允许「1.6」或「1.6级」，不在允许集合内返回 None。"""
    text = str(value or "").strip().removesuffix("级")
    if not _NUMBER_RE.match(text):
        return None
    grade = float(text)
    return grade if grade in ALLOWED_GRADES else None


def add_months(day: date, months: int) -> date:
    """按整月顺延，目标月份没有对应日时取当月最后一天。"""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def validate_values(values: dict[str, Any]) -> list[str]:
    """保存前的统一入口：缺字段、量程越界、精度越界、周期/日期非法都在这拦下。"""
    errors = [
        f"缺少必填字段：{field}"
        for field in REQUIRED_FIELDS
        if not str(values.get(field) or "").strip()
    ]

    raw_range = str(values.get("量程范围") or "").strip()
    if raw_range:
        pressure = _parse_pressure(raw_range)
        if pressure is None:
            errors.append(
                f"量程范围「{raw_range}」无法识别为合法压力区间，正确格式如 0~1.6 MPa"
            )
        else:
            lower, upper = pressure
            if (
                lower < PRESSURE_MIN_MPA
                or upper > PRESSURE_MAX_MPA
                or upper <= lower
            ):
                errors.append(
                    f"量程范围「{raw_range}」超出允许范围"
                    f"（下限不低于 {PRESSURE_MIN_MPA} MPa、上限不超过 {PRESSURE_MAX_MPA:g} MPa，"
                    "且上限必须大于下限）"
                )

    raw_grade = str(values.get("精度等级") or "").strip()
    if raw_grade and _parse_grade(raw_grade) is None:
        allowed = "、".join(f"{grade:g}" for grade in ALLOWED_GRADES)
        errors.append(f"精度等级「{raw_grade}」不在允许范围（允许：{allowed} 级）")

    raw_cycle = str(values.get("检定周期") or "").strip()
    if raw_cycle and _parse_cycle(raw_cycle) is None:
        errors.append(
            f"检定周期「{raw_cycle}」非法，只接受 {MIN_CYCLE_MONTHS}~{MAX_CYCLE_MONTHS} 之间的整月数"
        )

    raw_date = str(values.get("检定日期") or "").strip()
    if raw_date and _parse_date(raw_date) is None:
        errors.append(f"检定日期「{raw_date}」格式非法，正确格式为 YYYY-MM-DD")

    return errors


def latest_publication(entry: dict[str, Any]) -> dict[str, Any] | None:
    """结论只认发布记录里最后发布的一条（发布时间靠后优先）。"""
    publications = entry.get("publications") or []
    if not publications:
        return None
    return max(publications, key=lambda item: str(item.get("发布时间") or ""))


class GaugeService:
    # ---------- 统一判定 ----------

    def _cycle_of(self, entry: dict[str, Any]) -> int:
        return _parse_cycle(entry.get("检定周期")) or DEFAULT_CYCLE_MONTHS

    def apply_judgment(self, entry: dict[str, Any]) -> list[str]:
        """对一条记录执行全套判定并就地写回派生字段；返回规则异常说明（供历史重判）。

        保存入口遇到异常会拦截；历史重判不删数据，只把异常标出并让结论/状态/排期统一。
        """
        values = {
            "压力表编号": entry.get("压力表编号"),
            "所属设备": entry.get("所属设备"),
            "量程范围": entry.get("量程范围"),
            "精度等级": entry.get("精度等级"),
            "检定周期": entry.get("检定周期"),
            "检定日期": entry.get("检定日期"),
        }
        violations = validate_values(values)

        cycle = self._cycle_of(entry)
        entry["检定周期"] = cycle

        last_date = _parse_date(entry.get("检定日期"))
        if last_date is None:
            entry["下次检定日"] = ""
        else:
            entry["下次检定日"] = add_months(last_date, cycle).isoformat()

        publication = latest_publication(entry)
        if publication is None:
            conclusion = CONCLUSION_NONE
        else:
            conclusion = str(publication.get("检定结论") or CONCLUSION_NONE)
        # 列表页「检定结论」列与详情页同源：只展示最后发布的结论，杜绝两处打架。
        entry["检定结论"] = conclusion

        if entry.get("status") == STATUS_STOPPED:
            status = STATUS_STOPPED
        elif conclusion == CONCLUSION_FAIL:
            status = STATUS_DUE
        elif last_date is None:
            status = STATUS_DUE
        else:
            due_date = add_months(last_date, cycle)
            today = date.today()
            if due_date < today:
                status = STATUS_DUE
            elif (due_date - today).days <= EXPIRING_WINDOW_DAYS:
                status = STATUS_EXPIRING
            else:
                status = STATUS_PASS

        entry["status"] = status
        entry["仪表状态"] = status
        entry["violations"] = violations
        entry["pending"] = status in (STATUS_DUE, STATUS_EXPIRING)
        entry["abnormal"] = bool(violations) or conclusion == CONCLUSION_FAIL
        return violations

    def rejudge_all(self) -> dict[str, Any]:
        """判定标准变更后，对全部历史记录按现行规则重判一遍。"""
        rows = store.rows(MODULE)
        broken = 0
        status_count = {status: 0 for status in STATUS_ORDER}
        for row in rows:
            violations = self.apply_judgment(row)
            if violations:
                broken += 1
            status_count[str(row.get("status"))] = status_count.get(str(row.get("status")), 0) + 1
        return {
            "total": len(rows),
            "规则异常": broken,
            "状态分布": status_count,
            "重判时间": datetime.now().isoformat(timespec="seconds"),
        }

    # ---------- 查询 ----------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for row in rows:
            self.apply_judgment(row)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("压力表编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def scheduling_overview(self) -> dict[str, Any]:
        """待检定排期：已停用的表不参与；同时返回各状态数量供列表页统计。"""
        rows = store.rows(MODULE)
        for row in rows:
            self.apply_judgment(row)
        due_rows = [
            row for row in rows
            if row.get("status") == STATUS_DUE
        ]
        counts = {status: 0 for status in STATUS_ORDER}
        for row in rows:
            counts[str(row["status"])] += 1
        return {
            "items": due_rows,
            "total": len(rows),
            "待检定": counts[STATUS_DUE],
            "即将到期": counts[STATUS_EXPIRING],
            "检定合格": counts[STATUS_PASS],
            "已停用": counts[STATUS_STOPPED],
        }

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is not None:
            self.apply_judgment(entry)
        return entry

    # ---------- 保存与动作 ----------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        errors = validate_values(values)
        if errors:
            return None, errors
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["检定周期"] = _parse_cycle(values.get("检定周期")) or DEFAULT_CYCLE_MONTHS
        entry["检定日期"] = str(values.get("检定日期") or "").strip()
        entry["下次检定日"] = ""
        entry["publications"] = []
        entry["violations"] = []
        entry["status"] = STATUS_DUE

        initial = str(values.get("检定结论") or "").strip()
        inspect_date = entry["检定日期"] or date.today().isoformat()
        if initial in VALID_CONCLUSIONS:
            entry["检定日期"] = inspect_date
            entry["publications"].append({
                "检定日期": inspect_date,
                "检定结论": initial,
                "发布时间": datetime.now().isoformat(timespec="microseconds"),
            })

        self.apply_judgment(entry)
        rows.append(entry)
        return entry, []

    def _publish(
        self, entry: dict[str, Any], conclusion: str, values: dict[str, Any]
    ) -> str | None:
        """登记一条检定结论（以发布记录为准）；返回错误说明，None 表示成功。"""
        violations = self.apply_judgment(entry)
        if violations:
            return f"该表存在未整改的规则异常，不能登记检定结论：{'；'.join(violations)}"

        raw_date = str(values.get("检定日期") or "").strip()
        if raw_date:
            inspect_date = _parse_date(raw_date)
            if inspect_date is None:
                return f"检定日期「{raw_date}」格式非法，正确格式为 YYYY-MM-DD"
            inspect_text = inspect_date.isoformat()
        else:
            inspect_text = date.today().isoformat()

        raw_cycle = str(values.get("检定周期") or "").strip()
        if raw_cycle:
            cycle = _parse_cycle(raw_cycle)
            if cycle is None:
                return (
                    f"检定周期「{raw_cycle}」非法，只接受 "
                    f"{MIN_CYCLE_MONTHS}~{MAX_CYCLE_MONTHS} 之间的整月数"
                )
            entry["检定周期"] = cycle

        entry["检定日期"] = inspect_text
        entry.setdefault("publications", []).append({
            "检定日期": inspect_text,
            "检定结论": conclusion,
            "发布时间": datetime.now().isoformat(timespec="microseconds"),
        })
        self.apply_judgment(entry)
        return None

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        values = values or {}
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"压力表 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于压力表检定可执行范围"

        self.apply_judgment(entry)
        stopped = entry.get("status") == STATUS_STOPPED

        if action == ACTION_STOP:
            if stopped:
                return None, "压力表已是停用状态，停用后不参与检定排期"
            entry["status"] = STATUS_STOPPED
            entry["仪表状态"] = STATUS_STOPPED
            entry["停用时间"] = date.today().isoformat()
            entry["pending"] = False
            return entry, "压力表已办理停用，不再进入待检定排期"

        if stopped:
            # 已停用的表不参与排期，也不再接受新的检定结论。
            return None, "该压力表已停用，不参与检定排期，也不能登记检定结论"

        if action == ACTION_SCHEDULE:
            entry["上次排期日"] = date.today().isoformat()
            self.apply_judgment(entry)
            if entry["status"] != STATUS_DUE:
                return entry, "压力表尚在检定有效期内，已记录排期意向，无需立即安排检定"
            return entry, "压力表已排入待检定清单"

        if action in (ACTION_PASS, ACTION_FAIL):
            conclusion = CONCLUSION_PASS if action == ACTION_PASS else CONCLUSION_FAIL
            error = self._publish(entry, conclusion, values)
            if error:
                return None, error
            return entry, f"已发布检定结论「{conclusion}」，下次检定日按上次检定日期与检定周期重新计算"

        return None, f"动作「{action}」缺少处理规则"


gauge_service = GaugeService()
