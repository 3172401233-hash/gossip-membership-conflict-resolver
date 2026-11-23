"""命令行入口：读取分区成员表 JSON，输出合并报告。

用法：
    python -m membership input.json
    cat input.json | python -m membership
"""

import json
import sys

from .model import MemberRecord
from .resolver import merge_groups


def load_input(stream):
    data = json.load(stream)
    groups = {
        name: [MemberRecord.from_dict(item) for item in records]
        for name, records in data.get("groups", {}).items()
    }
    events = data.get("events", [])
    return groups, events


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv:
        with open(argv[0], "r", encoding="utf-8") as stream:
            groups, events = load_input(stream)
    else:
        groups, events = load_input(sys.stdin)
    result = merge_groups(groups, events)
    json.dump(result.to_dict(), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
