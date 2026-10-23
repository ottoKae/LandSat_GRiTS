# 独立安装与快速开始

使用 Python 3.12 或 3.13。下载或克隆本仓库后，在仓库目录安装；不需要 S1-GRiTS、
S2-GRiTS 或 GRiTS-Resolver：

```sh
git clone https://github.com/ottoKae/LandSat_GRiTS.git
cd LandSat_GRiTS
```

```sh
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux / macOS:
# source .venv/bin/activate
python -m pip install .
landsat-grits --help
```

如果使用 uv，安装命令为 `uv sync --locked`，之后用 `uv run landsat-grits ...`。
首次测试版为 `0.2.0b1`，发布物见 [GitHub Releases](https://github.com/ottoKae/LandSat_GRiTS/releases)。
请从本仓库或 GitHub Release 安装。

## 先运行离线示例

```sh
python examples/offline_demo.py --output outputs/offline-demo
landsat-grits verify outputs/offline-demo/manifest.json
landsat-grits gui outputs/offline-demo/archive
```

浏览器打开 http://127.0.0.1:8765 。示例生成两个日期、64×64 像元的人工 OLI L2 数据，
检查 TIFF→Zarr、维度、有效性和读取流程。它不联网，也不是实际卫星影像或完整 MGRS 瓦片。
再次运行请换一个新输出目录；脚本不会覆盖现有数据。

## 准备真实格网

```sh
landsat-grits grid --tiles 49QHE 50RKV --output data/mgrs.parquet
```

该命令只下载约 10 MB 的官方格网 ZIP，不下载影像。来源是
[Copernicus Sentinel-2 tiling grid](https://sentiwiki.copernicus.eu/web/s2-products)。
固定 SHA-256 防止上游内容静默变化；优先使用官方高精度 `LL_WKT`，避免显示坐标舍入导致
像元对齐变化。输出只含指定瓦片，并在每行记录源文件、URL 和摘要。

已有官方 UTF-8 KML／ZIP 时，可离线转换（拒绝 DTD 和实体声明）：

```sh
landsat-grits grid --source /path/to/official-grid.zip --tiles 49QHE --output data/mgrs.parquet
```

也可直接使用自己的 GeoParquet，至少含唯一的 `mgrs_tile_id`、带 CRS 的 polygon geometry；
`utm_epsg` 如存在必须与瓦片一致。程序拒绝缺失瓦片、无效几何和跨日期变更线几何。
转换不会覆盖已有格网。历史归档续传必须使用原来的冻结清单和格网，不用新表替换旧表。

## 一个公开 L2 下载示例

检查 `examples/shenzhen_l2.yaml` 后执行：

```sh
landsat-grits plan examples/shenzhen_l2.yaml --manifest outputs/shenzhen-plan.json
landsat-grits run outputs/shenzhen-plan.json
landsat-grits verify outputs/shenzhen-plan.json
landsat-grits gui outputs/shenzhen-l2
```

该配置查询 49QHE、2025-12-17、Landsat 9 OLI L2。`plan` 只冻结来源清单；
`run` 才读取影像并生成整格网 Zarr，耗时与空间随实际场景数变化。
同一清单可续跑，已提交场景会跳过。清单文件已存在时，请换名，不覆盖历史选择。

配置中的路径相对 YAML 文件解析；CLI 的文件参数相对当前目录解析。
换区域时，先准备对应瓦片的格网，再修改 `tiles`、日期、输出和 `mgrs_path`。
`config/example_l2.yaml` 是多年、多传感器模板，不建议未经查看就执行长时段下载。

## 完整 L1 波段

`config/example.yaml` 包括 MSS、TM、ETM+、OLI/TIRS L1。MSS 可走公开在线源；
现代 L1 需先准备本地已解包 TIFF 与 MTL，并在 YAML 设置：

```yaml
access:
  mode: local
  directory: ../source_scenes
```

显式 AWS Requester Pays 是另一条可选路线：先安装 `python -m pip install '.[aws]'`，
再配置 `access: {mode: aws-requester-pays, aws_profile: your-profile}`。
凭据由 AWS 标准配置提供，不写入 YAML。程序没有 EarthExplorer 登录自动下载功能。

## 日／月／年合成

本仓库的 `gui` 显示逐景数据。日／月／年合成由独立安装的 GRiTS-Resolver 提供；
见 [安装与相对路径配置](RESOLVER.md)。归档始终按景存储，年度显示不生成年度 Zarr。
