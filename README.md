# gossip-membership-conflict-resolver
解决 Gossip 成员版本、疑似失败和分区合并冲突

## 说明

本项目**只模拟成员状态**（membership state）的合并与冲突裁决，
不模拟真实的网络传输、消息丢失、心跳时序或故障检测器本身。
输入是分区两侧各自的成员表快照与分区恢复事件，输出是合并后的
最终成员视图、冲突链和仍需人工确认的节点。

## 运行环境

- Python 3.8.2+（仅标准库，无第三方依赖）

## 输入格式

JSON 文件，包含各分区的成员表 `groups` 和事件列表 `events`：

```json
{
  "groups": {
    "A": [
      {"id": "node-1", "incarnation": 5, "status": "alive",
       "observer": "node-1", "version": 11}
    ],
    "B": [
      {"id": "node-1", "incarnation": 3, "status": "dead",
       "observer": "node-4", "version": 20, "cause": "failed"}
    ]
  },
  "events": [
    {"type": "partition_healed", "groups": ["A", "B"]}
  ]
}
```

每条成员记录字段：

- `id`：成员 ID
- `incarnation`：成员自身的逻辑时钟（自报状态时才递增）
- `status`：`alive` / `suspect` / `dead`
- `observer`：该状态的观察者（等于 `id` 时表示节点自报）
- `version`：传播版本，只在同一 incarnation 内做排序
- `cause`：仅 `dead` 有效，`voluntary`（主动离开）或 `failed`（故障判定）

## 合并规则

1. **incarnation 优先于传播版本**：版本较新不代表删除状态一定正确。
   incarnation 回退的记录（版本虽新但时钟倒退）直接丢弃并记入冲突链。
2. **主动离开是终态**：`dead + voluntary` 不被同 incarnation 或更低
   incarnation 的 alive/suspect 复活；只有节点自己以更高 incarnation
   自报 alive 才视为重新加入。人工离开与故障判定严格区分。
3. **故障死亡压过同 incarnation 的 alive/suspect**；若节点在同一
   incarnation 自报 alive，无法自动裁决，标记为“仍需确认”。
4. **suspect 优先于同 incarnation 的非自报 alive**；节点要洗清怀疑
   必须提升 incarnation 后自报 alive。未反驳的 suspect 保留并标记
   为“仍需确认”，避免永久怀疑健康节点。

## 使用

```bash
python -m membership examples/partition_merge.json
cat examples/partition_merge.json | python -m membership
```

输出 JSON 报告：

- `merged_view`：合并后的最终成员视图
- `conflict_chain`：每个冲突成员的裁决链（每条记录的取舍及原因）
- `needs_confirmation`：仍需人工确认的节点及原因

## 测试

```bash
python -m unittest
```

覆盖双向 suspect、incarnation 回退、主动离开（终态与重新加入）、
故障死亡与自报存活冲突、分区恢复合并及 CLI 冒烟测试。
