# LandSat_GRiTS 详细使用说明

文中带日期的试点与 `docs/evidence/` 链接属于历史本机验收材料，不随公开仓库或安装包分发。
新用户请使用 [独立快速开始](docs/QUICKSTART.md)；日／月／年显示见 [Resolver 配套说明](docs/RESOLVER.md)。

仓库介绍、按传感器与产品级别分组的完整波段对应表及快速开始，见 [README](README.md)。本文补充运行配置、格网约定、数据读取和续传细节。当前实现审计、优化与测试见 [2026-10-21 审计报告](docs/AUDIT_20261021.md)。

产品选择见 [README：L1 与 L2 的区别](README.md#l1-与-l2-的区别)；数据来源与 AWS 访问费用见 [README：数据来源与费用](README.md#数据来源与费用)；在线 COG 读取和本地 TIFF 导入的区别见 [README：从 TIFF 到 Zarr 的流程](README.md#从-tiff-到-zarr-的流程)。

浏览当前深圳测试批次可运行 `.venv\Scripts\landsat-grits.exe gui outputs\pilots\shenzhen-20261021`，再打开 http://127.0.0.1:8765 。可将路径替换为其他已完成的归档目录。地图、轨道和场景切换、传感器对照、全部变量图集及数值解释见 [GUI 使用说明](docs/GUI.md)。

将 **Landsat 1–5、7–9 的 Collection 2 公开产品**，先投影到固定的
MGRS extent，再逐景堆叠为 **`(time, bands, x, y)`** Zarr。
Landsat 6 未成功入轨，没有影像。

默认主档案保存全部**现行 Collection 2 Level-1 交付光谱波段**：
Planetary Computer 的 `landsat-c2-l1` 提供 MSS，USGS 的 `landsat-c2l1`
提供 TM、ETM+、OLI/TIRS。PC 的现代 `landsat-c2-l2` 保留为可选独立产品。用户提供的
`https://github.com/lzxcas51651/Landsat_GRITS` 在本次核查返回 404，
因此这里依据官方目录独立实现，未声称复用了该仓库源码。

**全色、OLI 卷云、ETM+ 双增益热红外、TIRS B10/B11 均已纳入 L1。**
现代 L1 像元支持本地已下载产品导入或显式配置的 AWS requester-pays 访问。
免费 STAC 检索不需要像元访问凭证，默认不会启动收费访问。
RBV 和 Landsat 3 历史试验 MSS 热红外不在现行 Collection 2 光谱资产中；
浏览图、角度衍生 TIFF 不作为“原生光谱波段”写入，原始 STAC/MTL 则保留。
详见 [数据源与实测证据](docs/SOURCES.md)。
设计取舍见 [存储合同](docs/DESIGN.md)，本次检查范围见 [验证记录](docs/VALIDATION.md)。

## 波段设计

主档案按**传感器族＋产品级别**分开，同一库内的各组使用自身的发布分辨率，
各自内部按时间堆叠，共享场景身份和提交事务。
同一卫星上的不同传感器分别进入对应产品，例如 Landsat 4/5 的 MSS 与 TM。

| 默认产品 | 卫星 | optical | thermal | pan |
|---|---|---|---|---|
| `mss-l1` | 1–5 | green, red, nir08, nir09，60 m | 无 | 无 |
| `tm-l1` | 4/5 | blue, green, red, nir08, swir16, swir22，30 m | B6，30 m | 无 |
| `etm-l1` | 7 | 同 TM，30 m | B6_VCID_1、B6_VCID_2，30 m | B8，15 m |
| `oli-l1` | 8/9 | coastal, blue, green, red, nir08, swir16, swir22, cirrus，30 m | B10、B11，30 m | B8，15 m |

TM 保存 7 个光谱通道；ETM+ 的 8 个命名波段因 B6 双增益保存为 9 个数据通道；
OLI/TIRS 保存 11 个波段。L8/9 的 OLI-only 或 TIRS-only 合法场景按实际仪器
保留已采集波段，未采集部分 `present=false`；应存在的波段缺失则报错。
热红外传感器原始分辨率是 TM 120 m、ETM+ 60 m、TIRS 100 m，
现行 L1 文件已由发布方重采样至 30 m；本程序不伪称恢复原始探测器格网。

可选 `tm-l2/etm-l2` 保留六个 SR 波段，`oli-l2` 保留七个 SR 波段；
它们与 L1 分库。选择 [config/example_l2.yaml](config/example_l2.yaml) 可使用
公开 PC MSS L1＋现代 L2 路线。L1 已完成基础辐射和几何处理，保留产品 DN；
L2 提供大气校正后的 SR 和可用的 ST，不保留所有 L1 通道，也不提高空间分辨率。
两种级别的数据本身都免费，访问平台可能收取请求、传输或计算费用；
官方产品依据见 [USGS L1](https://www.usgs.gov/landsat-missions/landsat-collection-2-level-1-data)
和 [USGS L2](https://www.usgs.gov/landsat-missions/landsat-collection-2-level-2-science-products)。

不直接用 `B1/B2/...` 作为跨传感器 band 名称：同一编号对应的光谱不同。
每景保留 `source_band`、`platform` 和完整 STAC/MTL；MSS 在 Landsat 1–3
使用 B4–B7，在 Landsat 4/5 使用 B1–B4。

每个产品 Zarr 内部：

| 子组 | 内容 | 存储类型 | 数值解释 |
|---|---|---|---|
| `optical` | 多光谱 | MSS/TM/ETM L1 uint8；OLI L1 与 L2 uint16 | 原始产品 DN |
| `thermal` | L1 热红外或 L2 地表温度，按产品分库 | L1 uint8/uint16；L2 uint16 | L1 为 DN；仅 L2 是 DN × 0.00341802 + 149 K |
| `pan` | L1 全色，15 m | ETM uint8；OLI uint16 | 原始 DN，不降采样到 30 m |
| `ancillary` | 温度不确定度、辐亮度、发射率等 | int16 | 各 band 独立比例；填充 -9999 |
| `qa` | QA_PIXEL、QA_RADSAT 等位编码 | uint16 | 原位编码；uint8 QA 无损提升，原 dtype 另存 |

每组 `data` 和 `valid` 的维度均为 `(time, bands, x, y)`。
**`valid` 只表示有源像元，不等于无云或高质量。**
各变量有效范围可能不同；影像覆盖以测量波段的 `valid` 判断，
QA_RADSAT 等质量波段的有效 0 不能单独作为有影像或无云的依据。
`present(time,bands)` 区分整条 band 缺失和 band 内局部无效。
L2SR 场景缺少温度时仍保留该景，thermal 为填充值且 `present=false`。
ST_QA 属于连续温度不确定度，归入 ancillary，不当位编码解释。

L1 和 L2 写入时都保留源产品整数值；`open_group` 不自动缩放或应用掩膜。
所有 L1 的 `scale=1,offset=0,unit=DN` 仅表示保留原值，不是假定反射率／温度转换。
场景标定系数在原始 MTL 中保存，后续转换应显式处理太阳高度与每景系数。
L2 地表反射率为 `DN × 0.0000275 - 0.2`，先应用 valid 掩膜再缩放。
MSS 只有 L1；其 DN、辐亮度、TOA 反射率和 L2 地表反射率不能混称。
部分 MSS MTL 标定系数是 `"NULL"`，本版本保留 DN 和标定元数据，
不伪造反射率。MSS 两个 NIR 波段均保留。

选择此组织的理由：不用大量空波段扩展每一景，不丢传感器独有波段，
也不会把 L1 和 L2 放进同一个反射率数组。若后续确需统一 band 并集，
可以从主档案生成派生数据；当前提供 L2 六个公共反射率波段的读取接口。
**公共名字和相同像元大小不代表已完成跨传感器光谱响应校正。**

## 格网与时间约定

- 示例读取 `data/mgrs.parquet`，由 `landsat-grits grid` 从官方 KML 准备，或由用户提供。
  清单和归档记录该表 SHA-256、tile、UTM CRS、Affine、尺寸和中心坐标。
- 与 S1 的 `_build_grid_from_mgrs_tile` 相同：WGS84 polygon 投影至 tile UTM，
  向外 floor/ceil 到分辨率整数倍，再构造固定格网。
  extent 在查询影像之前确定，不随场景或年份变化。
- 历史测试使用的 S1 格网为 19,002 个 tile 的覆盖子集；独立转换只保留指定 tile。
  缺失 tile、无效几何和跨日期变更线几何明确报错；可配置替代 GeoParquet。
- 不硬编码 100 km 或 3660 像元。实测 `50RKV` 在 30 m 下为 **3661 × 3661**；
  15 m 下为 **7321 × 7321**，x 原点相差 15 m。每个分辨率都独立从原 polygon
  对齐，不能将 30 m 数组宽高直接乘二。所有 group 的完整格网均冻结到 manifest。
- 原 Landsat WRS 影像通过 nearest 重采样投影到格网，以保留 DN 与 QA 位编码。
  这是空间重投影，不是源像元无重采样复制。
  MSS 60 m 是发布产品像元；热红外 L1/L2 的 30 m 也不是传感器原始空间分辨率。
- `x` 向东递增，`y` 向南递减，坐标为像元中心。
  Rasterio 读出的 `(y,x)` 在写入前显式转置。
- `time` 使用观测 UTC 时间（微秒），不是日期或月份。
  身份为 `collection/item_id`；同一时刻不同场景保留两条观测。
  `platform(time)` 保留每条观测所属卫星。
- 不做月合成、自动去云、SLC-off 缺口插值或跨传感器光谱校正。

## 安装

推荐 Python 3.12。仓库已提供 `uv.lock` 冻结依赖：

```powershell
# 在本仓库目录运行
uv sync --locked --extra dev
.venv\Scripts\landsat-grits.exe --help
```

也可用 Python 3.12 的独立虚拟环境安装 `pip install -e ".[dev]"`。
首次运行请先看 [独立快速开始](docs/QUICKSTART.md)，其中包含离线示例与格网准备。
日／月／年 GUI 由另行安装的 Resolver 提供，见 [配套说明](docs/RESOLVER.md)。
无需 Earth Engine、EarthExplorer 或 Planetary Computer 账户来访问本版本的 PC 路线；
网络请求仍受上游限流与可用性影响。
如选择 AWS Level-1 像元访问，另装 `uv sync --extra dev --extra aws`，
并通过 AWS 标准凭证链配置账户；不把 access key/token 写进 YAML 或清单。

## 操作

复制并编辑 [config/example.yaml](config/example.yaml) 的 `tiles`、起止日期与
`output`。路径相对于 YAML 文件解析。示例只是说明配置，不代表已请求
对武汉或 1972–2026 执行完整生产。

默认 `access: {mode: auto}` 允许发现全部来源；当清单包含现代 USGS L1 时，
`run` 在任何像元下载前要求显式配置访问方式并重新规划：

```yaml
# 已通过 EarthExplorer 等方式下载并解包的 Collection 2 产品
access:
  mode: local
  directory: D:/Landsat/source_scenes
```

本地目录可包含各场景子目录；文件名必须与 STAC 资产的产品文件名一致，
每景包括全部已采集光谱 TIFF、QA 和 `*_MTL.json`。
缺文件、重名文件或场景 MTL 不匹配会报错。
一次 `run` 只枚举源目录一次并复用索引，作业结束后丢弃；独立 API 调用可用
`landsat_grits.access.local_asset_index(access)` 显式限定索引复用范围。
源目录在任务期间应保持只读，输出必须在源目录之外。读取边界及场景提交前检查
已选本地文件状态，作业结束还检查目录变化；全部通过后才生成成功回执。
这些 stat 检查不替代文件内容哈希，也不追溯检测已提交源文件在后续任务之间的内容修改。
也可使用 `access: {mode: aws-requester-pays, aws_profile: your-profile}`；
此选项显式启用申请者付费读取，费用取决于账户和访问量。
这些设置只影响 USGS L1，PC MSS/L2 仍走其公开访问路线。

```powershell
# 查看全部产品、band、资产名和比例
.venv\Scripts\landsat-grits.exe bands

# 全部分页查询，冻结未签名来源清单，不读取栅格像元
.venv\Scripts\landsat-grits.exe plan config\example.yaml --manifest outputs\plan.json

# 严格按清单逐景、按空间窗口下载/重投影/写入；重跑可续传
.venv\Scripts\landsat-grits.exe run outputs\plan.json

# 对照冻结清单检查结构、身份、时间、压缩和来源指纹
.venv\Scripts\landsat-grits.exe verify outputs\plan.json
```

明确有界的试点检索可加 `--limit-per-search 1`，清单会写
`selection=pilot`、`search_complete=false`。它取 API 遍历顺序中的前若干景，
随后排序，**不保证是全部历史的最早景**。试点必须使用单独输出目录；
把较晚试点接着当历史生产会触发历史插入检查。

默认 `max_cloud_cover: null` 保留多云场景与无云量估计场景；
设置百分比会筛选场景，不自动掩膜云像元。
默认不筛掉 Tier 2。真正“全部历史”仍受源集合覆盖、产品存在性和档案完整性约束。

运行输出示例：

```text
<output>/
  50RKV/
    mss-l1/60m.zarr/{optical,qa}/
    tm-l1/30m.zarr/{optical,thermal,qa}/
    etm-l1/30m.zarr/{optical,thermal,pan,qa}/
    oli-l1/30m.zarr/{optical,thermal,pan,qa}/
    oli-l1/30m.zarr.commits.json
    oli-l1/30m.zarr.provenance/<scene-hash>.json
  receipt-<manifest-hash>.json
```

路径中的 `30m.zarr` 表示主格网；其中 `pan` 自有 15 m 坐标和尺寸。
它与 optical/thermal 共享 time，但不共用 x/y 坐标。
使用 Zarr 格式 2，由 Zarr Python 3 写入，兼容 xarray；明确使用
Blosc/Zstd level 5、byte shuffle。默认 `data/valid` chunks 为
`(1,1,512,512)`，边缘按尺寸缩小。内存逐 band、逐窗口有界；
HTTP COG 读取实际传输量由源分块和 overview 决定。
规划输出的容量数是**未压缩数据＋valid 掩膜**，不是网络下载量或压缩后占盘承诺。

## 读取

读取原生全色不做隐式缩放：

```python
from landsat_grits.storage import open_group
pan = open_group(r"D:\archive\50RKV\oli-l1\30m.zarr", "pan")
raw_pan = pan.data.where(pan.valid)
pan.close()
```

以下反射率示例仅适用于另行归档的 **Level-2**：

```python
from landsat_grits.storage import open_group

ds = open_group(r"D:\archive\50RKV\oli-l2\30m.zarr", "optical")
print(ds.data.dims)  # ('time', 'bands', 'x', 'y')
reflectance = ds.data.where(ds.valid) * ds.scale + ds.offset
red = reflectance.sel(bands="red")
# 导出给常规栅格工具前按需转成 (time, bands, y, x)
conventional = reflectance.transpose("time", "bands", "y", "x")
ds.close()
```

TM、ETM+、OLI 同格网六公共反射率波段：

```python
from landsat_grits.reader import open_common_reflectance

common = open_common_reflectance([
    r"D:\archive\49QHE\tm-l2\30m.zarr",
    r"D:\archive\49QHE\etm-l2\30m.zarr",
    r"D:\archive\49QHE\oli-l2\30m.zarr",
], track="WRS2/P122")
print(common.reflectance.dims)
common.close()
```

读取默认采用 Dask 延迟计算，避免一开始把全部年份装进内存；
用 `.isel(...)` 选择范围后再 `.compute()`。
此接口检查 CRS、像元坐标完全一致，拒绝所有 L1、错格网和重复场景。
它保留 platform/scene_id/track 及 WRS Path/Row，掩膜无效值并应用缩放，**不做去云与光谱谐调**。
已知多轨道默认报错；确需联合读取时显式设置 `allow_mixed_tracks=True`，仍逐景保留，不拼接像元。
单景与单轨时序接口、未知轨道处理见 [光学轨道读取](docs/OPTICAL_TRACKS.md)。
MSS 没有官方 L2 SR，不能将其 L1 DN 放入此公共反射率视图。
读取期间应停止对应归档写入；当前不提供并发写入时的读快照。

## 续传与验证边界

每个产品库持有独占文件锁，所有时间 chunk 为单景。先完成该景所有数组、
来源元数据，再原子发布 commit sidecar。网络失败会回滚尾部；
15 m pan 写入失败也会回滚该景所有 30 m 组，不能将半景提交成功。
进程异常后的下一次 `run` 会先验证全部数组长度，再截去最多一景未提交尾部；
超出该范围或提交记录格式错误时拒绝修改，避免把已提交历史误当尾部删除。
已有场景只有身份和来源指纹相同才跳过；改变格网、波段合同、chunk、
同 ID 的来源元数据或缺失提交记录都会报错。
已写晚期景后不允许直接追加更早历史，需输出到新库完整重建。

`verify` 检查冻结清单内场景、来源指纹、Zarr 结构、时间轴和实际压缩配置。
**它不是全部压缩块的内容校验，也不证明上游没有漏景。**
缺场景为退出码 2；异常为 1；成功为 0。无匹配源景单独报告，
不制造空白日期来假装历史完整。当前实现适合本地文件系统单 writer；
尚未实现对象存储事务、分布式调度、并行跨产品完整事务或全球生产容量治理。

自动测试覆盖合成 GeoTIFF 的像元和维度、QA、缺温度、MSS 编号、
重复时间、失败回滚、断电尾部模拟、合同变更、缺组与分页失败。
真实联网试点及其明确范围见 [docs/evidence](docs/evidence)。
现代 L1 的已有验证限于免费 STAC 元数据与本地合成 TIFF；
当前测试区域已改为深圳大学粤海校区所在的 49QHE，完成 Landsat 四类产品五景及 S2 两条轨道两景；详见 [深圳验收报告](docs/VALIDATION_SHENZHEN_20261021.md)。

未执行 AWS Requester Pays 影像访问。此前 17MPU 全格网试点采用 MSS L1 与现代 L2，
各传感器族一景；它不覆盖现代 L1 独有的全色、卷云、ETM+ 双增益热红外或原生 B11 下载。
四景已全部完成整格网归档、全块解码、源值抽样和重复运行检查，详见 [17MPU 验收报告](docs/VALIDATION_17MPU_20261021.md)。L2 成功不等于现代 L1 联网验收通过。

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
.venv\Scripts\python.exe -c "from pathlib import Path; Path('.test-tmp').mkdir(exist_ok=True)"
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp .test-tmp/your-unique-run
.venv\Scripts\python.exe -m ruff check src tests examples tooling/release
```

没有提交全时段或全球下载任务；正式生产前需要配置实际 tile、日期和数据根目录。
