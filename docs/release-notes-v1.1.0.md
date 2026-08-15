# ZJU Research OS v1.1.0

v1.1.0 主要做了一件事：把更多工作流变成真正可以运行、可以复查的工具，而不只是告诉 Agent 应该怎么做。

## 这次新增了什么

- 文献检索可以调用 Crossref、OpenAlex、Europe PMC 和 PubMed。正式测试使用录制的公开响应，联网需要显式开启。
- 全文链可以获取合法开放的 Europe PMC JATS；PDF 阅读器会保存页码、版面块、章节、图表和公式线索。
- 统计执行器新增数据概览、单因素方差分析和单预测量二项 Logistic GLM；不支持的设计会明确拒绝。
- 图件执行器可以从源数据与 `result_id` 生成 PNG、SVG 和 PDF；文档执行器可以生成并重新打开检查 DOCX、PPTX。
- 新增一键安装、更新、诊断和卸载，支持 Codex、Claude Code、OpenCode；Fast Mode 可把单一任务直接交给一个专业 Skill。
- 新增三个可选领域包：组学、材料计算和药物发现。它们按需安装，不改变核心 20 项。

## 测试结果

- L1：20 个 Skills，120/120 条基础契约检查通过。
- L2：20 个 Skills，400/400 条确定性功能测试通过，覆盖 100 个核心能力。
- 离线回归：296 项测试在本地带可选缓存时全部通过；干净检出必须通过 295 项，另有 1 项缓存完整性检查可明确跳过。
- 静态检查：核心与领域包共 68 个 Python 脚本，当前规则下 0 项发现。
- CI：Ubuntu / Windows × Python 3.11 / 3.13。

这些结果证明的是固定输入下的软件行为和接口一致性，不是科研结论准确率。L3 已知任务对照和 L4 外部未见任务盲测仍需独立人员完成，因此 `official_portfolio_score` 继续为 `null`，能力状态仍为 Beta。

参与外部评测请阅读 [`docs/evaluation/external-blind-evaluation-v3.md`](evaluation/external-blind-evaluation-v3.md)。

## 安装

Windows：

```powershell
.\install.ps1 -Agent all -WithRuntime
```

macOS / Linux：

```bash
sh install.sh --agent all --with-runtime
```

更新：

```powershell
.\update.ps1 -Agent all -Pull -WithRuntime
```

详细使用方法见项目首页。
