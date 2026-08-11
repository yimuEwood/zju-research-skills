# ZJU Research OS

一套面向浙江大学科研场景的中文科研 Skills。项目由 1 个 Research Director 和 19 个专业 Skills 组成，覆盖文献检索、论文阅读、证据综合、实验记录、统计分析、写作、制图、答审、数据共享和科研转化。

![ZJU Research OS 项目概览](docs/assets/hero.png)

项目当前版本是 **v0.5.0-beta.1**，20 个 Skills 都处于 Beta 阶段。这是社区开源项目，与浙江大学官方无关。

## 为什么做这个项目

做这个项目的起点很简单：单独完成一次检索、润色一段文字，现有科研 Skills 往往已经够用；真正麻烦的是把一项研究连续做下去。

文献检索得到的来源，到了写作时可能已经找不到对应关系；实验设计里的实验单位和重复方式，进入统计阶段后又要重新解释；同一个结果分别写进正文、图注和答审信，很容易出现数字或措辞不一致。任务一长，聊天记录也很难承担完整的项目状态。

因此，这个项目没有把所有功能写进一份很长的提示词，而是采用 Director + Specialists 的结构：

- Research Director 负责拆解任务、安排依赖关系，并记录项目进度；
- 19 个专业 Skill 分别处理检索、全文获取、引用核验、精读、实验、统计、写作等具体工作；
- 文献、证据、主张、分析结果和产物都有稳定 ID，方便在不同阶段继续引用；
- 研究可以暂停和恢复，失败实验、矛盾证据与未解决问题不会因为切换 Skill 而被丢掉；
- 涉及账号、伦理、专利、外部上传或投稿时，仍然交给有权限的人确认。

![Director 与 19 个专业 Skills 的协作架构](docs/assets/architecture.png)

## v0.5 主要改了什么

如果把这次更新概括成一句话，就是把原来分散的功能真正接了起来。

1. 文献检索不再停在一组关键词上。现在会从概念、机制、方法、研究对象和限制条件五个角度拆分问题，针对化学、材料、生医和农业提供不同模板，还可以沿种子论文继续追踪参考文献与后续引用，并根据新增文献比例和覆盖缺口判断是否需要继续搜索。
2. 阅读、证据综合和假设设计共用一条证据链。Paper Spine 记录论文的问题、主张、方法、证据、论证和适用边界；冲突矩阵单独保留方向相反、空结果、异质性和偏倚；候选假设则按照可证伪性、信息增益和实验区分度排序。
3. 实验记录可以直接进入统计分析。Experiment Log 2.0 会保存实验单位、观察单位、重复、随机化、层级结构、结局指标和数据谱系，Statistics Skill 再据此建立 Analysis Contract，而不是到分析阶段重新猜测实验设计。
4. 正文、图件、答审和数据说明使用同一份 Result Registry。每个结果都有 `analysis_id` 和 `result_id`，数字只登记一次，再由不同产物引用，减少手工复制带来的不一致。
5. 基金和专利不再从空白模板开始。基金 Skill 可以把证据、竞争假设和判别实验整理成目标、工作包、依赖顺序和三分支里程碑；专利 Skill 可以从论文和实验记录中整理问题—方案—效果、特征—证据、权利要求依赖和现有技术查询图谱。
6. 化学数据比较增加了条件约束。只有化合物身份、终点、单位、测量基准、实验条件、方法和证据层级能够对齐时，记录才会放在一起比较。

这次也专门检查了各个 Skill 之间的接口。文献 `record_id` 现在不会因为输入顺序变化而重新编号；Evidence 中的支持和反对关系会与逐行记录双向核对；Hypothesis 可以无损交给 Proposal 和 Experiment Log；Result Registry 也会反查 Analysis Contract。仓库里新增了一条从文献发现一直走到论文产物的集成测试。

详细改动见 [`docs/iteration-review-v0.5.md`](docs/iteration-review-v0.5.md)，评测方法见 [`docs/evaluation-methodology.md`](docs/evaluation-methodology.md)。

下面这张图展示了主要的数据交接关系：

