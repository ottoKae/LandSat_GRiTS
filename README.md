# LandSat_GRiTS

[![CI](https://github.com/ottoKae/LandSat_GRiTS/actions/workflows/ci.yml/badge.svg)](https://github.com/ottoKae/LandSat_GRiTS/actions/workflows/ci.yml)
[GitHub Releases](https://github.com/ottoKae/LandSat_GRiTS/releases) · [MIT](LICENSE)

**LandSat_GRiTS 将 Landsat Collection 2 影像整理为固定 MGRS 格网上的 Zarr 时间序列。** 仓库沿用 S1-GRiTS 的思路：先确定空间格网，再逐景重投影，按 **`(time, bands, x, y)`** 堆叠。

数据**按传感器族与产品级别分组**，保留对应产品的全部光谱测量波段及质量标志。支持 Landsat 1–5、7–9 的 MSS、TM、ETM+、OLI/TIRS；Landsat 6 未成功入轨，没有影像。默认归档 Level-1（L1），也支持独立归档 Level-2（L2）地表反射率和地表温度。

当前支持单机本地归档、来源清单冻结、场景级续传、xarray/Dask 按需读取，以及本地网页 GUI。完整历史覆盖取决于上游产品和配置的时间范围；支持某颗卫星不代表已下载其全部影像。

**同一瓦片内按轨道、按景读取**：使用 `list_tracks → list_scenes → open_scene` 明确选择来源，或用 `open_track` 读取单轨时序。Landsat 轨道包含 WRS 系统与 Path，Row 单独保留；缺测部分不自动从另一轨、另一日期补齐。详见 [光学轨道读取](docs/OPTICAL_TRACKS.md)。

**同瓦片日合成**：GRiTS-Resolver 的 `OpticalDailyResolver` 按 UTC 日读取本仓库的单景归档，
允许同日跨轨，在质量筛选后逐像元选取完整波段组，并保留来源索引、场景 ID、轨道和实际时间。
原始 Zarr 保持不变；日合成可单独导出为 `(time,bands,y,x)` Zarr。
L2 按逐景系数输出物理量，MSS 等 L1 保留 DN；缺测保持无效。
使用方法、明确的质量规则和边界见 [Resolver 配套说明](docs/RESOLVER.md)。

## 独立安装与示例

**首次测试版：0.2.0b1，MIT 开源。** 下载、归档、读取和单景 GUI 不依赖其他 GRiTS 仓库。
日／月／年合成保持由可选的 GRiTS-Resolver 提供，见 [配套安装](docs/RESOLVER.md)。

在 Python 3.12／3.13 环境中，从仓库目录执行：

```sh
python -m pip install .
python examples/offline_demo.py --output outputs/offline-demo
landsat-grits gui outputs/offline-demo/archive
```

打开 http://127.0.0.1:8765 。离线示例为两个日期的人工数据，用于检查安装，不是真实影像。
使用 uv 可先运行 `uv sync --locked`，然后在上述运行命令前加 `uv run`。

实际影像先准备格网，再冻结选择和下载：

```sh
landsat-grits grid --tiles 49QHE --output data/mgrs.parquet
landsat-grits plan examples/shenzhen_l2.yaml --manifest outputs/shenzhen-plan.json
landsat-grits run outputs/shenzhen-plan.json
landsat-grits verify outputs/shenzhen-plan.json
landsat-grits gui outputs/shenzhen-l2
```

`grid` 只获取官方格网元数据，`plan` 只检索来源，`run` 才下载影像。
[完整快速开始](docs/QUICKSTART.md) 包含虚拟环境、离线格网转换、L1 本地导入与路径规则。

## L1 与 L2 的区别

L1、L2 表示处理级别，选择取决于需要保留哪些波段、分析什么物理量。

| 对比 | Level-1（L1） | Level-2（L2） |
|---|---|---|
| 处理与含义 | 已完成基础辐射和几何处理；交付为整数 DN，物理标定参数在 MTL 中 | 由 L1 生成大气校正后的地表反射率（SR）；条件满足时提供地表温度（ST） |
| 波段范围 | 保留相应传感器产品的全部光谱通道 | 不保留全部 L1 通道，例如全色、OLI 卷云和原生 TIRS B11 |
| 像元间距 | MSS 60 m；现代多光谱 30 m，全色 15 m | SR/ST 交付为 30 m；处理级别提高不意味着空间分辨率提高 |
| 适用任务 | 原生波段归档、自行标定或大气校正、全色及热红外研究 | 地表反射率时间序列、植被／土地覆盖和地表温度分析 |

L2 仍可能包含云、云影和无效像元，需结合 QA 筛选；本仓库不会自动去云。MSS 没有官方 Collection 2 L2 产品，保留为 `mss-l1`。产品定义见 [USGS L1 说明](https://www.usgs.gov/landsat-missions/landsat-collection-2-level-1-data)、[L2 说明](https://www.usgs.gov/landsat-missions/landsat-collection-2-level-2-science-products)和 [MSS L2 范围说明](https://www.usgs.gov/faqs/how-do-landsat-collection-2-level-2-products-compare-products-collection-1)。

## 数据来源与费用

程序先通过 STAC 目录检索场景，再读取目录指向的影像文件。目录检索和影像读取是两个步骤。

| 产品 | 检索与数据来源 | Collection | 当前读取方式 |
|---|---|---|---|
| `mss-l1` | [Microsoft Planetary Computer（PC）](https://planetarycomputer.microsoft.com/dataset/landsat-c2-l1) | `landsat-c2-l1` | 获取临时访问签名，在线读取公开 COG |
| `tm-l1`、`etm-l1`、`oli-l1` | [USGS STAC](https://landsatlook.usgs.gov/stac-server) | `landsat-c2l1` | 导入本地已下载产品，或配置 AWS Requester Pays 在线读取 |
| `tm-l2`、`etm-l2`、`oli-l2` | [Microsoft Planetary Computer（PC）](https://planetarycomputer.microsoft.com/dataset/landsat-c2-l2) | `landsat-c2-l2` | 获取临时访问签名，在线读取公开 COG |

**L1 和 L2 数据本身都免费。** USGS EarthExplorer 仍提供免费下载；AWS Requester Pays 是另一种云端访问方式，由请求方的 AWS 账户承担请求和适用的数据传输费用，费用取决于请求次数、传输量和访问位置，不按像元单独出售影像。参见 [USGS 费用说明](https://www.usgs.gov/faqs/when-do-i-need-amazon-web-services-aws-account-access-landsat-data-cloud-there-a-cost)和 [AWS 计费机制](https://docs.aws.amazon.com/AmazonS3/latest/userguide/RequesterPaysBuckets.html)。

本仓库的 PC 路线无需配置 AWS 账户。现代 L1 可以先从 EarthExplorer 下载，再用本地模式导入；**仓库尚未实现 EarthExplorer 登录或 M2M 自动下载**。只有显式设置 `access.mode: aws-requester-pays` 才会启用该云端计费访问；默认 `auto` 不启用。

## 从 TIFF 到 Zarr 的流程

**源影像是 GeoTIFF／COG，归档结果是 Zarr。** COG 是适合远程按块读取的 GeoTIFF。在线模式按窗口读取源文件，在内存中重投影后直接写入 Zarr，无需先把完整景下载到本地，也不生成中间 TIFF。

```text
MGRS extent → 固定 UTM 格网 → STAC 检索 → 冻结场景清单
                                               ↓
在线模式：远端 COG ──按窗口读取──┐
                              ├→ 内存中重投影 → 写入 Zarr 块 → 完成一景后提交
本地模式：已下载、解包的 TIFF ──┘
```

两条路线使用相同的重投影和写入流程。本地模式需要事先准备 TIFF 和 MTL 元数据；程序读取后保留源文件。在线读取仍会传输源数据块和元数据，传输量不等于目标 Zarr 的大小。

## 数据组织与波段

归档层级为 **`MGRS tile / 传感器与产品级别 / Zarr / 变量组`**。按传感器族分库可保留独有波段，按级别分库可区分 L1 DN 与 L2 地表物理量，避免把含义不同的数值放入同一时间序列。

- 同一传感器族、同一产品级别的不同卫星进入同一个时间序列，通过 `platform(time)` 保留卫星身份。例如，Landsat 4/5 的 TM Level-1 共用 `tm-l1`，其 MSS 观测进入 `mss-l1`。
- Level-1 与 Level-2 分开保存。L1 的原始数字量化值（DN）、L2 地表反射率和 L2 地表温度具有不同含义。
- 每个产品内部按 `optical`、`thermal`、`pan`、`ancillary`、`qa` 分组，各组保留对应的数据类型和交付像元间距。
- `bands` 使用 `red`、`nir08` 等语义名称，同时通过 `source_band(time,bands)` 保留原始波段编号。相同语义名称不意味着不同传感器的光谱响应相同。

### Level-1：默认的原生波段档案

默认配置见 [config/example.yaml](config/example.yaml)。下表的波段数量不含 QA。

| 产品键 | Landsat 卫星 | 传感器 | 多光谱组 `optical` | 热红外组 `thermal` | 全色组 `pan` | 光谱通道总数 |
|---|---|---|---|---|---|---:|
| `mss-l1` | 1–5 | MSS | 4 个，60 m | — | — | 4 |
| `tm-l1` | 4、5 | TM | 6 个，30 m | B6，30 m | — | 7 |
| `etm-l1` | 7 | ETM+ | 6 个，30 m | B6_VCID_1、B6_VCID_2，30 m | B8，15 m | 9 |
| `oli-l1` | 8、9 | OLI/TIRS；含 OLI-2/TIRS-2 | 8 个，30 m | B10、B11，30 m | B8，15 m | 11 |

ETM+ 的 8 个命名波段因 B6 包含两个增益，保存为 9 个数据通道。OLI/TIRS 的 11 个波段包含全色、卷云和两个热红外波段；OLI-only 或 TIRS-only 场景按实际采集情况保存，其余位置记录为 `present=false`。应有波段缺失则报错。

以下为 **L1 光谱波段原始编号与 Zarr 波段名称的完整对应表**；“—”表示该传感器没有对应通道。

| Zarr 组 | `bands` 名称 | 含义 | MSS：卫星 1–3 | MSS：卫星 4–5 | TM：卫星 4–5 | ETM+：卫星 7 | OLI/TIRS：卫星 8–9 |
|---|---|---|---|---|---|---|---|
| `optical` | `coastal` | 海岸／气溶胶 | — | — | — | — | B1 |
| `optical` | `blue` | 蓝光 | — | — | B1 | B1 | B2 |
| `optical` | `green` | 绿光 | B4 | B1 | B2 | B2 | B3 |
| `optical` | `red` | 红光 | B5 | B2 | B3 | B3 | B4 |
| `optical` | `nir08` | 近红外；MSS 为第一个近红外通道 | B6 | B3 | B4 | B4 | B5 |
| `optical` | `nir09` | MSS 第二个近红外通道 | B7 | B4 | — | — | — |
| `optical` | `swir16` | 短波红外 1 | — | — | B5 | B5 | B6 |
| `optical` | `swir22` | 短波红外 2 | — | — | B7 | B7 | B7 |
| `optical` | `cirrus` | 卷云 | — | — | — | — | B9 |
| `thermal` | `lwir` | TM 热红外／ETM+ 低增益热红外 | — | — | B6 | B6_VCID_1 | — |
| `thermal` | `lwir_high` | ETM+ 高增益热红外 | — | — | — | B6_VCID_2 | — |
| `thermal` | `lwir11` | TIRS 热红外 1 | — | — | — | — | B10 |
| `thermal` | `lwir12` | TIRS 热红外 2 | — | — | — | — | B11 |
| `pan` | `pan` | 全色 | — | — | — | B8 | B8 |

这里的分辨率指 **Collection 2 交付产品的像元间距**：MSS 为 60 m；热红外产品交付为 30 m，而 TM、ETM+、TIRS 的原始热探测器分辨率分别为 120、60、100 m。全色组保持 15 m，并在 MGRS extent 上独立建格网。

### Level-2：独立的地表反射率与温度档案

可选配置见 [config/example_l2.yaml](config/example_l2.yaml)。MSS 没有官方 Collection 2 Level-2 产品，该配置仍以 `mss-l1` 保存早期 MSS。下列 L2 产品的各组像元间距均为 30 m。

| 产品键 | Landsat／传感器 | `optical`：名称 → 原始编号 | `thermal`：名称 → 原始编号 | `ancillary` | `qa` |
|---|---|---|---|---|---|
| `tm-l2` | 4、5／TM | blue → SR_B1；green → SR_B2；red → SR_B3；nir08 → SR_B4；swir16 → SR_B5；swir22 → SR_B7 | surface_temperature → ST_B6 | 8 个 ST 辅助变量＋SR_ATMOS_OPACITY | QA_PIXEL、QA_RADSAT、SR_CLOUD_QA |
| `etm-l2` | 7／ETM+ | blue → SR_B1；green → SR_B2；red → SR_B3；nir08 → SR_B4；swir16 → SR_B5；swir22 → SR_B7 | surface_temperature → ST_B6 | 8 个 ST 辅助变量＋SR_ATMOS_OPACITY | QA_PIXEL、QA_RADSAT、SR_CLOUD_QA |
| `oli-l2` | 8、9／OLI-TIRS | coastal → SR_B1；blue → SR_B2；green → SR_B3；red → SR_B4；nir08 → SR_B5；swir16 → SR_B6；swir22 → SR_B7 | surface_temperature → ST_B10 | 8 个 ST 辅助变量 | QA_PIXEL、QA_RADSAT、SR_QA_AEROSOL |

8 个 ST 辅助变量为 `ST_QA`、`ST_TRAD`、`ST_URAD`、`ST_DRAD`、`ST_ATRAN`、`ST_EMIS`、`ST_EMSD`、`ST_CDIST`。其中 **ST_QA 是连续的温度不确定度**，归入 `ancillary`；位编码质量标志归入 `qa`。全部 L1 产品也保存 QA_PIXEL 和 QA_RADSAT。

L2 不提供 L1 的全色、卷云和原生 B11 通道，因此与 L1 独立归档。只有反射率的 L2SR 场景仍会保留；缺少的温度与辅助变量以填充值和 `present=false` 表示。完整变量名称、单位和比例系数以[波段注册表](src/landsat_grits/bands.py)为准，也可运行 `landsat-grits bands` 查看。

### 数值与质量标志

| 变量组 | 存储内容与类型 | 读取解释 |
|---|---|---|
| `optical` | L1 MSS/TM/ETM+ 为 `uint8`；L1 OLI 和 L2 为 `uint16` | L1 保留 DN；L2 地表反射率为 `DN × 0.0000275 - 0.2` |
| `thermal` | L1 TM/ETM+ 为 `uint8`；L1 TIRS 和 L2 为 `uint16` | L1 保留 DN；L2 地表温度为 `DN × 0.00341802 + 149` K |
| `pan` | ETM+ 为 `uint8`；OLI 为 `uint16` | L1 原始 DN，15 m |
| `ancillary` | L2 连续辅助变量，`int16` | 逐波段应用 `scale/offset`，填充值为 `-9999` |
| `qa` | 质量位编码，统一存为 `uint16` | 保留原始位值和 `source_dtype`；按传感器对应位定义解释 |

L1、L2 均保留源产品的整数值，写入时不转换成浮点物理量。`open_group` 返回这些整数及解释字段；读取 L2 时先应用 `valid` 掩膜，再用 `data × scale + offset` 转换。**`valid` 表示像元有效性，不等于无云或高质量。** QA_RADSAT 的 0 是有效值。L1 光谱波段的 `scale=1, offset=0, unit=DN` 只表示保留 DN，不能据此得到反射率或温度；物理标定系数保存在原始 MTL 中。

各变量的有效范围可能不同。影像覆盖应以测量波段的 `valid` 判断，质量波段的有效 0 不能单独作为有影像或无云的依据。

## MGRS 格网与 Zarr 设计

示例使用本仓库 `data/mgrs.parquet`，可通过 `landsat-grits grid` 从官方格网生成，也可提供自己的 GeoParquet。每个 tile 的 WGS84 多边形先投影到对应 UTM，取外包矩形，再按目标像元间距向外对齐；格网在影像检索前确定，不随场景或年份变化。输出覆盖这个矩形，不按多边形边缘再次裁切。清单保存格网参数与源表 SHA-256。

格网命令只写入指定的瓦片，拒绝缺失瓦片、无效几何和跨日期变更线几何。也可使用自己的 GeoParquet。不同分辨率分别对齐，同一 extent 的 15 m 数组尺寸不能简单由 30 m 尺寸乘二得到。

```text
<output>/
└── <MGRS tile>/
    ├── mss-l1/60m.zarr/    → optical, qa
    ├── tm-l1/30m.zarr/     → optical, thermal, qa
    ├── etm-l1/30m.zarr/    → optical, thermal, pan, qa
    ├── oli-l1/30m.zarr/    → optical, thermal, pan, qa
    ├── tm-l2/30m.zarr/     → optical, thermal, ancillary, qa
    ├── etm-l2/30m.zarr/    → optical, thermal, ancillary, qa
    └── oli-l2/30m.zarr/    → optical, thermal, ancillary, qa
```

每个 `.zarr` 是包含数组、元数据和压缩块的目录。只生成配置中选定且有场景的产品。路径中的 `30m.zarr` 表示主格网；其中 `pan` 使用独立的 15 m 坐标，各组共享场景时间与身份。

| 数组／坐标 | 维度 | 用途 |
|---|---|---|
| `data`、`valid` | `(time, bands, x, y)` | 原始数据与像元有效掩膜 |
| `present` | `(time, bands)` | 区分整个波段缺失与局部像元无效 |
| `scale`、`offset`、`source_band`、`source_dtype` | `(time, bands)` | 数值解释与原始波段信息 |
| `time`、`scene_id`、`platform` | `(time)` | UTC 观测时间、来源场景、卫星身份 |
| `bands`、`x`、`y` | 各自的一维坐标 | 波段名称与投影坐标中的像元中心 |
| `spatial_ref` | 标量 | CRS 信息 |

`x` 向东递增，`y` 向南递减；写入前将常规栅格的 `(y,x)` 转置为 `(x,y)`。空间重投影采用最近邻，保留源像元的 DN 和 QA 位值。`time` 记录每景实际的 UTC 观测时间；同一时刻的不同场景分别保留，不自动拼成一景或合成为月数据。

### 分块与压缩

- 使用 **Zarr 格式 2**，压缩方式为 Blosc/Zstd，级别 5。
- `data/valid` 默认 chunks 为 **`(1,1,512,512)`**：每块保存一景、一个波段、一个空间窗口。小于 512 像元的格网按实际尺寸分块。
- 时间块长度为 1，新增场景不需要重写已提交的历史像元块；单波段分块便于按需读取。
- 不同分辨率和变量类型分组保存，避免把 15 m 全色降为 30 m，或把 QA 与反射率混成同一数组。

这种设计适合逐景追加和空间窗口读取。长期、多波段归档会产生较多小文件，跨大量日期读取小区域也需访问多个块。`plan` 提供未压缩数组字节数和 `data/valid` 块数量上限，便于预估规模；它们不代表实际磁盘占用或网络下载量。

### 提交、续传与来源记录

每个产品库旁保存以下文件：

```text
30m.zarr/                  # 分组数组
30m.zarr.commits.json       # 已完成的场景及来源指纹
30m.zarr.provenance/        # 逐景 STAC、MTL 和源栅格信息
30m.zarr.lock               # 单写入者锁
```

一景的各组数据写完并通过检查后，才更新提交记录。再次运行同一清单会跳过已提交且来源指纹一致的场景；失败场景清理未提交的尾部后重新写入，**续传粒度为场景**。提交记录或数组结构异常时停止处理，详细恢复边界见[存储设计](docs/DESIGN.md)。

归档按观测时间和场景标识顺序追加，不支持插入到已归档历史中。读取期间应停止向同一个库写入。长期保存或迁移时，应一并保留 Zarr、提交记录、来源目录及冻结清单。

### 已落实的读写优化

- 每个波段复用数组句柄，每组集中写入波段元数据，减少重复访问。
- 目录请求支持分页和有界重试；PC 临时签名按有效期复用，持久化来源记录时移除访问凭据。
- 本地源目录每个作业只建立一次文件索引；读取前后和场景提交前检查源文件状态。
- 写入前检查格网、波段、类型、分块和提交记录，防止不兼容数据追加到已有库。

## Python 读取

读取 L1 多光谱原始 DN，并只计算一个小窗口：

```python
from landsat_grits.storage import open_group

ds = open_group("outputs/archive/50RKV/oli-l1/30m.zarr", "optical")
try:
    print(ds.data.dims)  # ('time', 'bands', 'x', 'y')
    window = ds.isel(time=slice(0, 1), x=slice(0, 256), y=slice(0, 256))
    red_dn = window.data.sel(bands="red").where(
        window.valid.sel(bands="red")
    ).compute()
finally:
    ds.close()
```

读取 15 m 全色数据时，将 `open_group` 的组名 `"optical"` 改为 `"pan"`，并将两处 `bands="red"` 改为 `bands="pan"`。对于 L2 光学数据，先掩膜再按 `data * scale + offset` 转成地表反射率。

`landsat_grits.reader.open_common_reflectance` 可从同格网的 `tm-l2`、`etm-l2`、`oli-l2` 读取六个公共反射率波段：`blue, green, red, nir08, swir16, swir22`，并应用有效性掩膜和比例系数。该接口仅接受 L2，**MSS L1 不进入公共 SR 视图**；它保留场景及轨道身份、检查格网一致性，不执行跨传感器光谱响应校正。建议指定 `track="WRS2/P122"`；已知多轨道默认拒绝，明确需要时设置 `allow_mixed_tracks=True`，仍逐景保留，不拼接像元。示例见[轨道读取说明](docs/OPTICAL_TRACKS.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [GUI 使用说明](docs/GUI.md) | 启动、地图与图集、单位、图例及预览边界 |
| [光学轨道读取](docs/OPTICAL_TRACKS.md) | Landsat 单景、单轨接口及多轨道规则 |
| [详细使用说明](docs/USAGE.md) | 配置、续传、读取和运行约定 |
| [存储设计](docs/DESIGN.md) | 格网、数组、提交与一致性约定 |
| [数据源说明](docs/SOURCES.md) | 官方来源、产品范围、波段与标定依据 |

## 许可与开发

代码采用 [MIT](LICENSE)；卫星产品及外部格网遵循各自来源条件，见 [NOTICE](NOTICE.md)。
[贡献与测试](CONTRIBUTING.md) · [首次发布说明](docs/RELEASE.md) · [变更记录](CHANGELOG.md)。
