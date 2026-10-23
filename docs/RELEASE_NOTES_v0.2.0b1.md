首次公开测试版，面向单机 Landsat Collection 2 归档与分析。

- 支持 MSS、TM、ETM+、OLI/TIRS，按传感器与产品级别分组。
- 固定 MGRS extent，逐景保存为 `(time,bands,x,y)` Zarr，保留 QA、轨道和来源。
- 提供官方格网准备、公开 MSS L1／现代 L2 读取、现代 L1 本地导入、续传和单景 GUI。
- 核心独立安装；日／月／年合成由另行安装的 GRiTS-Resolver 提供。
- MIT 开源，附带无需联网的两景人工数据示例。

本 Release 只有在 Windows／Linux × Python 3.12／3.13 四组 CI 全部通过后发布。
附件 wheel 和源码包来自该标签的 CI，`SHA256SUMS` 可核验下载文件。

安装 wheel：`python -m pip install /path/to/landsat_grits-0.2.0b1-py3-none-any.whl`。
运行示例请下载并解压源码包，按 README 操作；不需要现有 GRiTS 目录布局。

这是测试版，不承诺全球档案完整、完全无云或分布式生产能力。
现代 L1 AWS Requester Pays 真实付费访问尚未验收。
未向 PyPI 发布；不要将同名索引包视为本次发布物。
