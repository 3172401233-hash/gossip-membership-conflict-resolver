"""成员冲突合并规则。

核心原则：
1. incarnation 是成员自身状态的逻辑时钟，高 incarnation 优先；
   传播版本 version 只用于同一 incarnation 内的排序，
   版本较新不代表删除/死亡状态一定正确。
2. 主动离开（dead + voluntary）是终态：同 incarnation 或更低
   incarnation 的任何 alive/suspect 都不能复活该节点；
   只有节点自己以更高 incarnation 宣告 alive 才视为重新加入。
3. 故障死亡（dead + failed）压过同 incarnation 的 alive/suspect，
   但若节点自己在同 incarnation 宣称 alive，则无法自动裁决，
   标记为“仍需确认”。
4. suspect 压过同 incarnation 的非自报 alive；节点要洗清怀疑
   必须提升自己的 incarnation 再宣告 alive。若节点自报 alive
   与 suspect 处于同一 incarnation（未反驳），保留 suspect 并
   标记为“仍需确认”。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .model import Cause, MemberRecord, Status

OUTCOME_KEPT = "kept"
OUTCOME_SUPERSEDED = "superseded"  # incarnation 较旧，正常被取代
OUTCOME_DISCARDED = "discarded"  # incarnation 回退：版本虽新但时钟倒退
OUTCOME_BLOCKED = "blocked"  # 同 incarnation 被更强状态压制


@dataclass
class ChainEntry:
    record: MemberRecord
    outcome: str
    note: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record": self.record.to_dict(),
            "outcome": self.outcome,
            "note": self.note,
        }


@dataclass
class MemberResolution:
    member_id: str
    final: MemberRecord
    rule: str
    chain: List[ChainEntry] = field(default_factory=list)
    needs_confirmation: bool = False
    confirmation_reasons: List[str] = field(default_factory=list)

    @property
    def has_conflict(self) -> bool:
        interesting = (OUTCOME_DISCARDED, OUTCOME_BLOCKED)
        return (
            self.needs_confirmation
            or any(entry.outcome in interesting for entry in self.chain)
            or len(self.chain) > 1
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "member": self.member_id,
            "final": self.final.to_dict(),
            "rule": self.rule,
            "needs_confirmation": self.needs_confirmation,
            "confirmation_reasons": list(self.confirmation_reasons),
            "chain": [entry.to_dict() for entry in self.chain],
        }


@dataclass
class MergeResult:
    view: Dict[str, MemberRecord]
    resolutions: List[MemberResolution]
    merged_groups: List[str]

    @property
    def conflicts(self) -> List[MemberResolution]:
        return [r for r in self.resolutions if r.has_conflict]

    @property
    def pending(self) -> List[MemberResolution]:
        return [r for r in self.resolutions if r.needs_confirmation]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "merged_groups": list(self.merged_groups),
            "merged_view": {
                member_id: record.to_dict()
                for member_id, record in sorted(self.view.items())
            },
            "conflict_chain": [r.to_dict() for r in self.conflicts],
            "needs_confirmation": [
                {
                    "member": r.member_id,
                    "reasons": list(r.confirmation_reasons),
                    "current_status": r.final.status.value,
                }
                for r in self.pending
            ],
        }


def _strongest(records: Iterable[MemberRecord]) -> MemberRecord:
    return max(records, key=lambda r: (r.version, r.observer))


def resolve_member(member_id: str,
                   records: Sequence[MemberRecord]) -> MemberResolution:
    if not records:
        raise ValueError("no records for member %r" % member_id)
    ordered = sorted(records, key=lambda r: (r.incarnation, r.version, r.observer))
    max_inc = ordered[-1].incarnation
    candidates = [r for r in ordered if r.incarnation == max_inc]
    stale = [r for r in ordered if r.incarnation < max_inc]

    chain: List[ChainEntry] = []
    notes: List[str] = []
    max_candidate_version = max(r.version for r in candidates)

    for record in stale:
        if record.status is not Status.ALIVE and record.version >= max_candidate_version:
            chain.append(ChainEntry(
                record, OUTCOME_DISCARDED,
                "incarnation 回退：传播版本较新但逻辑时钟倒退，丢弃该状态",
            ))
            notes.append("incarnation_regression")
        else:
            chain.append(ChainEntry(
                record, OUTCOME_SUPERSEDED,
                "incarnation 较旧，被更新的状态取代",
            ))

    voluntary = [r for r in candidates
                 if r.status is Status.DEAD and r.cause is Cause.VOLUNTARY]
    failed = [r for r in candidates
              if r.status is Status.DEAD and r.cause is Cause.FAILED]
    suspects = [r for r in candidates if r.status is Status.SUSPECT]
    alives = [r for r in candidates if r.status is Status.ALIVE]
    self_alives = [r for r in alives if r.is_self_report]

    needs_confirmation = False
    confirm_reasons: List[str] = []

    if voluntary:
        final = _strongest(voluntary)
        rule = "voluntary_leave_terminal"
        for record in candidates:
            if record is final:
                continue
            chain.append(ChainEntry(
                record, OUTCOME_BLOCKED,
                "主动离开是终态，同 incarnation 的其他状态不能复活该节点",
            ))
    elif failed:
        final = _strongest(failed)
        rule = "failure_dead"
        for record in candidates:
            if record is final:
                continue
            chain.append(ChainEntry(
                record, OUTCOME_BLOCKED,
                "故障死亡压过同 incarnation 的 %s" % record.status.value,
            ))
        if self_alives:
            needs_confirmation = True
            confirm_reasons.append(
                "节点自报 alive 与他方故障死亡判定处于同一 incarnation，"
                "需等待节点提升 incarnation 或人工确认"
            )
            rule = "dead_vs_self_alive"
    elif suspects:
        final = _strongest(suspects)
        rule = "suspect_over_alive"
        for record in candidates:
            if record is final:
                continue
            chain.append(ChainEntry(
                record, OUTCOME_BLOCKED,
                "同 incarnation 下 suspect 优先于 alive",
            ))
        if self_alives:
            needs_confirmation = True
            confirm_reasons.append(
                "节点被怀疑但未通过提升 incarnation 反驳，"
                "保留 suspect 并等待节点自证或人工确认"
            )
            rule = "suspect_unrefuted"
    else:
        final = _strongest(alives)
        rule = "alive"
        for record in candidates:
            if record is final:
                continue
            chain.append(ChainEntry(
                record, OUTCOME_BLOCKED,
                "同状态竞争，传播版本较低",
            ))

    stale_leave = any(
        r.status is Status.DEAD and r.cause is Cause.VOLUNTARY for r in stale
    )
    if stale_leave and final.status is Status.ALIVE and final.is_self_report:
        notes.append("rejoin_after_leave")

    chain.append(ChainEntry(final, OUTCOME_KEPT, "最终采用（规则：%s）" % rule))
    chain.sort(key=lambda e: (e.record.incarnation, e.record.version,
                              e.record.observer, e.outcome != OUTCOME_KEPT))

    return MemberResolution(
        member_id=member_id,
        final=final,
        rule=rule,
        chain=chain,
        needs_confirmation=needs_confirmation,
        confirmation_reasons=confirm_reasons,
    )


class Resolver:
    """把多个分区的成员表合并成一份最终视图。"""

    def merge(self,
              groups: Dict[str, Sequence[MemberRecord]],
              events: Optional[Sequence[Dict[str, Any]]] = None
              ) -> MergeResult:
        merged_groups = self._select_groups(groups, events)
        by_member: Dict[str, List[MemberRecord]] = {}
        for name in merged_groups:
            for record in groups[name]:
                by_member.setdefault(record.member_id, []).append(record)
        resolutions = [
            resolve_member(member_id, by_member[member_id])
            for member_id in sorted(by_member)
        ]
        view = {r.member_id: r.final for r in resolutions}
        return MergeResult(view=view, resolutions=resolutions,
                           merged_groups=merged_groups)

    @staticmethod
    def _select_groups(groups: Dict[str, Sequence[MemberRecord]],
                       events: Optional[Sequence[Dict[str, Any]]]
                       ) -> List[str]:
        if not events:
            return sorted(groups)
        selected: List[str] = []
        for event in events:
            if event.get("type") != "partition_healed":
                continue
            for name in event.get("groups", []):
                if name not in groups:
                    raise ValueError("heal event references unknown group %r"
                                     % name)
                if name not in selected:
                    selected.append(name)
        return selected or sorted(groups)


def merge_groups(groups: Dict[str, Sequence[MemberRecord]],
                 events: Optional[Sequence[Dict[str, Any]]] = None
                 ) -> MergeResult:
    return Resolver().merge(groups, events)
