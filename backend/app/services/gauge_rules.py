"""压力表检定判定规则（唯一收口处）。

所有合格性判定、下次检定日计算、状态推导都只允许走这里，
列表页、详情页、排期接口共用同一份结果，避免两处结论互相打架。

判定标准调整时：
1. 修改下面的常量（量程上限、允许精度等级、默认周期等）；
2. 把 RULE_VERSION 递增一位；
3. 历史数据通过 rejudge 按新规则重判，结论一律以最新规则/最新发布的检定为准。
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

# 规则版本：每次判定标准调整都递增，发布记录会带上当时的版本号。
RULE_VERSION = 1

# 压力表量程允许范围，单位 MPa：下限必须 >= 0，上限必须 <= 该值。
MIN_SCALE_MPA = 0.0
MAX_SCALE_MPA = 100.0

# 允许的精度等级（GB/T 1226 常见等级），入参会先去掉「级」字与空白再比对。
ALLOWED_ACCURACY_GRADES = {"0.25", "0.4", "0.6", "1.0", "1.6", "2.5", "4.0"}

# 默认检定周期（月）；允许填写的周期区间。
DEFAULT_CYCLE_MONTHS = 6
MIN_CYCLE_MONTHS = 1
MAX_CYCLE_MONTHS = 36

# 到期前多少天进入「即将到期」。
EXPIRING_SOON_DAYS = 30

STATUS_QUALIFIED = "检定合格"
STATUS_EXPIRING = "即将到期"
STATUS_DUE = "待检定"
STATUS_STOPPED = "已停用"
STATUS_NONCOMPLIANT = "不合规"

VALID_STATUSES = [
    STATUS_QUALIFIED,
    STATUS_EXPIRING,
    STATUS_DUE,
    STATUS_NONCOMPLIANT,
    STATUS_STOPPED,
]

# 允许提交到检定记录里的结论；同一块表以最后发布的一条为准。
PUBLISHABLE_RESULTS = {"合格", "不合格"}

_RANGE_PATTERN = re.compile(
    r"^\s*(-?[0-9]+(?:\.[0-9]+)?)\s*(?:~\s*(-?[0-9]+(?:\.[0-9]+)?)\s*)?(?:MPa|mpa|兆帕)?\s*$"
)
# 连字符区间写法（0-1.6）不能误伤负数下限（-0.1~1.6），仅替换数字后面的连字符。
_HYPHEN_RANGE = re.compile(r"([0-9])\s*[—－-]\s*(-?[0-9])")


def parse_date(value: Any) -> date | None:
    """把 YYYY-MM-DD 文本解析成日期；空值或无法识别时返回 None。"""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def add_months(day: date, months: int) -> date:
    """按月加周期：落到目标月份没有的日期（如 3 月 31 日 +1 月）取该月最后一天。"""
    year = day.year + (day.month - 1 + months) // 12
    month = (day.month - 1 + months) % 12 + 1
    # 月份天数上限 31，calendar 会自动裁到当月最后一天。
    import calendar

    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def parse_range(value: Any) -> tuple[tuple[float, float] | None, str | None]:
    """校验并解析量程范围，返回 ((下限, 上限), 错误说明)。

    接受「0~1.6」「0-1.6 MPa」「0～1.6兆帕」等写法；单值按上限处理。
    """
    text = str(value or "").strip().replace("～", "~")
    if not text:
        return None, "量程范围未填写"
    normalized = _HYPHEN_RANGE.sub(r"\1~\2", text)
    match = _RANGE_PATTERN.match(normalized)
    if not match:
        return None, f"量程范围「{text}」格式无法识别，应形如 0~1.6 MPa"
    low_text, high_text = match.group(1), match.group(2)
    low = float(low_text)
    high = float(high_text) if high_text is not None else low
    if high_text is None:
        low, high = 0.0, low
    if low >= high:
        return None, f"量程范围「{text}」下限必须小于上限"
    if low < MIN_SCALE_MPA or high > MAX_SCALE_MPA:
        return None, (
            f"量程范围「{text}」超出允许范围"
            f"（{_fmt(MIN_SCALE_MPA)}~{_fmt(MAX_SCALE_MPA)} MPa）"
        )
    return (low, high), None


def parse_accuracy_grade(value: Any) -> tuple[str | None, str | None]:
    """校验精度等级，返回 (标准等级, 错误说明)。"""
    text = str(value or "").strip().rstrip("级").strip()
    if not text:
        return None, "精度等级未填写"
    normalized = text.replace("．", ".")
    if normalized not in ALLOWED_ACCURACY_GRADES:
        return None, (
            f"精度等级「{text}」超出允许范围"
            f"（允许：{'、'.join(sorted(ALLOWED_ACCURACY_GRADES, key=float))} 级）"
        )
    return normalized, None


def parse_cycle_months(value: Any) -> tuple[int, str | None]:
    """校验检定周期（月），空值取默认周期。"""
    if value is None or str(value).strip() == "":
        return DEFAULT_CYCLE_MONTHS, None
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]+", text):
        return 0, f"检定周期「{text}」必须是整数月份"
    months = int(text)
    if not (MIN_CYCLE_MONTHS <= months <= MAX_CYCLE_MONTHS):
        return 0, (
            f"检定周期「{text}」超出允许范围"
            f"（{MIN_CYCLE_MONTHS}~{MAX_CYCLE_MONTHS} 个月）"
        )
    return months, None


def validate_spec(values: dict[str, Any]) -> list[str]:
    """登记/修改保存前的硬校验：量程与精度等级越界一律不许保存。

    返回越界项说明列表；空列表表示通过。检定周期为可选项，只校验格式。
    """
    errors: list[str] = []
    _, range_error = parse_range(values.get("量程范围"))
    if range_error:
        errors.append(range_error)
    _, grade_error = parse_accuracy_grade(values.get("精度等级"))
    if grade_error:
        errors.append(grade_error)
    if "检定周期" in values:
        _, cycle_error = parse_cycle_months(values.get("检定周期"))
        if cycle_error:
            errors.append(cycle_error)
    return errors


def _fmt(number: float) -> str:
    return f"{number:g}"


def next_calibration_date(last_date: date | None, cycle_months: int) -> str | None:
    """下次检定日统一由「上次检定日期 + 检定周期」算出，不接受手填。"""
    if last_date is None:
        return None
    return add_months(last_date, cycle_months).isoformat()


def latest_verdicts(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """取该表全部已发布检定，按发布先后排序（最后一条即最终结论）。"""
    verdicts = list(entry.get("检定记录") or [])
    return sorted(verdicts, key=lambda item: int(item.get("seq", 0)))


def derive(entry: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """对一块压力表按当前规则做一次完整判定。

    列表页、详情页、排期、看板都调用本函数，保证只有一份结论：
    - 已停用：不参与任何排期，单独标识；
    - 量程/精度越界：判为不合规（历史数据按新规则重判时也会暴露出来）；
    - 无有效检定记录：待检定；
    - 最后发布的检定结论为不合格：待检定；
    - 否则按「上次检定日期 + 检定周期」算下次检定日，30 天内到期算即将到期。
    """
    today = today or date.today()
    stopped = bool(entry.get("停用", False))

    spec_errors = validate_spec(entry)

    verdicts = latest_verdicts(entry)
    last_verdict = verdicts[-1] if verdicts else None
    last_date = parse_date(last_verdict.get("检定日期")) if last_verdict else None
    cycle_months, _ = parse_cycle_months(entry.get("检定周期"))
    due_text = next_calibration_date(last_date, cycle_months)
    due_date = parse_date(due_text)

    if stopped:
        status = STATUS_STOPPED
    elif spec_errors:
        status = STATUS_NONCOMPLIANT
    elif last_verdict is None or last_verdict.get("检定结论") != "合格":
        status = STATUS_DUE
    elif due_date is not None and due_date < today:
        status = STATUS_DUE
    elif due_date is not None and (due_date - today).days <= EXPIRING_SOON_DAYS:
        status = STATUS_EXPIRING
    else:
        status = STATUS_QUALIFIED

    if last_verdict is not None:
        conclusion = f"检定{last_verdict['检定结论']}"
    elif stopped:
        conclusion = "已停用"
    elif spec_errors:
        conclusion = STATUS_NONCOMPLIANT
    else:
        conclusion = STATUS_DUE

    reason_parts: list[str] = []
    if stopped:
        reason_parts.append("仪表已停用，不参与检定排期")
    if spec_errors:
        reason_parts.extend(spec_errors)
    if not stopped and not spec_errors:
        if last_verdict is None:
            reason_parts.append("尚无检定记录，需安排首检")
        elif last_verdict.get("检定结论") != "合格":
            reason_parts.append("最后发布的检定结论为不合格，以该结论为准重新安排检定")
        elif due_date is not None and due_date < today:
            reason_parts.append(f"检定周期已于 {due_text} 到期")
        elif due_date is not None and (due_date - today).days <= EXPIRING_SOON_DAYS:
            reason_parts.append(f"将于 {due_text} 到期（30 天内）")
        else:
            reason_parts.append(f"下次检定日 {due_text}，在检定有效期内")
    if last_verdict is not None:
        reason_parts.append(f"依据最后发布的第 {last_verdict.get('seq')} 次检定（规则 v{last_verdict.get('规则版本', '?')}）")

    return {
        "status": status,
        "检定结论": conclusion,
        "下次检定日": due_text or "—",
        "合规": not spec_errors,
        "判定说明": "；".join(reason_parts),
        "规则版本": RULE_VERSION,
        "停用": stopped,
        "可排期": (not stopped) and status in (STATUS_DUE, STATUS_EXPIRING),
        "待处理": (not stopped) and status in (STATUS_DUE, STATUS_EXPIRING, STATUS_NONCOMPLIANT),
    }
