# 发布流程

`0.2.0b1` 为单机研究和试用的公开测试版。

源代码采用 MIT，数据来源声明见 [NOTICE](../NOTICE.md)。Landsat 与 Resolver 保持独立分工。
公开仓库为 https://github.com/ottoKae/LandSat_GRiTS 。推送 main 或版本标签会触发四组 CI。
首次 `v0.2.0b1` 标签的四组检查全部通过后，工作流使用同次 CI 构建物自动发布 GitHub 预发行版。
工作流不自动推送代码、创建标签或上传 PyPI。

## 本地检查

```sh
uv sync --locked --extra dev
uv run --locked ruff check src tests examples tooling/release tooling/validation
uv run --locked python -c "from pathlib import Path; Path('.test-tmp').mkdir(exist_ok=True)"
uv run --locked pytest -p no:cacheprovider --basetemp .test-tmp/release-run
uv build --out-dir .test-tmp/release-dist
uv run python tooling/release/check_artifacts.py .test-tmp/release-dist
```

每次构建使用只包含当前版本的新目录。wheel 包含 Python 包、GUI 资源和许可证；
源码包额外包含测试、示例、说明、发布检查脚本和 `tooling/validation/` 校验工具。
原始影像、试点目录、运行日志和大型验收材料不进入发布包。

将 wheel 安装到新的虚拟环境后，用该环境的 Python 执行：

```sh
python -I tooling/release/smoke_installed.py --output .test-tmp/installed-demo
```

它核对模块确实来自已安装包，运行离线两景归档，再检查本地 HTTP GUI 的资源与目录接口。
GitHub Actions 覆盖 Windows／Linux 和 Python 3.12／3.13。
标签提交必须通过四组检查后才能发布；附件包含 wheel、源码包和 SHA256SUMS。

## 文件范围

源码、测试、示例、CI 配置和使用文档纳入版本控制。影像、输出、缓存、运行日志和
历史验收材料保留在本地；日志按 `logs/日期/任务名/` 组织，由 `.gitignore` 排除。
官方格网由 `landsat-grits grid` 显式下载并校验 SHA-256，不随软件包分发。
