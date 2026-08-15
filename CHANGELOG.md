# Changelog

## 1.0.0 - 2026-08-15

- 发布首个稳定发行包，冻结当前 20-Skill 目录、公共交接契约和三种 Agent 的项目级入口。
- 加入机器可读的发行资格门，明确区分 `distribution_status: stable` 与 `capability_evaluation_status: beta`。
- 将完整离线测试、脚本静态扫描和发行资格检查接入分支 CI 与标签发布流水线。
- 保留 protocol v3 的 fail-closed 能力门：没有独立冻结盲测时，正式能力分继续为 `null`。

早期版本的详细迭代记录见 [README](README.md) 和 [`docs/iteration-review-v0.5.md`](docs/iteration-review-v0.5.md)。
