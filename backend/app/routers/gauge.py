"""压力表检定接口：维护压力表，覆盖安排检定、登记合格、登记不合格、办理停用等动作。

判定规则全部在 GaugeService 内收口，路由层只负责参数透传与错误包装。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.gauge import (
    ACTION_FAIL,
    ACTION_PASS,
    ACTION_SCHEDULE,
    ACTION_STOP,
    GaugeService,
)

router = APIRouter(prefix="/api/gauge", tags=["压力表检定"])

service = GaugeService()

LIST_FIELDS = ["压力表编号", "所属设备", "量程范围", "精度等级", "检定日期", "检定周期", "下次检定日", "检定结论", "仪表状态"]
STATUSES = ["检定合格", "即将到期", "待检定", "已停用"]
ACTIONS = [ACTION_SCHEDULE, ACTION_PASS, ACTION_FAIL, ACTION_STOP]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按压力表编号检索"),
    status: str | None = Query(default=None, description="检定合格、即将到期、待检定、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按压力表编号与状态过滤压力表检定列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/scheduling/pending")
def scheduling_pending() -> dict[str, Any]:
    """待检定排期清单：只含待检定的在用表，已停用的不参与。"""
    return service.scheduling_overview()


@router.post("/rejudge", response_model=ActionResult)
def rejudge_history() -> ActionResult:
    """判定标准调整后，按现行规则对全部历史记录重新判一遍。"""
    summary = service.rejudge_all()
    broken = int(summary["规则异常"])
    detail = (
        f"历史记录已按新规则重判 {summary['total']} 条，其中 {broken} 条存在规则异常并已标出"
        if broken
        else f"历史记录已按新规则重判 {summary['total']} 条，全部通过"
    )
    return ActionResult(ok=True, message=detail, entry=summary)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出压力表检定清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "gauge", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条压力表明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"压力表 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条压力表：量程、精度等级、周期越界一律不许保存，并逐项说明原因。"""
    entry, errors = service.create_entry(payload.values)
    if errors:
        return ActionResult(ok=False, message="；".join(errors))
    return ActionResult(ok=True, message="压力表已登记，判定结果已按统一规则生成", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条压力表执行安排检定、登记合格、登记不合格、办理停用。

    停用表会被挡在排期与结论登记之外；不允许的动作同样拦下并说明原因。
    """
    values = dict(payload.values)
    action = str(values.pop("action", "") or "").strip()
    entry, message = service.run_action(entry_id, action, values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