```mermaid
flowchart LR
  Q[研究问题] --> S[Search Map]
  S --> F[Source Package]
  F --> P[Paper Spine]
  P --> C[Conflict Matrix]
  C --> H[竞争假设与判别实验]
  H --> E[Experiment Log]
  E --> A[Analysis Contract]
  A --> R[Result Registry]
  R --> W[论文与图件]
  R --> V[评审与答审]
  R --> D[数据与复现包]
  C --> G[基金工作包]
  E --> I[专利披露图谱]
  H --> M[持续监测]
  M --> C
```

## 包含哪些 Skills

| 范围 | Skills | 状态 | 当前测试依据 |
|---|---|---:|---|
| 统一入口 | [`zju-research-director`](skills/zju-research-director/) | Beta | 20/20 确定性路由断言；3/3 新上下文前向测试 |
| 文献入口 | [`literature-search`](skills/zju-literature-search/)、[`fulltext-access`](skills/zju-fulltext-access/)、[`reference-audit`](skills/zju-reference-audit/)、[`paper-reader`](skills/zju-paper-reader/) | Beta（legacy-v1） | v0.4 内部模型评测；Stable 晋级暂停 |
| 实验与论文 | [`experiment-log`](skills/zju-experiment-log/)、[`statistics-audit`](skills/zju-statistics-audit/)、[`scientific-writing`](skills/zju-scientific-writing/) | Beta（legacy-v1） | 同一内部评测；等待 v2 冻结留出集 |
| 证据与研究设计 | [`literature-monitor`](skills/zju-literature-monitor/)、[`evidence-synthesis`](skills/zju-evidence-synthesis/)、[`hypothesis-design`](skills/zju-hypothesis-design/) | Beta | 每项 10 个任务级金标准案例 |
| 交流与同行评审 | [`scientific-figure`](skills/zju-scientific-figure/)、[`paper2ppt`](skills/zju-paper2ppt/)、[`reviewer`](skills/zju-reviewer/)、[`review-response`](skills/zju-review-response/) | Beta | 每项 10 个任务级金标准案例 |
| 共享、立项与转化 | [`data-availability`](skills/zju-data-availability/)、[`proposal-writer`](skills/zju-proposal-writer/)、[`paper-to-patent`](skills/zju-paper-to-patent/) | Beta | 每项 10 个任务级金标准案例 |
| 专业数据库与科研规范 | [`chemistry-databases`](skills/zju-chemistry-databases/)、[`research-integrity`](skills/zju-research-integrity/) | Beta | 每项 10 个任务级金标准案例 |

## 测试结果，以及该怎么理解

仓库保留了 v0.4 的内部测试结果，方便后续版本对照。不过，这些数字不是第三方测评，也不能理解成科研结论的正确率。它们来自固定模型、固定案例和固定日期，应当和下面列出的限制一起看。

### 首批 7 个 Skills

2026 年 8 月 11 日，我们在固定模型设置下运行了 70 个仓库内案例。每个案例分别测试“无 Skill”“固定版本的 Nature Skills 上游 Skill”和“ZJU 版本”，共得到 210 份回答。评分时隐藏了 A/B/C 标签，由 GPT-5.5 进行两次评分；两次结果分歧时，再进行第三次裁决。这是模型评分，不是独立人类专家评分。

| 评测臂 | 平均质量分（0–100） | 单任务中位耗时 | 单任务 Token 中位数 | 关键失败 |
|---|---:|---:|---:|---:|
| 无 Skill | 56.759 | 31.377 秒 | 34,091 | 3/70 |
| 固定 Nature 上游 | 61.714 | 61.924 秒 | 96,989 | 4/70 |
| ZJU 版本 | **85.205** | **57.224 秒** | **72,893.5** | **0/70** |

按照当时使用的 legacy-v1 rubric，ZJU 版本比固定 Nature 上游平均高 **23.491 分**，中位耗时低 **7.59%**，Token 中位数低 **24.84%**；比无 Skill 平均高 **28.446 分**。这些数字是内部加权分和运行观察值，不是准确率，也不能当作因果结论。尤其是当时的工具协议没有由执行器严格强制，效率数据只能作为参考。

各项结果如下：

| Skill | 相对“无 Skill / 上游 Skill 中较高者”的内部分差 | 当时的通过方式 |
|---|---:|---|
| Literature Search | +26.063 | 质量 |
| Full-text Access | +22.187 | 质量 |
| Reference Audit | +30.124 | 质量；耗时下降 16.809%，Token 下降 41.991% |
| Paper Reader | +9.938 | 效率；耗时下降 26.958%，Token 下降 33.950% |
| Experiment Log | +24.437 | 质量 |
| Statistics Audit | +10.687 | 质量 |
| Scientific Writing | +28.125 | 质量 |

