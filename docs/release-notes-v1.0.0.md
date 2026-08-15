# ZJU Research OS v1.0.0

这是项目的第一个正式发行版。v1.0.0 稳定的是发行包、公开工作流接口和已经声明支持的确定性执行链，不是对科研答案正确率的笼统承诺。

## 这一版可以做什么

- 用 1 个 Research Director 调度 19 个专业 Skills，并通过稳定 ID 连接检索、全文、精读、证据、实验、统计、制图、写作、评审和交付。
- 直接导入 JSON/JSONL、CSV/TSV、RIS、BibTeX 与 NBIB 文献记录。
- 离线解析 PDF/文本，生成逐页锚点、章节、图表/公式线索和来源哈希。
- 在显式分析契约下执行有限但可核验的统计核心，并把结果写入 Result Registry。
- 从已核验结果生成 PNG、SVG 与 PDF 图件，并保留数据、结果 ID 和文件哈希。
- 通过 Codex、Claude Code 与 OpenCode 的项目级入口发现同一套 20 个 Skills。

## 正式版测试门

- 20/20 Skills 纳入同一份能力矩阵，每项固定 5 个核心能力。
- L1 工程契约普查 120/120 通过，输入清单绑定 153 个文件。
- 共发现 **192** 项离线单元、可执行产物与发行一致性测试。本地带可选上游缓存时 192 项全部通过；干净 GitHub runner 必跑 191 项，并明确跳过 1 项需要未追踪上游缓存的可选完整性检查。
- 46 个 Skill 脚本通过静态规则扫描，0 个发现。
- GitHub Actions 在 Ubuntu/Windows 与 Python 3.11/3.13 的四种组合上运行完整测试。
- 标签流水线会在创建 GitHub Release 前再次运行离线测试、静态扫描和发行资格检查。

## 仍然没有宣称什么

protocol v3 的科研能力正式分仍为 `null`，20 项能力证据状态仍为 Beta。L2 确定性案例、L3 三臂受控任务和 L4 独立管理的冻结留出任务还没有全部完成；因此 L1、单元测试和历史内部模型评分都不会被包装成“科研正确率”。后续能力晋级仍需完整的 1,060 个检查/案例单位、至少 2,140 条臂级记录，以及预登记的独立验证材料。

完整边界见 [distribution-release-v1.json](https://github.com/yimuEwood/zju-research-skills/blob/v1.0.0/provenance/distribution-release-v1.json) 和 [portfolio-protocol-v3.md](https://github.com/yimuEwood/zju-research-skills/blob/v1.0.0/evals/portfolio-protocol-v3.md)。
