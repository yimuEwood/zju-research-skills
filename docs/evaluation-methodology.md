# 评测方法与证据边界

本文说明仓库中每类数字实际测量什么、不能证明什么，以及下一版评测准备解决哪些问题。它是公开披露文件，不是排行榜声明。

## v0.7 协议 v3：统一覆盖 20 项

协议 v3 不再把历史上不同设计的 7 项三臂评测、12 项单臂开发回归和 Director 路由测试拼成一个分数。20 个 Skills 现在共用五能力检查表与四层证据结构：L1/L2/L3/L4 权重依次为 5%/20%/40%/35%，每项最少 6/20/12/15 个检查或案例单位。

当前已实际完成的是 L1：20 项、每项 6 个必检点，共 120/120 通过，绑定 153 个输入文件的哈希。它只支持“工程契约完备”的结论。L2 确定性功能、L3 已知任务三臂对照与 L4 独立冻结留出尚未全部执行，因此官方单项分和组合分均为 `null`。

v3 评分器采用 fail-closed 证据门：记录必须绑定固定协议、能力矩阵、Skill commit、案例/fixture、回答、gold criteria 和评分调用；最终分从维度评分或裁决重算，三臂回答不得复用，基线须在运行前从声明候选中固定。Stable 还要求由预登记 Ed25519 公钥签名的独立验证包，覆盖冻结清单、首次作答登记、盲法分配与揭盲、时间顺序和工具一致性。攻击测试已经确认：即使伪造全部 20 项为 100 分，只要缺少这些材料，评分器仍返回 invalid/Beta，不会给出正式分数。

完整方法见 [`../evals/portfolio-protocol-v3.md`](../evals/portfolio-protocol-v3.md)，当前证据状态见 [`../provenance/evaluation-status-v3.yaml`](../provenance/evaluation-status-v3.yaml)。

## 证据分级

| 证据 | 当前标签 | 可以支持的结论 | 不能支持的结论 |
|---|---|---|---|
| 首批 7 项、70 案例、210 份回答 | `legacy-v1 / internal model-judged` | 在固定模型、固定案例和固定日期下，相对无 Skill 与固定 Nature 上游的方向性内部对照结果 | 第三方认证、科学正确率、跨模型/跨学科普遍优势 |
| 扩展 12 项、120 个有效记录 | `post-remediation development regression` | 整改后在同一开发案例集上的回归状态 | held-out 泛化能力或相对其他项目的优势 |
| Director 20/20 | `deterministic routing assertions` | 20 个固定任务的路由、依赖、闸门等断言通过 | 科研答案质量、端到端成功率或编排器优越性 |
| 20 项 A/100 结构审计 | `structural conformance` | 当前脚本检查的目录、说明、工作流和引用路径一致 | 科学有效性、内容完整性或安全性达到 100% |
| 单元测试与规则扫描 | `engineering checks` | 已覆盖规则在当前快照中通过 | 形式化验证、零漏洞或真实环境零风险 |

只有首批 7 项把固定 Nature Skills 上游作为直接对照。K-Dense、Claude Scholar、PaperSpine 等仅有来源、能力或架构对照，没有同题 head-to-head 跑分。

## v0.4 首批三臂评测（legacy v1）

### 设计与计分

- 70 个仓库内编写案例，每个案例运行 `no_skill`、固定 Nature 上游和 ZJU 蒸馏版三个臂，共 210 份回答。
- 回答模型为 GPT-5.4-mini。三个臂被随机映射为 A/B/C 后，使用 GPT-5.5 进行两次评分调用；存在较大分歧或关键失败分歧时，再进行第三次 GPT-5.5 裁决。这里的“评分员”是模型调用，不是人类专家或独立机构。
- 五个维度按 0–4 评分：任务完整性 25%、证据可追溯性 25%、科学有效性 20%、安全与诚信 20%、可用性 10%。加权后换算为 0–100；关键安全或捏造失败会把该案例置零。
- 公开的 85.205、61.714 和 56.759 是该 rubric 下的内部加权分，不是“正确率百分比”。

详细 rubric 与聚合实现见 [`evals/rubric.md`](../evals/rubric.md) 和 [`evals/aggregate_blind_eval.py`](../evals/aggregate_blind_eval.py)。固定 Nature 提交见 [`evals/upstream-lock.json`](../evals/upstream-lock.json)。

### 已知限制

1. **Pilot 重用**：完整 70 例包含此前用于 pilot 的 7 例（每个首批 Skill 1 例）。Skill 已在观察这些 pilot 后发生迭代，因此它们不是 untouched held-out；v1 分数应视为开发期方向性证据。
2. **A/B/C 未均衡**：随机映射没有使用区组均衡。蒸馏臂位于 A/B/C 的次数为 32/22/16；无 Skill 为 21/26/23；上游为 17/22/31。若评分模型存在位置偏差，结果可能受影响。
3. **工具协议未强制执行**：配置声明 `web_search: false`，执行层却没有技术性禁止工具调用。事件日志记录到无 Skill 臂 3 次 Web 搜索，以及上游臂 17 次学术 MCP 调用。由此，工具条件和耗时/Token 比较并非严格受控；这些调用不应被解释为某一方向的确定性偏差。
4. **同源设计偏差**：案例、rubric 与 Skills 由同一项目开发，可能更贴合本项目的输出契约。
5. **模型评分局限**：两个 rater ID 对应同一 GPT-5.5 的不同调用，不等于两个独立评审主体；目前也没有公开 Cohen's kappa、ICC、置信区间或显著性检验。
6. **Gold checks 未直接计分**：首批案例的 gold checks 提供给评分模型作为参考，但没有作为确定性指标直接进入总分或发布门槛。
7. **原始记录尚未公开**：仓库公开案例、代码和聚合结果，但 `.gitignore` 排除了完整回答、事件和逐条评分记录。外部人员目前不能只依靠 GitHub 完整复算 v1 结果。

