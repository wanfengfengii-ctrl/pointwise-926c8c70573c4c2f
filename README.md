# 清泉流量脉冲试验站

清泉流量计试验室的操作员需要在班中人工读清计数器。清零后，寄存器里的旧数已消失。站库保全每次点检的原码和首执，夜班质检员可在台架停用后据此核对整班流量。

## 一班的工作

1. 准备员用 `configure(device_id, revision, microliters_per_pulse)` 登记设备及具名定标。每脉冲系数为正整数微升。原参重投复领，异参拒绝。
2. 操作员用 `begin(shift_id, device_id, revision, limit_microliters)` 冻结定标实值及非负阈值并开一份空录制。原参重开复领原班，设备活动期间拒绝另开同设备的新班。
3. 脉冲源用 `count(recording_id, pulses)` 送入非负整数脉冲。
4. 值班员用 `inspect(recording_id, action_id)` 读清，保存不可变原件。新点检从设备序位 1 开始递增，零值照常有件。重领动作不再取数。
5. 用 `accept(evidence)` 逐件交给站库。可乱序和重投，首件与首执同时提交。错绑或同身份异内容拒绝。
6. 用 `stop(recording_id, action_id)` 停采并取末件，再将末件送站。末件原码是停采前的余量，末件序位确定应收范围。停后拒绝新脉冲或新动作，原动作可复领。
7. 用 `publish(shift_id)` 签班。`awaiting_stop` 表示末件未入站，`missing` 携带确切缺序位，`signed` 携带固定班报。各实收原码求和后乘冻结系数得到整数微升，严格大于阈值才超限。提交失败保留已实收原件，成功后按原报告号复领。
8. 夜班打开归档并用 `readback(database, report_number)` 按号领取。领取结果含原班报及逐件原据、首执。核账从固定引用出发，用开班快照复算。缺件、错绑和计量不一致均拒绝核定。

## Python 接口

`station.setup.bootstrap.open_station(path)` 打开或初始化本地 SQLite 站库并返回 `StationContext`。步骤 1 至 7 可直接调用同名方法。设备动作返回 `SourceEvidence`，实收返回 `Receipt`，开班返回含 `recording_id` 的 `ShiftSnapshot`。参数或状态冲突报 `ValueError`，未知设备、班号、修订或录制报 `LookupError`，SQLite 写入或提交错误向调用方传播。

一个在线台架共享一份 `SessionDirectory`。重复初始化保留用户通过接口追加的修订及已持久化的班次、实收和报告。在线装配可通过 `sessions=` 重用同一活设备目录。重新创建设备目录会产生一个独立的设备运行环境，旧班次同参重领返回持久化身份。设备内的寄存器和动作簿由原设备运行环境持有。班内诊断可观察 `sessions.require(recording_id).register.read()`，原语 `read_clear()` 会真实清零。生产点检和停采用各自业务入口保存读清原件。

`station.setup.bootstrap.open_archive(path)` 建立只读站库入口。`station.audit.readback.readback` 使用该入口核账。未知报告或已签口径异常报 `VerificationError`，固定引用解析失败报 `ProvenanceError`。

## 运行

使用 Python 3.11 标准库，从项目根目录执行。

```sh
python3 -B check_project.py
python3 -B -m examples.demo
```

控制台物化时提供 `check_project.py`。直接运行完整 `unittest` 套件可用 `python3 -B -m unittest discover -s tests -t . -v`。

`examples/shift.json` 固定输入和投递次序。以 2 微升每脉冲开班，登记新系数 3 后，原班仍按 2 换算。先加 7 脉冲，点检得到 7 和 0，停采得到 0。按 3、1、2 投递时，先缺 1、2，再缺 2。实际齐备后签出 7 脉冲、14 微升，等于阈值故不超限。样例断开设备上下文后按签出的号码领取并核对原序出处。号码是首签时生成的不透明标识，样例核对其复领等同性。用例和样例使用各自的临时站库。
