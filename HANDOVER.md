# 已签班报离线交接

同一试验室的两站各有站库。收档站在收包前已登记与源站完全相同的开班快照，含设备、录制、定标实值和限值。交班走双方信任的离线渠道，包内首执来自源站原签班报。交接从已停采并签发的录制开始。

## 调用和交班包

在 `station.report.handover` 提供以下 Python 接口。

- `export_handover(database, report_number) -> dict` 从源站已签班报导出原报告和经核对的原据。源库可通过 `open_archive` 只读打开。未知报告或原据异常沿用原核账异常。
- `import_handover(database, package) -> str` 在可写收档站库整包接纳，返回包内原报告号。等价重投返回原号。坏包、错绑及业务冲突报 `ValueError`，SQLite 写入及提交错误沿原接口传播。
- 原有 `readback(database, report_number)` 同时支持按号领取本站报告和已接纳的外站报告，两者均返回原 `ReadbackResult`。外站报告保留源站的全部原引用与首执。重开时可直接用 `open_archive` 按号领取。

交班包是可用 JSON 往返的字典。对象成员如下，名称沿用 V1 数据类。对象键可换序，引用和原据数组的顺序有意义。身份字段为非空白字符串。所有数量，包括序位，使用现有 `encode_quantity` 的无损十六进制字符串。`terminal_sequence` 在点检件中为 JSON null，`over_limit` 为 JSON 布尔值。

| 位置 | 成员 |
| --- | --- |
| 根对象 | `report`、`entries` |
| `report` | `identity`、`shift`、`totals`、`decision`、`references` |
| `identity` | `number`、`shift_id`、`recording_id` |
| `shift` | `shift_id`、`device_id`、`recording_id`、`revision`、`microliters_per_pulse`、`limit_microliters` |
| `totals` | `pulses`、`microliters` |
| `decision` | `limit_microliters`、`over_limit` |
| `references` 的每个成员 | `recording_id`、`sequence`、`action_id`、`receipt_id` |
| `entries` 的每个成员 | `evidence`、`receipt` |
| `evidence` | `shift_id`、`device_id`、`recording_id`、`sequence`、`action_id`、`kind`、`pulses`、`terminal_sequence` |
| `receipt` | `receipt_id`、`recording_id`、`sequence`、`action_id` |

例如只收到零原码末件的班报可按下列形式交接。收档站须已有此开班快照，导入返回 `R-example`。按原号复核时，原码合计和微升量均为零，源站首执是 `origin-1`。

```json
{
  "report": {
    "identity": {"number": "R-example", "shift_id": "day", "recording_id": "rec-example"},
    "shift": {"shift_id": "day", "device_id": "bench", "recording_id": "rec-example", "revision": "r1", "microliters_per_pulse": "2", "limit_microliters": "e"},
    "totals": {"pulses": "0", "microliters": "0"},
    "decision": {"limit_microliters": "e", "over_limit": false},
    "references": [{"recording_id": "rec-example", "sequence": "1", "action_id": "stop", "receipt_id": "origin-1"}]
  },
  "entries": [{
    "evidence": {"shift_id": "day", "device_id": "bench", "recording_id": "rec-example", "sequence": "1", "action_id": "stop", "kind": "stop", "pulses": "0", "terminal_sequence": "1"},
    "receipt": {"receipt_id": "origin-1", "recording_id": "rec-example", "sequence": "1", "action_id": "stop"}
  }]
}
```

## 收档与本站实收

档案按同一录制从序位 1 到唯一末件排列。每个引用须恰好对应同位置的原件及外站首执，包内动作号与首执号各自唯一。核对班次、设备、录制及全部冻结条件，再按原口径用原码求精确班量和限值判定。任何报告号冲突均拒绝，包括已被本站班报占用的号码。外站报告与同班的本站报告各以原号领取。

导入时核对本站已收原件，本站后续实收也须核对已导入原件。两侧共用同一录制内的序位、动作号和末件裁决，原件一致时两站仍各有自己的实收首执。本地已有序位超出包内末件时，整包拒绝。外站末件成立后，本地超界或冲突件拒收。首次本地接纳同一原件照常生成本站首执，本地重投返回本站原首执。本站 `publish` 的齐备状态只取决于本站实际接纳的原件。导入只有在整包提交成功后才生效。导入被拒或提交失败后，原可领报告、本站实收和后续来件裁决保持原状。

## 双站样例

`examples.handover_fixture.make_fixture(directory)` 在临时目录中备好两个站库。它从开班后、首收前复制冻结快照。仅源站投递并签报。返回值包含 `source`、`target`、`shift`、原序 `evidence`、`source_report` 与挂在收档库上的 `fault`。用 `fault.arm('import_handover')` 模拟导入提交失败，沿用原 `before_commit` 注入。

源站按 3、1、2 到件，原序原码为 7、0、0，班量 14 微升，等阈判未超限。收档站未在本站收件时，导入后仍返回 `awaiting_stop`。本地先收末件则报告缺 1、2，补齐后按自己的首执签出本站报告。先导入时，同身份第 1 件改为 8 要拒收，原样 7 可首收。反过来若本站已先收 8，导入要拒绝，原首执仍可重投复领。关源并重开收档库后，外站报告和本站报告分别核回各自实际采用的首执。

运行 `python3 -B -m examples.handover_fixture` 可看到两站独立生成首执的 V1 样例。交付仍用 `.pointwise-runtime.json` 的核验和演示入口，跑全套原回归并覆盖上述两种到件次序及失败续办，保留原演示并展示停源后分别领取双报。
