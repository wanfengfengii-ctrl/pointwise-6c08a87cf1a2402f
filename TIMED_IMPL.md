# 定时交班实现说明

本说明对照 `TIMED.md`，记录版本 2 定时站件的资格表示、样品次序与交接不变量。
V1 的空间核验入口、库表与成果行为保持不变，新功能全部通过新增入口与新增库表接通。

## 1. 录入、冻结与后继草稿

- `intake.record_timed(db, key, record_id, start, end, depart, arrive)` 写入独立的
  `timed_transfers` 表。时刻必须是 `type(x) is int` 的非负整数（布尔值拒绝），
  且 `depart <= arrive`，否则抛 `ValueError('INVALID_TIME')`。
- 定时记录与无时刻记录共用同一编号空间：同号重报只有在端点和四个字段完全一致时
  返回原 `TimedTransfer`；改端点、改时刻或与另一类记录撞号均抛 `RECORD_CONFLICT`。
- `withdraw_record` 同时覆盖两类记录；定时记录可先撤下再用无时刻入口重登，反之亦然。
- 冻结快照按记录类型序列化：无时刻记录仍是三字段，定时记录附带 `depart`、`arrive`。
  `read_revision` 据此还原，`open_successor` 通过统一的 `populate_draft` 保真复制。
- 定时出处链只由定时记录组成。相邻两笔要求前笔 `arrive <=` 下笔 `depart`
  （相等允许），不满足时该走法不进入定时资格；全图无环检查仍对全部记录生效。
  仅用于空间核验的无时刻记录继续参与 V1 计算，不进入定时走法枚举。

## 2. 站件资格表示（version 2）

外层沿用 V1 的 `version`、`id`、`station`、`batch`、`revision`、`boundaries`、
`eligibility` 七字段，`version` 为整数 `2`。站件只携带公开端点、站界时刻和匿名相容
事实；站内记录编号、内部节点、逐条路线均不出现在载荷中。

`eligibility.singles` 逐项表达一条有向公开关系 `[起点, 终点]` 的可实现站界时刻：

- `front`：本站作前站时的末笔接收秒集合（关系起点到 `out` 接口）；
- `rear`：本站作后站时的首笔发出秒集合（`in` 接口到关系终点）。

同一关系可能两个角色都可用，两个数组分别给出；一次查验在每站只取一个角色。

`eligibility.pairs` 表达双件在原记录互斥前提下可共同实现的整组时刻：

- `relations` 是两条（允许相同的）公开关系，按文本字典序规范化存储；
- `front` 是与 `relations` 同序的 `(甲末笔接收秒, 乙末笔接收秒)` 元组列表；
- `rear` 是同序的 `(甲首笔发出秒, 乙首笔发出秒)` 元组列表。

只有当存在一对记录集合互不相交的链时，对应时刻组合才会列入。端点、时刻相同但编号
不同的记录视为独立记录，因此同接口、同时刻的双件可以成立。接纳端只校验结构、公开
引用和内部一致性（时刻必须能在对应单件角色中找到、关系必须已在 `singles` 中），
互斥事实由发布者从原记录核定。载荷经规范化编码，字段顺序与空白不影响同号重报。

## 3. 样品次序与选择规则

`results.issue_timed(db, request, max_wait)` 沿用原 `ReviewRequest` /
`SampleRequest`，样品次序即请求元组次序，不做任何重排。

1. 两个父件必须是同批、相邻站的已接纳 **定时** 件；V1 父件或缺失件按原异常拒绝，
   非法 `max_wait`（非非负整数、含布尔）抛 `ValueError('INVALID_MAX_WAIT')`。
2. 逐件在两站共用同名互认接口。对每个候选接口元组：
   - 单件在两个站件的 `singles` 中按前/后角色取时刻；
   - 双件在两个站件的 `pairs` 中取整组时刻，保证两链原记录互斥。
3. 每件等待 = 后站首笔发出秒 − 前站末笔接收秒，必须满足
   `0 <= 等待 <= max_wait`，两端含等号。
4. 多组可行时先按接口元组的文本字典序，再按请求次序排列的
   `(前站接收秒, 后站发出秒)` 序列字典序取首项。交换样品顺序时，关系和时刻随样品
   整体交换。
5. 无任何共同见证抛 `NO_COMMON_EVIDENCE`；拒签、写入或提交失败都不产生成果或成功
   回执，SQLite 异常原样传播。

## 4. 成果、摘录与复领

定时成果复用原成果号与回执号空间，快照额外固定 `max_wait` 与按样品顺序排列的
`handoffs`。`IssuedResult` 以默认值扩展 `max_wait=None`、`handoffs=None`，V1 成果
对象字段与相等性不变。`export_excerpt` 对定时件增加顶层整数 `max_wait`，并在每个
样品项增加整数 `front_arrive`、`rear_depart`；V1 摘录结构原样保留。所有领取只读已签
快照，因此关闭源库、修订更新、重开夜班库后，`fetch` 与 `export_excerpt` 仍返回同一
内容。

## 5. 运行入口

- `python3 -B timed_cases.py verify`：先跑 `check_project.py` 的完整 unittest 回归
  （含原 V1 套件与 `tests/test_timed.py`），再跑定时样例。
- `python3 -B timed_cases.py demo`：先演示 V1 空间核验，再演示定时单签、断源拒签、
  平行记录双签、错峰时刻选择、写入失败回滚与重开复领。
- `python3 -B timed_cases.py v1`：仅核对 V1 空间资格与定时夹具的 V1 投影一致。
