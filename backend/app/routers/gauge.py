"""压力表检定接口：维护压力表，覆盖安排检定、登记结论、停用、排期与规则重判。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.gauge import GaugeService

router = APIRouter(prefix="/api/gauge", tags=["压力表检定"])

service = GaugeService()

LIST_FIELDS = ["压力表编号", "所属设备", "量程范围", "精度等级", "检定周期", "检定日期", "下次检定日", "检定结论", "仪表状态"]
STATUSES = ["检定合格", "即将到期", "待检定", "不合规", "已停用"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按压力表编号检索"),
    status: str | None = Query(default=None, description="检定合格、即将到期、待检定、不合规、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按压力表编号与状态过滤压力表检定列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if status and status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"状态「{status}」不在允许范围内：{'、'.join(STATUSES)}")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/schedule")
def schedule_entries() -> dict[str, Any]:
    """待检定排期清单：已停用的表不出现在这里。"""
    items = service.schedule()
    return {"module": "gauge", "total": len(items), "items": items}


@router.get("/stats")
def gauge_stats() -> dict[str, Any]:
    """列表页卡片统计，口径与列表判定完全一致。"""
    return {"stats": service.stats()}


@router.get("/rules")
def current_rules() -> dict[str, Any]:
    """公示当前判定标准与版本，方便对照历史记录是按哪版规则判的。"""
    from app.services import gauge_rules

    return {
        "规则版本": gauge_rules.RULE_VERSION,
        "量程允许范围MPa": [gauge_rules.MIN_SCALE_MPA, gauge_rules.MAX_SCALE_MPA],
        "允许精度等级": sorted(gauge_rules.ALLOWED_ACCURACY_GRADES, key=float),
        "默认检定周期月": gauge_rules.DEFAULT_CYCLE_MONTHS,
        "允许检定周期月": [gauge_rules.MIN_CYCLE_MONTHS, gauge_rules.MAX_CYCLE_MONTHS],
        "到期预警天数": gauge_rules.EXPIRING_SOON_DAYS,
    }


@router.post("/rejudge")
def rejudge_entries() -> ActionResult:
    """判定标准调整后，对历史数据按新规则重新判一遍并汇报变更。"""
    report = service.rejudge()
    message = (
        f"已按规则 v{report['规则版本']} 重判 {report['重判数量']} 块压力表，"
        f"{report['变更数量']} 块结论发生变化"
    )
    return ActionResult(ok=True, message=message, entry=report)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条压力表明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"压力表 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记压力表：缺字段或量程/精度越界都拦下，并说明具体是哪一项。"""
    entry, errors = service.create_entry(payload.values)
    if errors:
        return ActionResult(ok=False, message="；".join(errors))
    return ActionResult(ok=True, message="压力表已登记，判定结论已按当前规则生成", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """修改压力表档案；停用表只读，量程/精度越界同样不许保存。"""
    entry, errors = service.update_entry(entry_id, payload.values)
    if errors:
        return ActionResult(ok=False, message="；".join(errors))
    return ActionResult(ok=True, message="压力表档案已更新并重新判定", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单块压力表执行安排检定、登记合格/不合格、办理停用；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    action_values = {key: value for key, value in payload.values.items() if key != "action"}
    entry, message = service.run_action(entry_id, action, action_values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出压力表检定清单：返回当前判定口径下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "gauge", "total": total, "items": items}
