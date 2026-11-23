"""成员状态数据模型。

每条 MemberRecord 表示某个观察者（observer）对某个成员（member_id）
的一次状态宣称，携带 incarnation 与传播版本 version。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict


class Status(Enum):
    ALIVE = "alive"
    SUSPECT = "suspect"
    DEAD = "dead"


class Cause(Enum):
    """dead 状态的成因。alive/suspect 一律为 NONE。"""

    NONE = "none"
    VOLUNTARY = "voluntary"  # 主动离开（人工下线），终态
    FAILED = "failed"  # 被故障检测判定死亡


@dataclass(frozen=True)
class MemberRecord:
    member_id: str
    incarnation: int
    status: Status
    observer: str
    version: int
    cause: Cause = Cause.NONE

    @property
    def is_self_report(self) -> bool:
        return self.observer == self.member_id

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemberRecord":
        status = Status(data["status"])
        cause = Cause(data.get("cause", "none"))
        if status is not Status.DEAD:
            cause = Cause.NONE
        return cls(
            member_id=data["id"],
            incarnation=int(data["incarnation"]),
            status=status,
            observer=data.get("observer", data["id"]),
            version=int(data.get("version", 0)),
            cause=cause,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.member_id,
            "incarnation": self.incarnation,
            "status": self.status.value,
            "observer": self.observer,
            "version": self.version,
            "cause": self.cause.value,
        }
