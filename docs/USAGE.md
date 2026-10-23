# 详细使用说明

配置、读取和续传约定。安装与示例见 [快速开始](QUICKSTART.md)，
产品级别和完整波段表见 [README](../README.md)，日／月／年显示见 [Resolver](RESOLVER.md)。

## 格网与时间约定

- 示例读取 `data/mgrs.parquet`，由 `landsat-grits grid` 从官方 KML 准备，或由用户提供。
  清单和归档记录该表 SHA-256、tile、UTM CRS、Affine、尺寸和中心坐标。
- 与 S1 的 `_build_grid_from_mgrs_tile` 相同：WGS84 polygon 投影至 tile UTM，
  向外 floor/ceil 到分辨率整数倍，再构造固定格网。
  extent 在查询影像之前确定，不随场景或年份变化。
- 格网转换只保留指定 tile。
  缺失 tile、无效几何和跨日期变更线几何明确报错；可配置替代 GeoParquet。
- 不硬编码 100 km 或 3660 像元。每个分辨率都独立从原 polygon 对齐，
  不能将 30 m 数组宽高直接乘二。所有 group 的完整格网均冻结到 manifest。
- 原 Landsat WRS 影像通过 nearest 重采样投影到格网，以保留 DN 与 QA 位编码。
  这是空间重投影，不是源像元无重采样复制。
  MSS 60 m 是发布产品像元；热红外 L1/L2 的 30 m 也不是传感器原始空间分辨率。
- `x` 向东递增，`y` 向南递减，坐标为像元中心。
  Rasterio 读出的 `(y,x)` 在写入前显式转置。
- `time` 使用观测 UTC 时间（微秒），不是日期或月份。
  身份为 `collection/item_id`；同一时刻不同场景保留两条观测。
  `platform(time)` 保留每条观测所属卫星。
- 不做月合成、自动去云、SLC-off 缺口插值或跨传感器光谱校正。

## 操作

复制并编辑 [config/example.yaml](../config/example.yaml) 的 `tiles`、起止日期、
`mgrs_path` 与 `output`。路径相对于 YAML 文件解析；执行前确认下载范围。

默认 `access: {mode: auto}` 允许发现全部来源；当清单包含现代 USGS L1 时，
`run` 在任何像元下载前要求显式配置访问方式并重新规划：

```yaml
# 已通过 EarthExplorer 等方式下载并解包的 Collection 2 产品
access:
  mode: local
  directory: ../source_scenes
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

```sh
# 查看全部产品、band、资产名和比例
landsat-grits bands

# 全部分页查询，冻结未签名来源清单，不读取栅格像元
landsat-grits plan config/example.yaml --manifest outputs/plan.json

# 严格按清单逐景、按空间窗口下载/重投影/写入；重跑可续传
landsat-grits run outputs/plan.json

# 对照冻结清单检查结构、身份、时间、压缩和来源指纹
landsat-grits verify outputs/plan.json
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
pan = open_group("outputs/archive/50RKV/oli-l1/30m.zarr", "pan")
raw_pan = pan.data.where(pan.valid)
pan.close()
```

以下反射率示例仅适用于另行归档的 **Level-2**：

```python
from landsat_grits.storage import open_group

ds = open_group("outputs/archive/50RKV/oli-l2/30m.zarr", "optical")
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
    "outputs/archive/49QHE/tm-l2/30m.zarr",
    "outputs/archive/49QHE/etm-l2/30m.zarr",
    "outputs/archive/49QHE/oli-l2/30m.zarr",
], track="WRS2/P122")
print(common.reflectance.dims)
common.close()
```

读取默认采用 Dask 延迟计算，避免一开始把全部年份装进内存；
用 `.isel(...)` 选择范围后再 `.compute()`。
此接口检查 CRS、像元坐标完全一致，拒绝所有 L1、错格网和重复场景。
它保留 platform/scene_id/track 及 WRS Path/Row，掩膜无效值并应用缩放，**不做去云与光谱谐调**。
已知多轨道默认报错；确需联合读取时显式设置 `allow_mixed_tracks=True`，仍逐景保留，不拼接像元。
单景与单轨时序接口、未知轨道处理见 [光学轨道读取](OPTICAL_TRACKS.md)。
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
