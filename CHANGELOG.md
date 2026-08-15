# Changelog

## 1.1.0 - 2026-08-15

- 文献链加入 Crossref、OpenAlex、Europe PMC 和 PubMed 执行器，以及合法开放全文 JATS 获取和真实 PDF 页面锚点解析。
- 统计与交付链加入 EDA、单因素方差分析、单预测量二项 Logistic GLM、结果绑定制图、DOCX 和 PPTX 生成。
- 20 个核心 Skills 完成 400/400 条 L2 确定性功能测试；L3/L4 外部盲测仍待独立人员执行，正式能力分继续为空。
- 加入 Codex、Claude Code、OpenCode 的一键安装、更新、诊断与卸载，并提供单 Skill 的 Fast Mode。
- 新增可选的组学、材料计算和药物发现领域包，不改变核心 20 项的默认行为。
- 重写 README，使项目说明更接近日常开源文档；宣传图不再写入容易过期的测试数字。

## 1.0.0 - 2026-08-15

- 发布首个稳定发行包，冻结当前 20-Skill 目录、公共交接契约和三种 Agent 的项目级入口。
- 加入机器可读的发行资格门，明确区分 `distribution_status: stable` 与 `capability_evaluation_status: beta`。
- 将完整离线测试、脚本静态扫描和发行资格检查接入分支 CI 与标签发布流水线。
- 保留 protocol v3 的 fail-closed 能力门：没有独立冻结盲测时，正式能力分继续为 `null`。

早期版本的详细迭代记录见 [README](README.md) 和 [`docs/iteration-review-v0.5.md`](docs/iteration-review-v0.5.md)。
