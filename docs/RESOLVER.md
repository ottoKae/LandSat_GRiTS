# 可选 GRiTS-Resolver

LandSat_GRiTS 独立负责下载、格网化、逐景 Zarr、轨道读取和单景 GUI。
GRiTS-Resolver 是另一份独立软件，读取已提交的 Landsat 归档，提供日／月／年合成显示。
本仓库没有复制 Resolver，也不会在导入或安装时自动寻找相邻仓库。

## 安装

在已安装 LandSat_GRiTS 的同一个 Python 3.12 环境中，安装取得的、含光学日合成查看器的
GRiTS-Resolver 0.3.0 源码目录或 wheel：

```sh
python -m pip install /path/to/GRiTS-Resolver
# 或使用正式取得的本地 wheel：
# python -m pip install /path/to/grits_resolver-0.3.0-py3-none-any.whl
python -c "from grits_resolver import OpticalDailyResolver; print('Resolver ready')"
```

路径由用户指定，两份源码不要求相邻。当前配套版本要求 Python 3.12；Landsat 核心支持
3.12／3.13 不等于此配套版本也支持 3.13。Resolver 的分发地址与许可由其仓库提供；
在取得对应发布物之前，单独安装本仓库不会得到合成 GUI。这里不假设 PyPI 的同名包就是配套版本。

## 离线演示

先运行本仓库 `examples/offline_demo.py`，再执行：

```sh
python -m grits_resolver.daily_viewer --tile-config outputs/offline-demo/resolver.json --port 8771
```

打开 http://127.0.0.1:8771 。该配置使用相对路径，两个日期均为人工数据，
只用于检查安装、日期分组和界面，不能解释为遥感观测结果。

## 实际归档

完成 `examples/shenzhen_l2.yaml` 的归档后，可以使用：

```sh
python -m grits_resolver.daily_viewer --tile-config examples/resolver_year.json --port 8771
```

这个下载示例只有一天；选择“年度”只显示该年已下载的观测，不代表全年完整。
查看自己的年份时，修改 JSON 的 `stores`、`default_year`、`bands` 和标签。
`stores` 与 `cache_dir` 均相对 JSON 所在目录解析，绝对路径也可使用。
多批次可以在 `stores` 中列出同瓦片、同产品及一致格网的多个库；每类传感器单独一个 archive。

合成按 QA 筛选，一个输出像元的全部请求光学波段来自同一景。Landsat 同分时优先较早
UTC 时间，再按场景 ID 排序；逐像元来源索引保留真实场景、轨道和时间。
同日允许跨轨道，日合成不借其他日期填洞；月／年显示才扩展日期范围。
默认不混卫星，不融合 S2，不做跨传感器辐射一致化。缺口保留，无云率不等于有效覆盖率。

源 Zarr 不变。月／年界面仅创建显示缓存；需要导出日合成时，可显式调用 Resolver API，
将结果写到新位置。不要在读取期间向同一个源库追加。