这组结果有四个已知问题：70 个案例中有 7 个参加过前期 pilot；A/B/C 的位置没有做区组均衡；日志中出现了测试配置之外的 3 次 Web 搜索和 17 次学术 MCP 调用；仓库目前只公开了聚合结果，没有完整回答、事件日志和逐条评分记录。

基于这些问题，首批 7 个 Skills 已重新标记为 Beta（legacy-v1），不再仅凭这组分数进入 Stable。下一次性能比较会使用新的 protocol v2 和全新冻结留出集。目前尚未运行 v2，因此没有 v0.5 的新跑分。

### 扩展 Skills、Director 和本地检查

| 范围 | 案例 | 结果 | 应该如何理解 |
|---|---:|---|---|
| 12 个扩展 Skills | 120 | 整改后开发集回归：平均 84.385/100；gold-check 命中率 84.58%；严重失败 0 | 最终聚合含 49 条基础运行和 71 条整改/修订记录；不是 held-out，也没有外部项目对照 |
| Director 路由 | 20 | 5 个领域，覆盖 19 个专业 Skills，20/20 路由断言通过 | 只测试路由和阶段安排，不是科研答案质量分 |
| Director 新上下文测试 | 3 | 3/3 断言通过 | 前向压力测试，不是跨项目 head-to-head |
| 本地工程检查 | — | 20/20 格式校验；20 项结构一致性审计为 A/100；103 个单元测试通过，其中包含 1 条发现→论文的跨 Skill 集成链；39 个离线处理/验证脚本通过当前静态规则检查 | 结构审计使用的 25/30/20/25 权重参考 Claude Scholar；不评价科学正确性 |

聚合结果保存在 [`provenance/release-status.yaml`](provenance/release-status.yaml)、[`evals/results/full-20260811-a/aggregate-adjudicated.json`](evals/results/full-20260811-a/aggregate-adjudicated.json)、[`evals/results/expansion-full-20260811-a/aggregate-expansion-full-judge-1.json`](evals/results/expansion-full-20260811-a/aggregate-expansion-full-judge-1.json) 和 [`evals/results/director-forward-20260811-a.json`](evals/results/director-forward-20260811-a.json)。由于 v1 的完整原始记录还没有公开，仅凭当前仓库不能完整复算当时的所有评分。

回答生成模型为 GPT-5.4-mini，评分模型为 GPT-5.5。所有数字都是 2026-08-11 的仓库内快照，不代表其他模型、学科或实际课题一定会得到相同结果。

## 这个项目参考了哪些开源工作

ZJU Research OS 不是从零开始的。中文科研工作流的主骨架来自 Nature Skills，同时参考了 K-Dense、PaperSpine、Claude Scholar 等项目中做得比较好的部分。下表记录的是来源和改写方向，不是排行榜。除了首批 7 项与固定 Nature Skills 上游做过 legacy-v1 内部对照，其余项目都没有进行同题 head-to-head 测试。

![与其他科研 Skills 的能力取舍](docs/assets/comparison.png)

