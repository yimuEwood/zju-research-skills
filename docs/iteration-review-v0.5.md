# v0.5 自我审查与迭代说明

本轮先按真实科研任务重走“发现—理解—设计—执行—分析—表达—转化”全链路，再修复会阻断这些能力交接的工程问题。目标不是继续堆 Skill 数量，而是让已有 20 个 Skills 在同一项研究里共享证据、设计变量、分析结果和下一步决策。

## 科研能力升级

| 科研闭环 | 旧版主要断点 | 本轮升级 | 可交接产物 |
|---|---|---|---|
| 检索 → 全文 → 精读 | 检索式、补充材料与精读结构彼此松散；难判断何时搜够 | 五视角问题拆解、四领域模板、引文网络扩展、搜索饱和度；全文来源包同步记录版本、补充材料、协议、数据与代码；Paper Spine 深读 | `search-map`、`source-package`、`paper-spine` |
| 精读 → 证据综合 → 假设 | 文献总结容易把冲突和边界压平，假设排序依赖主观直觉 | 冲突矩阵显式区分方向、空结果、异质性、偏倚和直接性；候选假设按可证伪性、信息增益及实验区分度排序 | `conflict-matrix`、竞争假设集、判别实验排序 |
| 监测 → 动态研究议程 | 新论文只形成信息流，不能稳定更新研究动作 | 监测目标分为 claim、gap、hypothesis、seed；区分新增、版本更新和重复，并生成下游待办队列 | 证据关联的 monitor state 与 action queue |
| 实验 → 统计 | 实验记录与分析方案之间缺少实验单位、重复和层级约束 | 日志携带实验/观察单位、技术与生物学重复、随机化、层级、重复测量、结局 ID 和数据谱系；统计按领域决策图生成分析契约 | `design-analysis handoff`、`analysis-contract` |
| 统计 → 写作/图件/数据 | 同一结果在正文、图注、回复和数据说明中容易被重复手填 | 规范化结果注册表作为单一事实源；写作、图件、答审和数据开放使用 `analysis_id` / `result_id`，并支持确定性结果 token 渲染与跨产物核对 | `result-registry`、result tokens、reconciliation report |
| 审稿 → 修改 → 回复 | “已修改”与真实文本差异、证据和解决测试脱节 | concern → action → evidence/result → exact manuscript diff → resolution test 的闭环追踪 | 可复核 response tracker 与修改证据链 |
| 证据 → 基金/专利 | 申请书和披露书常从模板重新叙述，丢失竞争假设和证据边界 | 基金侧编译为目标、工作包、拓扑顺序、三分支里程碑和能力矩阵；专利侧编译为问题—方案—效果、特征—证据、权利要求依赖和现有技术查询映射 | proposal execution handoff、research-to-invention map |
| 化学数据库 → 可比较证据 | 同一化合物的数值可能因终点、单位、基准、条件和方法不同而不可比 | 条件感知分组、单位/基准对齐、方法与证据层级分层，显式隔离不可直接比较的记录 | condition-aware evidence matrix |

这些改动强化的是研究过程中的推理与交接能力；它们不自动证明生成的科学结论正确。对应脚本负责确定性解析、映射和一致性检查，领域判断仍由来源证据与研究者负责。

### 第二遍接口审查发现并修复的断链

- 文献 `record_id` 改为保留合法既有 ID，并为新记录生成与输入顺序无关的身份指纹；插入或重排记录不会让整条下游证据链重新编号。
- Evidence rows 强制携带 `record_id`；claim 的 supporting / contradicting / contextual 列表必须与每行 `evidence_role` 双向一致。Hypothesis v2 同时拒绝 evidence map 之外的证据 ID。
- Hypothesis → Proposal adapter 保留机制、预测、干预、对照、实验单位、主结局、各假设预期模式和不可判定区域；Experiment Log 的主生成器现在真实输出 schema 2.0 设计与分析交接，而不只是参考文档写了 2.0。
- Result Registry 必须反查 Analysis Contract 的 study、analysis 和 outcome 归属；有界 P 值与显示舍入使用统一机器表示，token renderer 的输出可以直接回灌一致性核对。
- 新的 result-handoff validator 同时检查定量 claim、Results section、figure panel、review concern 和 data/reproducibility package；Paper-to-Patent 也能直接读取嵌套 canonical result registry 与 artifact inventory。
- 新增一条集成回归，从稳定文献 ID、冲突证据、竞争假设、基金工作包、实验日志、分析契约一路走到结果注册表及论文/图件/答审/数据消费者，并验证悬空 ID 会阻断交接。