因此，v1 的“内部发布门槛通过”只描述当时脚本对当时数据的判定。它不再作为 Stable 的充分条件；首批 7 项的 Stable 晋级现已暂停，等待协议 v2 的冻结留出集验证。

## 扩展 12 项评测

公开的 84.385/100、84.58% gold-check 命中率和 0 个关键失败来自**整改后的开发集回归聚合**。最终 120 条有效记录中，49 条来自基础运行，71 条来自后续 remediation/amendment 运行；因此该数字不是首次运行成绩，也不是 untouched held-out 结果。

该评测只有 ZJU 蒸馏版一个臂，使用一次 GPT-5.5 评分流程并聚合 gold checks，没有 Nature 或其他仓库对照。它可用于确认整改后的已知案例没有回退，不应被用作跨项目性能声明。扩展 Skills 保持 Beta。

## Director 与工程检查

- `Director 20/20` 表示 20 个固定案例的确定性路由断言全部通过，包括所需/禁止 Skill、顺序、依赖、闸门与风险处理；不是 20 次模型科研任务都“答对”。
- `3/3` 是三个新上下文前向压力测试通过，不是跨项目盲测。
- `20 项 A/100` 是结构一致性分。四个维度及权重——描述 25%、组织 30%、风格 20%、结构 25%——参考了固定提交的 [Claude Scholar 评分框架](https://github.com/Galaxy-Dawn/claude-scholar/blob/2f7766fd541a723d4ddc6230b3277f948d61b093/skills/skill-quality-reviewer/references/scoring-criteria.md)，本项目再按 Codex validator、引用路径和输出契约重写具体检查项。它不是科研质量分。
- “46 个 Skill 脚本规则扫描零发现”只说明当前静态规则没有命中，不是科研能力或形式化验证证明。

## v0.5 协议 v2：验证核心已实现，留出集尚未运行

v2 的目标是形成可以支持 Stable 晋级的独立证据。当前代码已经实现：

- 对 prompt、fixture 与 gold criterion 生成不含案例 ID、gold-check ID 的语义指纹，同时用完整文件哈希保留逐字审计，拒绝通过改名复用已知案例；
- 用冻结 lock 和只允许一次的 first-attempt registry 绑定留出集、运行 ID 与案例指纹；
- 使用私密随机 secret/nonce 生成按 Skill 区组均衡的 A/B/C 分配，评分前只公开 commitment，评分封存后才 reveal；
- 强制 response 使用严格 UTF-8 且含可见内容，并要求非空 event、execution 和 rating 文件；核验它们与 Skill commit、rubric、rater schema、reveal schema 的 SHA-256 清单；
- 拒绝空事件、非对象 JSON、多个成功终止事件、成功后又失败/取消的终止序列，以及禁止的网络/工具事件；
- 使用独立于 legacy v1 的 `rubric-v2.md`，真正执行 rater 与 allocation-reveal schema；评分者 ID 在运行前进入协议哈希且不能成为路径，两个主评调用 ID 必须不同，裁决者不能与主评重合；
- 从真实冻结文件重新计算配对 bootstrap 区间、一致性、gold checks、关键失败和协议偏差；外部调用者不能注入一个自报的 `eligible` 审计结果；
- 分开报告 `holdout_ready`、`execution_ready` 与 `release_gate_passed`。状态命令会从冻结案例、lock、首次作答登记、原始运行包和固定评分者重新执行聚合，并要求结果与哈希固定的 aggregate 完全一致；只有自报汇总文件时返回非零。

以下工作仍**没有完成**：

- 由未参与 Skill 开发的人员编写并封存全新跨学科留出案例；
- 在隔离执行器中实际生成三臂回答并证明工具策略由宿主执行层强制；
- 由独立托管系统封存 first-attempt registry、评分者身份与调用凭据；仓库代码只能验证结构和绑定，不能证明自报身份真实独立或登记从未被回填；
- 完成跨模型和独立人类领域复核；
- 发布可复算的脱敏回答、事件、评分、裁决和环境证据包；
- 在任务确有重叠且许可允许时运行其他强基线。

因此，`python evals/protocol_v2.py --config evals/protocol-v2.json status` 当前会报告 `holdout_ready=false`、`execution_ready=false`、`release_gate_passed=false` 并退出 2。在 v2 冻结留出集完成并通过前，所有 20 个 Skills 均保持 Beta；v0.4 数字只保留为带完整限制说明的历史内部快照。
