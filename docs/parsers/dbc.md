# DBC 解析覆盖报告（DATA-01）

- 解析器：`zlg_ai_test_engine.plugins.parser_dbc.DbcParser`（`parser_id="dbc"`，`parser_version="1.0.0"`，支持 `.dbc`）
- 底层实现：cantools 45.2.0（MIT）；**cantools 对象只在解析函数内部出现**，
  `ParseResult.model` 与查询接口返回值均为本项目 `NetworkModel` 序列化的纯 dict / 项目类型
  （测试 `TestNoCantoolsLeakage` 递归断言类型名与模块名不含 "cantools"）
- 统一模型：`zlg_ai_test_engine.domain.network.model.NetworkModel`
- 查询/编解码：`zlg_ai_test_engine.domain.network.queries.NetworkQueries`
- 样例：`examples/dbc/demo_vehicle.dbc`（合成数据，非厂商材料）、
  `examples/dbc/corrupt_bad_syntax.dbc`（故意损坏）
- 测试：`tests/unit/test_dbc_parser.py`

## 1. 支持字段（逐项）

### 1.1 文件元信息

| 字段 | 来源 | 说明 |
| --- | --- | --- |
| `database_name` | 文件名主干 | DBC 无显式数据库名字段，取 `path.stem` |
| `version` | `VERSION "..."` | 缺失为 `None` |
| `sha256` | 源文件字节流 | 与 `ParseResult.source_sha256` 一致，同文件稳定 |
| `parser_id` / `parser_version` | 解析器自身 | `"dbc"` / `"1.0.0"` |
| 解析时间 | 诊断 `parse_completed`（INFO） | `duration_ms=… parsed_at=…`（含节点/报文/信号计数） |
| 警告 | `ParseResult.diagnostics` | 见第 2 节；demo 样例解析为零警告 |

### 1.2 报文（Message）

| 字段 | DBC 来源 |
| --- | --- |
| `message_name` | `BO_ <id> <name>:` |
| `frame_id` | `BO_` 帧 ID（**裸 ID**，不含 0x80000000 扩展标志位） |
| `is_extended` | 帧 ID 最高位（0x80000000）判定 |
| `dlc` | `BO_` 数据长度 |
| `sender` | `BO_` 发送节点（`Vector__XXX` 视为无发送者 → `None`；多发送者取首位） |
| `cycle_time_ms` | `BA_ "GenMsgCycleTime" BO_ …` 属性；属性缺失为 `None` |
| `comment` | `CM_ BO_ <id> "..."` |
| `signals` | 全部 `SG_` 信号 |

### 1.3 信号（Signal）

| 字段 | DBC 来源 |
| --- | --- |
| `signal_name` | `SG_ <name>` |
| `start` / `bit_length` | `SG_ <start>\|<len>@…`（DBC 位编号语义：intel 起始位为最低有效位，motorola 起始位为最高有效位） |
| `byte_order` | `@0` → `motorola`（big-endian），`@1` → `intel`（little-endian） |
| `is_signed` | `@…+/-` |
| `initial_value` | `BA_ "GenSigStartValue" SG_ …` 原始值经 scale/offset 换算为物理值；缺失为 `None` |
| `scale` / `offset` | `SG_ (factor, offset)` |
| `minimum` / `maximum` | `SG_ [min\|max]` |
| `unit` | `SG_ "unit"`（空单位为 `None`） |
| `receivers` | `SG_ … <接收节点列表>` |
| `choices` | `VAL_ <frame_id> <signal> …` 值表 `{int: str}`；无值表为空 dict（**键保持 int**） |
| `comment` | `CM_ SG_ <id> <signal> "..."` |

## 2. 未识别字段 / 异常的处理（绝不静默丢弃）

解析器在 cantools 之上叠加**原文行扫描 + 模型交叉核对**，所有诊断带
`文件名:L行[:C列]` 定位，按出现行号稳定排序（同输入必得同输出）：

