# 共享判读模式复核

盲检中心在配样时固定可能使用的判读模式及先验。同批各站实际共用一个模式，中心出报告时仍不知道是哪一个。站端可在揭晓前发行证据，交班人员可仅凭中心库和固定站件完成签发。普通批继续按 README.md 的 V1 流程运行。

## 配样与站端输入

- 在 `campaign.manifest` 提供 `register_modes(campaign, batch_id, allocations, sealed_count, modes)`。批号、`StationAllocation`、站额排序及密封总数沿用 V1。`modes` 是非空有序列表，每项形如 `{"name": "standard", "prior": "1/2"}`。模式名非空且互异，先验严格为正，精确总和为一。普通批和模式批使用同一中心批号空间。模式批同号重送时，站额、K、模式名称顺序及精确先验全部相同才返回原清册。登记冲突拒绝，保留原揭晓状态。
- 模式清册为可直接传递的 JSON 对象，字段为 `batch_id`、`allocations`、`modes`。后两项分别使用 V1 站额对象列表和上述模式对象列表。先验以精确分数字符串输出。公开清册中无密封总数。两类批次均通过现有 `campaign.reveal.reveal` 揭晓。
- 在 `observations.revision` 提供 `freeze_modes(observations, manifest, station_id, rows)`，返回本地正整数修订号。`manifest` 可由站端读取清册 JSON 得到。每行含唯一非空 `trial_id` 和 `mode_weights` 列表。列表项含 `mode`、`normal`、`defect`，按名称对应清册，每个模式恰一项。试片数等于本站容量。权重是非负有理数，可用非布尔整数、有理数字符串或 `Fraction`。零权重，包括某模式下同一试片两项皆零，表示相应证据质量为零。更正时提交完整新修订，旧修订保留原证据及清册。
- 在 `station.issuance` 提供 `issue_modes(observations, station, revision_id)`，返回固定模式站件的 JSON 文本。同一修订重复发行返回原文。逐片身份和逐片权重留站。

## 站件交换与接纳

模式站件绑定固定公开清册、批号、站号、容量、发行号及来源修订。它交接每个模式下从零到本站容量的全部计数证据。某计数的证据为本站所有该计数分配的权重之和，每个分配乘入各片在该模式和实际缺陷状态下的权重。各模式的计数证据须可在同一站尺度下比较，零项和零质量模式仍有明确位置。

在 `receipt.intake` 提供 `receive_modes(campaign, receipts, batch_id, envelope)`，返回中心接纳号。用中心登记的固定清册核对批号、站额及模式依据。接纳身份沿用批号、站号、发行号。相同身份下，清册、容量和来源修订相符且全部模式的全部计数权重相差同一正因子时，返回原接纳号并保留首次接纳原文。全零证据组只与相同元数据的全零证据组等价。其他同身份内容拒收。在同模块提供 `read_mode_envelope(receipts, receipt_id)`，返回该号首次接纳的 JSON 文本。

交付说明须记录站件 JSON 格式及其尺度约定，使独立站点能生成有效站件和同身份的等比重投件。站件载有整数版本号 `2`。格式应能表达同一证据的不同整站尺度。

## 签发与领取

在 `review.publication` 提供 `publish_modes(campaign, receipts, reports, batch_id, receipt_ids, thresholds)`。接纳号与 `Threshold` 沿用原规则及请求次序。中心须使用显式给出的每站一份同清册模式站件，混入 V1、错清册、重复或缺站均拒绝。门槛为零时该事件必达，超过本站容量时不可达。所有选中站都参与全批总数约束。

给定已揭晓的 K，每个模式下大小为 K 的缺陷子集先验等可能。每次全批分配只使用一个共享模式。以该模式的先验乘全部站的相容证据，保留各模式质量。两项有序边缘、共同事件和模式后验使用同一个全批条件分母。某模式相容质量为零时其后验为零。全批相容质量为零时拒发。允许全量重算和复用 V1 计数枚举。

签发返回 `(report, receipt)`。报告提供 `report_id`、`batch_id`、`manifest`、`defect_count`、`receipt_ids`、`thresholds`、`probabilities` 和 `mode_posterior`。`manifest` 为签发时的模式清册，`receipt_ids` 和 `thresholds` 为按原请求顺序的元组。`probabilities` 使用 V1 `RiskProbabilities`，`mode_posterior` 是按清册顺序的 `Fraction` 元组。回执使用原 `PublicationReceipt`。报告与回执同次提交。在 `review.readback` 提供 `fetch_modes(reports, report_id)`，按号领取原报告和回执，更正、重开后直接读取存值。

仓储初始化和连接使用方式沿用 README.md。非法输入、错误站件类型、冲突、未揭晓或不相容报 `ValueError`，未知编号报 `LookupError`。登记、冻结、发行、接纳和签发的写入错误沿原语义传播。本次失败不留部分成果，解除故障后可重试。保留 V1 的接口、旧件及全部原测试。

## 交班样例

`samples/shared-modes.json` 给出可手算的配样、站端输入和两次签发。两站各一片，K 为 1。原证据在 `standard` 模式下的甲乙命中权重为 81 和 1，在 `backup` 下为 1 和 9。两模式先验各半且两站不可能同时达标，故风险为 `(41/46, 5/46, 0)`，有序后验为 `(41/46, 5/46)`。只加倍甲站 `standard` 权重并用新修订发行后，风险为 `(163/174, 11/174, 0)`，后验为 `(82/87, 5/87)`。同样的改动用于原发行身份时应拒收，共同放大原件的所有模式则应返回原接纳号。两站若在某模式下均只支持零缺陷，该模式在 K 为 1 的报告中后验为零。

新增入口完成后，`python3 -B mode_example.py` 可演示分别关闭站库后接纳及签发，随后更正并重开中心库按原号领取。可参照 `tests/test_readback.py` 的延迟外键场景核对真实提交失败和旧件读取。

沿用 `.pointwise-runtime.json` 的版本 1 和 cli 类型。`setup` 是命令参数数组的列表，`verify` 与 `demo` 各为一个命令参数数组。`verify` 执行原回归与新增联动核验，并在核验失败时返回非零退出码。`demo` 展示断源签发的模式后验、双站风险和重开领取结果。交付时简述站件尺度和报告条件分母的交接约定。
