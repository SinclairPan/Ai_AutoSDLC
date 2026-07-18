# Lean Code 风险预算与有界质量闭环

Lean Code 用确定性证据约束新功能与 Bug 修复。它是风险预算，不是机械 LOC 门禁；实现 Agent 负责修改代码，Evaluator 与 Reviewer 只评估、生成 findings 和定向 fix plan。

## 范围

- 新功能只实现已确认的需求、P0、验收标准与 tasks。
- Bug 修复只改复现问题与防止回归所需的最小范围。
- 禁止顺手重构、顺手加功能、预建无消费者扩展点、移动代码刷指标、混入格式化噪音，或靠压缩表达式和删除错误处理降低 LOC。

## TDD

新功能与 Bug 修复遵循 `RED → GREEN → REFACTOR → VERIFY`。Bug 修复必须有先失败后通过的可执行回归证据；没有新增测试时，必须用结构化证据证明现有测试先失败并能捕获该 Bug。使用 `lean-regression --phase red` 与 `--phase green` 无 shell 执行同一 argv；运行时 receipt 绑定 source snapshot、退出码、stdout/stderr、测试源码和工具链。只有退出码、命令、手写输出或自制 JSON 的记录无效。

## 400/50

手写产品文件 400 行、函数 50 行是初始可维护性预算，不是语义边界：

- 未变更历史超限代码只报告，不阻断。
- 新增或显著修改代码单独超限为 ADVISORY。
- 超限同时伴随复杂度、重复、耦合、职责混杂或范围蔓延时为 REQUIRED。
- 行为、安全、授权、兼容、状态、副作用或验证合同损坏为 BLOCKER。
- generated、fixture、vendored、snapshot 与 declarative 文件独立分类，不进入手写产品硬预算。generated/vendored 路径还必须有生成头或上游 provenance；仅靠目录名或后缀不能豁免手写代码。
- unknown 或无法可靠测量的语义指标进入 needs_user，不按零风险处理。

## 公共抽象

新增公共抽象至少要有 3 个当前真实、语义一致的产品调用者。少于 3 个调用者时使用局部私有 helper 或清晰直接实现。动态调用、外部 API、框架入口和无法可靠解析的 caller 不得机械判为零调用者。

## 单一真值与薄入口

版本、schema、路径、阶段、规则 token、默认值与状态定义只有一个 canonical source。CLI、Controller 与 Command Handler 只解析输入、检查授权、调用领域服务、渲染结果和映射退出码；评估算法放在小型领域模块。

## 行为与注释

LOC 下降不能抵消功能完整性、错误处理、安全授权、CLI/API 与 artifact 兼容、状态迁移、并发事务、审计证据和测试覆盖。纯移动代码不算减重。新增注释只解释复杂意图、边界、兼容、并发、缓存、错误处理和非显然业务约束，并遵循项目语言约定。

## 严重度、例外与关闭

- BLOCKER：损坏或过期 artifact/policy/input、未批准 scope drift、验证失败、行为或安全合同破坏、无效例外。
- REQUIRED：size 与其他风险共同恶化、Bug 修复缺回归证据、无消费者公共抽象、未使用扩展点或明显范围蔓延。
- ADVISORY：单独 400/50、未修改历史债务、非阻断可读性或减重机会。

例外必须绑定 finding、scope、policy、commit、diff、evaluation 与证据 digest。close 与 PR 入口会重新读取例外和证据；删除、替换或过期都会使旧报告失效。例外不隐藏 finding；接受例外后的结论是 `risk_accepted`，不是 `fully_clean`。

`report` 模式只报告非完整性 REQUIRED，`warning` 模式要求定向修复，`blocking` 模式将未解决 REQUIRED 置为 blocked；artifact 完整性、scope drift、验证失败和无效例外在所有模式下都 fail-closed。400/50 单独超限始终只是 ADVISORY。

## 有界 Loop

Lean evaluation 属于 Implementation Loop，不是新的顶层 Loop。默认最多两轮：第一次评估生成 findings 与 fix plan，Implementation Agent 定向修复后用 `lean-verify --loop-id <id> --test-source <path> -- <argv>` 真实执行验证。声明的 test source 必须由可解释的受控 runner adapter 实际执行；只把路径放进 `python -c` 普通参数、ignore/config 参数或输出文本无效。把 receipt 路径记录为 Implementation evidence；只新增一条未执行的命令字符串不算验证。第二次评估重新绑定当前 diff。相同 BLOCKER/REQUIRED 两轮后仍存在则进入 `needs_user`。如果修复只能破坏行为或成本大于收益，使用 `needs_user` 加结构化 No-Go 原因。Loop 不自动修改用户代码，也不会无限迭代。

Local PR Reviewer 必须独立于实现 Agent，并消费绑定当前 head、diff、snapshot、policy、findings 与 evaluation input 的 fresh Lean report。内置证据证明 reviewer 在独立进程和独立输入包中运行，不冒充“不同人类身份”；需要职责分离的团队还应配置不同账号/provider，并在外部治理系统保留 actor/session 记录。CI 只验证确定性 artifact，不调用模型或自动修复。