| 代码 | 级别 | 触发场景 |
| --- | --- | --- |
| `parse_error` | ERROR | cantools 无法解析（语法损坏、cantools 不支持的顶层构造等）；消息含上游原始错误与行:列，`model={}` 但 `source_sha256` 仍计算 |
| `unrecognized_content` | WARNING | 扫描器发现不属于任何已知 DBC 关键字的行（对上游的双保险；顶层垃圾行通常先被 cantools 报为 `parse_error`） |
| `unsupported_feature` | WARNING | cantools 可解析、但 `NetworkModel` 不表达的构造：`VAL_TABLE_`、`SIG_VALTYPE_`、`SG_MUL_VAL_`、`BO_TX_BU_`、`SIG_GROUP_`、`EV_*`、`CAT_*`、`FILTER`、关系属性（`BA_*_REL_`）、`CM_` 数据库级注释、`CM_ EV_`、SG_ 复用标记（`M`/`mN`）、`VECTOR__INDEPENDENT_SIG_MSG` 等；同关键字按首次出现行号去重并计数 |
| `unmodeled_attribute` | WARNING | `BA_` / `BA_DEF_` / `BA_DEF_DEF_` 中除 `GenMsgCycleTime`、`GenSigStartValue` 外的属性（同名去重，消息含"共 N 处"） |
| `orphan_val_table` | WARNING | `VAL_` 引用不存在的报文/信号、或缺少 frame id —— 这些情况**上游解析器会静默忽略**，由本项目交叉核对补抓 |
| `encoding_replacement` | WARNING | 字节在 cp1252 中未定义，替换为 U+FFFD 后继续解析（不静默替换） |
| `upstream_warning` | WARNING | cantools 解析期间抛出的 Python 告警（透传类别与消息） |
| `parse_completed` | INFO | 解析元信息：节点/报文/信号计数、`duration_ms`、`parsed_at` |

`NS_ :` 与 `BS_:` 块按结构跳过（NS_ 块内为符号清单，非数据）；`//` 行注释与
cantools tokenizer 语义一致地跳过。

## 3. 已知限制

1. **浮点信号**：`SIG_VALTYPE_` 声明的浮点信号不进入模型（生成
   `unsupported_feature` 警告）；对该类信号按整数位布局 decode/encode **语义不正确**，
   含此类声明的 DBC 不建议用于编解码。
2. **复用（multiplexed）信号**：SG_ 复用标记与 `SG_MUL_VAL_` 关系不建模（警告）；
   模型按普通信号处理，decode/encode 不感知复用状态。
3. **cantools 45.2.0 语法上限**：`CAT_`/`CAT_DEF_`/`FILTER`/`SGTYPE_`/
   `SIGTYPE_VALTYPE_`/`BA_DEF_SGTYPE_` 等即使为标准构造，上游 grammar 也不接受 →
   整体 `parse_error`（带行定位）。属上游能力边界，升级 cantools 可缓解。
4. **多发送者/发送类型**：`BO_TX_BU_`、`GenMsgSendType`、`GenSigPhysicalRange` 等
   仅告警不建模；`sender` 取首位。
5. **环境变量、信号组、关系属性**：不建模（均告警）。
6. **语句跨行**：扫描器按引号感知的 `;` 收集语句，正常 DBC 均可处理；若文件在
   EOF 处语句未终止，该语句整体按关键字归类告警。
7. **编码**：按 DBC 惯例以 cp1252 解码（与 cantools 默认一致）；其他编码的字节
   会被替换并生成 `encoding_replacement` 警告。
8. **encode 精度**：物理值→原始值按 `round((v-offset)/scale)`（与 cantools 同式，
   Python banker 舍入）；恰好落在 .5 分界的换算差异未做特殊处理。
9. **encode 默认值**：未提供的信号按**原始值 0** 编码（不使用
   `initial_value`），位宽越界抛 `ValueError` 而非静默截断。

## 4. 交叉验证状态（如实记录）

- **已执行**：`tests/unit/test_dbc_parser.py` 以 **cantools 自身 decode/encode
  作为参照**进行交叉验证——Golden 向量的解码结果与 cantools `message.decode()`
  逐项相等，编码字节与 cantools `message.encode()` 逐字节相等，覆盖 Intel/
  Motorola、Signed/Unsigned、跨字节、Factor/Offset、枚举值表与上下边界。
- **待人工执行（V 级）**：与**外部基准工具**（Vector CANdb++、PCAN-Explorer、
  Kvaser Database Editor 等）的逐字段/逐帧对照**尚未执行**——本机无此类授权工具。
  该项为任务书中标注的 V 级人工验证项，**不得视为已对照**。建议对照清单：
  各信号起始位/位序/符号性、factor/offset、min/max、值表、扩展帧 ID、
  GenMsgCycleTime 属性读数，以及 demo 样例 3 组 Golden 帧的解码值。