## 审查结论

| 发现 | 旧风险 | 本轮处理 | 当前边界 |
|---|---|---|---|
| Director 只看产物类型或状态 | 计划、假路径或自报哈希可能被误当成真实交付物 | 引入共享 Artifact Envelope，核验生产 Skill、路由步骤、本地文件、SHA-256、格式签名和绑定验证回执 | 静态与确定性检查不能代替科学语义、视觉质量或专家判断 |
| 步骤输出是宽松列表 | `deck_plan` 等中间件可能替代用户要求的 PPTX | 使用 `required_output_groups`；PPTX、图件和投稿包分别要求真实交付文件与 QA/manifest 组 | 仍需在真实 Office/PDF 环境中增加端到端渲染回归 |
| 发布闸门只覆盖部分上游状态 | 有 manuscript 时仍可能遗漏数据、统计、引用、复现性或诚信检查 | 投稿发布重新计算适用的 evidence/data/analysis/reference/claim/artifact/integrity/reproducibility 闸门 | 伦理、隐私、专利和投稿决定仍必须由有权人员完成 |
| 发布授权是宽泛布尔值 | 旧授权可能被复用，代理也可能在 mission 内伪造“已批准” | mission decision 必须引用由调用者从独立输入通道提供的回执；回执绑定 mission、artifact IDs、动作、允许角色、状态哈希和有效期 | 这是明确的本地信任边界，不是机构数字签名系统 |
| 专业脚本接受“形似正确”的记录 | 无标识文献会误合并，版本更新无法识别，专利数字与图件哈希可能只做格式检查 | 修复无标识记录保留、文献版本迁移、专利来源锚点与数字不变量、图件和科研产物真实文件哈希检查 | 数据库真实性和科学解释仍需来源或领域专家复核 |
| v1 跑分被过度解读 | Pilot 重用、位置不均衡、工具协议漂移和 raw records 缺失可能被隐藏在聚合分后 | 全部 Skills 回调 Beta；公开 legacy-v1 限制；新增严格的 protocol v2 核心，不产生或替换任何新分数 | 新冻结留出集尚未执行，因此没有 v2 性能结论 |

## Protocol v2 的发布原则

协议 v2 把“评测脚本能聚合一个数字”和“证据足以支持发布”分开。只有下列条件同时满足，才允许生成新的发布判定：

1. 全新留出案例在首次运行前冻结，案例内容指纹不与已知开发案例重合；
2. 盲法分配在评分前不可由公开信息解码，评分后再验证 commitment 与 reveal；
3. 每个回答、事件日志、评分、rubric、Skill commit 和运行清单都有哈希绑定；
4. 工具事件非空、结构合法，并由执行层保证同一工具政策；
5. 两名主评的调用身份不同，裁决者与主评不同，评分字段和理由通过 schema；
6. 从冻结文件重新计算配对置信区间、一致性、gold checks、关键失败和协议偏差；
7. 完整的脱敏证据包可以由第三方复算。

当前仓库只提供协议实现与回归测试，不提供 v2 跑分。`holdout_ready`、`execution_ready` 和最终 `release_gate_passed` 必须分别报告，不能互相替代。

## 下一轮验证优先级

- 用化学、材料、生医、农业真实课题测量检索召回、误收、引文扩展边际收益和饱和停止质量；
- 测量全文组件包完整率、Paper Spine 的 claim-anchor 完整率，以及冲突矩阵与领域专家判断的一致性；
- 检验假设排序能否提出真正区分竞争机制的预测、实验与不可判定区域，而不只是生成更多候选想法；
- 用真实实验数据测量 design-analysis handoff 的字段完整率，以及正文、图注、答审、数据说明间的数值一致性；
- 让基金工作包和专利披露图谱接受课题负责人逐项评审，记录可直接采用、需修改和不可采用的比例；
- 同时邀请未参与 Skill 编写的老师或研究生封存跨学科留出案例，在统一执行器中完成 v2 三臂评测并发布脱敏证据包。

这份说明记录的是工程审查结果，不构成浙江大学官方背书，也不把通过单元测试等同于科学正确。