| 参考项目 | 主要借鉴 | 在本项目中的改写 |
|---|---|---|
| [Nature Skills](https://github.com/Yuan1z0825/nature-skills) | 中文科研流程、写作、全文获取、图表和答审 | 增加五视角检索、引文追踪、搜索停止判断、全文组件包、Paper Spine、冲突矩阵和稳定 ID 交接；首批 7 项有固定 commit 的 legacy-v1 内部对照 |
| [K-Dense Scientific Agent Skills](https://github.com/K-Dense-AI/scientific-agent-skills) | 数据库覆盖、统计、引用管理和确定性脚本 | 改写成四领域检索模板、设计优先的分析契约、结果注册表和跨产物一致性检查；没有 head-to-head 测试 |
| [Auto-claude-code-research-in-sleep](https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep) | 长流程执行、实验交接、来源和评审节点 | 使用可暂停的 Research Mission、预算和停止条件，并保留外部操作的人工确认；属于架构对照 |
| [Claude Scholar](https://github.com/Galaxy-Dawn/claude-scholar) | Skill 质量评分、渐进披露和整改清单 | 结构检查的四个维度与 25/30/20/25 权重参考其 scorecard，再按 Codex validator、引用路径和输出契约改写；没有模型对照 |
| [PaperSpine](https://github.com/WUBING2023/PaperSpine) | 贡献契约、结果验证、审稿异议和阶段恢复 | 保留 argument spine 的思路，并扩展到跨论文冲突、竞争假设、判别实验、结果注册表和科研转化 |
| [Agentic Awesome Skills](https://github.com/sickn33/agentic-awesome-skills) 等 | 能力目录、选择清单、流水线和导师式审查 | 只采用与科研闭环相关、许可证兼容的部分；`academic-research-skills` 和 `Supervisor-Skills` 因许可边界只用于比较，没有改编其内容 |

具体 URL、固定 commit、许可证和采用范围记录在 [`provenance/sources.yaml`](provenance/sources.yaml) 与 [`NOTICE`](NOTICE) 中。

## 针对浙大场景做了哪些适配

- 检索和证据模板覆盖化学、材料、生医、农业以及通用/计算研究，中文问题可以直接进入工作流；
- 支持中文或中英双语输出，检索图、精读卡、实验设计、统计结果、正文、图注和答审可以沿用同一组 ID；
- 化学与材料场景会提示 SciFinder、Reaxys 等候选资源，并在比较数据时同时检查终点、单位、测量基准、条件、方法和证据层级；
- 校外访问按照浙江大学图书馆当前页面，在 WebVPN、CARSI、RVPN 和人工馆员路径之间给出建议；候选数据库包括 CNKI、Web of Science、Scopus、SciFinder 和 Reaxys，实际可用性仍以图书馆页面为准；
- 访问和科研规范引用校内权威页面，并记录核验日期。工作流不会保存统一身份认证密码、Cookie 或 Token，也不会绕过付费墙或进行违规批量下载；
- 可以与现有 `nature-*` Skills 并行使用，不会覆盖原有安装。

相关入口：[浙江大学图书馆校外访问](https://libweb.zju.edu.cn/56334/list.htm) · [电子资源使用管理办法](https://libweb.zju.edu.cn/2016/1014/c55987a2245977/page.htm) · [学术道德行为规范及管理办法](https://pi.zju.edu.cn/2019/0920/c66998a2550400/page.htm)

## 怎么使用

将仓库根目录作为本地 Codex 插件加载即可。插件入口是 [`.codex-plugin/plugin.json`](.codex-plugin/plugin.json)，Skills 位于 [`skills/`](skills/)。具体安装方式可能随 Codex 或 Agent Skills 客户端版本变化，请以所用客户端的当前说明为准。

需要处理跨阶段任务时，可以从 Director 开始：

```text
请使用 zju-research-director，把“钙钛矿器件湿热稳定性”规划成一项 Research Mission。
自治上限 L2；先完成可复现检索、证据综合和可证伪假设，只执行下一个就绪阶段，不做外部提交。
```

如果只是做一件具体的事，直接调用对应 Skill 会更简单：

```text
请使用 zju-reference-audit，逐字段核验这 30 条参考文献，并分开判断“书目信息正确”和“是否支持正文主张”。
```

已有项目也可以从保存的任务状态继续：

```text
请使用 zju-research-director 验证现有 research-mission.json，保留所有 open_loops 和失败实验，从第一个 ready 步骤继续。
```

## 项目状态与许可证

- 项目代码和原创工作流按 [Apache-2.0](LICENSE) 发布，第三方归属见 [`NOTICE`](NOTICE)；
- 非商业或 Share-Alike 来源只用于能力比较，没有把其文字、模板或工作流表达并入当前发行包；
- GitHub Star 只用于发现候选项目，不作为科学正确性或可靠性的证据；
- 20 个 Skills 目前全部是 Beta。首批 7 项保留 legacy-v1 内部记录，但 Stable 晋级已经暂停；扩展 Skills 与 Director 也需要通过新的冻结留出集、统一工具条件和独立复核后，才会考虑升级状态。

如果你愿意参与测试，欢迎提交可复现的案例、失败样本或改进建议。相比只报告成功案例，这些材料对项目下一轮迭代更有价值。
