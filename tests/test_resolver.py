import json
import os
import subprocess
import sys
import unittest

from membership import Cause, MemberRecord, Status, merge_groups
from membership.resolver import (
    OUTCOME_BLOCKED,
    OUTCOME_DISCARDED,
    resolve_member,
)


def rec(member, inc, status, observer=None, version=0, cause="none"):
    return MemberRecord(
        member_id=member,
        incarnation=inc,
        status=Status(status),
        observer=observer or member,
        version=version,
        cause=Cause(cause),
    )


class BidirectionalSuspectTest(unittest.TestCase):
    """分区两侧互相 suspect：自报 alive 且提升 incarnation 的一方能洗清，
    未提升 incarnation 的一方保留 suspect 并进入待确认列表。"""

    def test_bidirectional_suspect(self):
        groups = {
            "A": [
                rec("x", 2, "suspect", observer="y", version=5),
                rec("y", 2, "alive", observer="y", version=5),
            ],
            "B": [
                rec("x", 2, "alive", observer="x", version=6),
                rec("y", 2, "suspect", observer="x", version=4),
                rec("y", 3, "alive", observer="y", version=7),
            ],
        }
        result = merge_groups(groups)

        # x 自报 alive 但未提升 incarnation，无法反驳 suspect
        self.assertEqual(result.view["x"].status, Status.SUSPECT)
        self.assertIn("x", [r.member_id for r in result.pending])

        # y 以更高 incarnation 自报 alive，怀疑被驳回
        self.assertEqual(result.view["y"].status, Status.ALIVE)
        self.assertEqual(result.view["y"].incarnation, 3)
        self.assertNotIn("y", [r.member_id for r in result.pending])


class IncarnationRegressionTest(unittest.TestCase):
    """传播版本较新的 dead 记录若 incarnation 回退，不能覆盖高
    incarnation 的 alive；该记录进入冲突链并被丢弃。"""

    def test_regression_dead_does_not_kill(self):
        records = [
            rec("n1", 5, "alive", observer="n1", version=10),
            rec("n1", 3, "dead", observer="n9", version=99, cause="failed"),
        ]
        resolution = resolve_member("n1", records)

        self.assertEqual(resolution.final.status, Status.ALIVE)
        self.assertEqual(resolution.final.incarnation, 5)
        discarded = [e for e in resolution.chain
                     if e.outcome == OUTCOME_DISCARDED]
        self.assertEqual(len(discarded), 1)
        self.assertEqual(discarded[0].record.status, Status.DEAD)
        self.assertFalse(resolution.needs_confirmation)

    def test_newer_version_alive_does_not_override_leave(self):
        records = [
            rec("n1", 4, "dead", observer="n1", version=8,
                cause="voluntary"),
            rec("n1", 3, "alive", observer="n2", version=50),
        ]
        resolution = resolve_member("n1", records)
        self.assertEqual(resolution.final.status, Status.DEAD)
        self.assertEqual(resolution.final.cause, Cause.VOLUNTARY)


class VoluntaryLeaveTest(unittest.TestCase):
    """主动离开是终态：同 incarnation 的 alive 不能复活节点；
    只有节点自己以更高 incarnation 宣告 alive 才算重新加入。"""

    def test_leave_is_terminal_at_same_incarnation(self):
        records = [
            rec("n1", 4, "dead", observer="n1", version=8,
                cause="voluntary"),
            rec("n1", 4, "alive", observer="n2", version=12),
        ]
        resolution = resolve_member("n1", records)

        self.assertEqual(resolution.final.status, Status.DEAD)
        self.assertEqual(resolution.final.cause, Cause.VOLUNTARY)
        blocked = [e for e in resolution.chain
                   if e.outcome == OUTCOME_BLOCKED]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0].record.status, Status.ALIVE)

    def test_rejoin_requires_higher_incarnation_self_alive(self):
        records = [
            rec("n1", 4, "dead", observer="n1", version=8,
                cause="voluntary"),
            rec("n1", 5, "alive", observer="n1", version=9),
        ]
        resolution = resolve_member("n1", records)
        self.assertEqual(resolution.final.status, Status.ALIVE)
        self.assertEqual(resolution.final.incarnation, 5)

    def test_leave_distinguished_from_failure(self):
        # 故障死亡 + 节点同 incarnation 自报 alive -> 仍需确认
        records = [
            rec("n1", 4, "dead", observer="n2", version=8, cause="failed"),
            rec("n1", 4, "alive", observer="n1", version=9),
        ]
        resolution = resolve_member("n1", records)
        self.assertEqual(resolution.final.status, Status.DEAD)
        self.assertTrue(resolution.needs_confirmation)


class PartitionMergeTest(unittest.TestCase):
    """完整的分区恢复合并：验证最终视图、冲突链与待确认节点。"""

    def setUp(self):
        self.groups = {
            "A": [
                rec("node-1", 5, "alive", observer="node-1", version=11),
                rec("node-2", 4, "suspect", observer="node-1", version=9),
                rec("node-3", 4, "dead", observer="node-3", version=8,
                    cause="voluntary"),
                rec("node-4", 2, "suspect", observer="node-2", version=7),
            ],
            "B": [
                rec("node-1", 3, "dead", observer="node-4", version=20,
                    cause="failed"),
                rec("node-2", 4, "alive", observer="node-2", version=10),
                rec("node-3", 4, "alive", observer="node-1", version=12),
                rec("node-4", 2, "suspect", observer="node-1", version=6),
            ],
        }
        self.events = [
            {"type": "partition_healed", "groups": ["A", "B"]},
        ]

    def test_partition_merge(self):
        result = merge_groups(self.groups, self.events)

        # node-1：B 区版本更新的 dead 记录 incarnation 回退，存活
        self.assertEqual(result.view["node-1"].status, Status.ALIVE)
        # node-2：同 incarnation 下 suspect 优先，且自报 alive 未反驳 -> 待确认
        self.assertEqual(result.view["node-2"].status, Status.SUSPECT)
        # node-3：主动离开不被 B 区的 alive 复活
        self.assertEqual(result.view["node-3"].status, Status.DEAD)
        self.assertEqual(result.view["node-3"].cause, Cause.VOLUNTARY)
        # node-4：两侧都 suspect，合并后仍 suspect，无自报 -> 不需确认
        self.assertEqual(result.view["node-4"].status, Status.SUSPECT)

        pending = [r.member_id for r in result.pending]
        self.assertEqual(pending, ["node-2"])

        conflict_members = [r.member_id for r in result.conflicts]
        self.assertIn("node-1", conflict_members)
        self.assertIn("node-3", conflict_members)

        report = result.to_dict()
        self.assertIn("conflict_chain", report)
        self.assertIn("merged_view", report)
        self.assertIn("needs_confirmation", report)
        json.dumps(report, ensure_ascii=False)  # 报告必须可序列化

    def test_heal_event_selects_groups(self):
        events = [{"type": "partition_healed", "groups": ["A"]}]
        result = merge_groups(self.groups, events)
        self.assertEqual(result.merged_groups, ["A"])
        self.assertEqual(result.view["node-1"].incarnation, 5)


class CliTest(unittest.TestCase):
    def test_cli_on_example(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        example = os.path.join(root, "examples", "partition_merge.json")
        proc = subprocess.run(
            [sys.executable, "-m", "membership", example],
            cwd=root, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(
            report["merged_view"]["node-3"]["status"], "dead")
        self.assertEqual(
            report["needs_confirmation"][0]["member"], "node-2")


if __name__ == "__main__":
    unittest.main()
