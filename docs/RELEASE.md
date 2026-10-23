# 首次发布准备

目标版本为 `0.2.0b1`，定位为单机研究和试用的首次公开测试版。
本地历史报告中的 `0.2.0` 是开发版本标记，不表示已有公开版本。

源代码采用 MIT，数据来源声明见 [NOTICE](../NOTICE.md)。Landsat 与 Resolver 保持独立分工。
公开仓库为 https://github.com/ottoKae/LandSat_GRiTS 。推送 main 或版本标签会触发四组 CI。
首次 `v0.2.0b1` 标签的四组检查全部通过后，工作流使用同次 CI 构建物自动发布 GitHub 预发行版。
工作流不自动推送代码、创建标签或上传 PyPI。

## 本地检查

```sh
uv sync --locked --extra dev
uv run --locked ruff check src tests examples tooling/release
uv run --locked pytest -p no:cacheprovider --basetemp .test-tmp/release-run
uv build --out-dir .test-tmp/release-dist
uv run python tooling/release/check_artifacts.py .test-tmp/release-dist
```

每次构建使用只包含当前版本的新目录。wheel 包含 Python 包、GUI 资源和许可证；
源码包额外包含测试、示例、说明和测试实际依赖的诊断 Python 脚本。
原始影像、试点目录、运行日志和大型验收材料不进入发布包。

将 wheel 安装到新的虚拟环境后，用该环境的 Python 执行：

```sh
python -I tooling/release/smoke_installed.py --output .test-tmp/installed-demo
```

它核对模块确实来自已安装包，运行离线两景归档，再检查本地 HTTP GUI 的资源与目录接口。
GitHub Actions 配置覆盖 Windows／Linux 和 Python 3.12／3.13；远程矩阵结果须在推送后确认，
不能把已写入 CI 文件当作所有平台测试已通过。

## 发布边界

- 官方格网 ZIP 由用户显式下载，校验 SHA-256；不携带 S1-GRiTS 的格网文件。
- 真实影像测试是历史验收证据，不是安装时必须下载的测试数据。
- `docs/evidence/` 与历史本机 GUI 记录可在源码工作区查看，随软件包分发的文档不保证包含大型附件。
- 暂未执行现代 L1 付费访问验收、全球全历史负载验证或跨平台真实影像重跑。
- GitHub Release 必须关联通过矩阵检查的标签提交；附件包含 wheel、源码包和 SHA256SUMS。
