# 光学数据：按轨道、按景读取

**MGRS 决定空间格网，轨道决定观测几何，场景决定一次产品观测。三者分别保存。** 同一个瓦片可以被不同轨道部分覆盖，不能为了填满瓦片而自动拼接不同轨道、日期或产品的像元。

用户明确选择的派生功能是 **同一 MGRS 瓦片内，同日多景合成**。现在由
`grits_resolver.OpticalDailyResolver.read_day/read_days` 实现：同日跨轨可以参与，
逐像元质量选景，全部请求波段取自同一来源，并返回 `source_index` 与完整来源表。
原始单景接口继续保持上述身份约束。详见 [日合成使用说明](../../GRiTS-Resolver/README.md#同一-mgrs-瓦片的日合成)。

本次参考 [GRiTS-Resolver 的 S1 读取实现](../../GRiTS-Resolver/src/grits_resolver/s1.py)：列出全部轨道，明确选择来源，再分别读取数据与有效掩膜。`read_common_pair()` 的共同时间／有效掩膜约束不是像元拼接。其 [S2 采集身份实现](../../GRiTS-Resolver/src/grits_resolver/s2_acquisition.py)也区分连续采集与空间分块；不同轨道不能当作同次采集去重。

## 身份与组织

| 对象 | Landsat | Sentinel-2 |
|---|---|---|
| 空间瓦片 | 固定 MGRS extent，经源表外扩对齐 | 原生 MGRS 产品格网，10/20/60 m 各自保存 |
| 重复地面轨道 | `WRS1/P130`、`WRS2/P122` 等 | 相对轨道，如 R032、R132 |
| 沿轨场景框 | WRS Row，例如 044；Row 不等于另一条轨道 | MGRS tile 表示空间分块 |
| 精确产品身份 | `collection/scene_id` 与 UTC | `collection/item_id` 与 UTC |
| 连续采集身份 | 不用日期代替产品身份 | 卫星 + datatake 开始时间 + 绝对轨道；不以相对轨道代替 |

Landsat 1–3 使用 WRS-1，4–9 使用 WRS-2；MSS 跨越两套系统，相同 Path 数字不能直接合并。Path 表示轨迹，Row 表示沿轨的景中心位置。依据见 [USGS WRS 说明](https://www.usgs.gov/landsat-missions/landsat-shapefiles-and-kml-files)和 [NASA WRS 定义](https://science.nasa.gov/mission/landsat/worldwide-reference-system/)。S2 的相对轨道范围为 1–143，datatake 和瓦片的关系见 [Copernicus 产品说明](https://sentiwiki.copernicus.eu/web/s2-products)。

Landsat 保持原有 `tile/product/*.zarr/group` 和 `(time,bands,x,y)`，不按轨道复制像元。轨道索引从提交记录对应的原始 STAC provenance 读取，校验场景身份、时间及来源指纹。缺轨道元数据时明确标为未知；未知景分别列出。已引用的来源文件缺失、被修改或存在身份冲突时拒绝读取。

## Landsat 读取接口

```python
from landsat_grits.reader import list_tracks, list_scenes, open_scene, open_track

path = "outputs/pilots/shenzhen-20261021/49QHE/oli-l2/30m.zarr"
print(list_tracks(path))
scenes = list_scenes(path, track="WRS2/P122", wrs_row=44)

# 从索引中明确选一景；示例按列表顺序选取第一条已提交记录。
scene_id = scenes[0]["scene_id"]
with open_scene(path, scene_id=scene_id, track="WRS2/P122",
                window=(100, 100, 256, 256)) as ds:
    red = ds.data.sel(bands="red").where(ds.valid.sel(bands="red")).compute()
    print(ds.scene_id.values, ds.track.values, ds.wrs_row.values)

# 同轨时间序列仍保留各景、各 Row；不把相同日期压成一张影像。
with open_track(path, "WRS2/P122", wrs_row=44) as series:
    print(series.sizes, series.time.values)
```

- `open_scene` 必须唯一命中一景；多个轨道或同轨多日期同时命中均报错，需指定完整 `scene_id`。
- `scene_id` 与 `track` 同时提供时，两者必须相符。
- `open_track` 要求明确的 WRS 系统与 Path；需要固定沿轨景框时再指定 `wrs_row`。
- `window=(row_off,col_off,height,width)` 按所选组的原生格网解释。读取结果保持 `(time,bands,x,y)`，单景保留长度为 1 的 `time`；窗口坐标与 transform 相应更新。
- 原始 DN、逐波段 `valid/present/scale/offset` 均保留。`valid` 不是无云掩膜；不会用另一景填补当前景的无效部分。
- 未知轨道的坐标为 `track=""`、WRS 数字字段为 `-1`。可用精确 `scene_id` 读取，但不能命中某个已知轨道筛选。
- xarray 返回惰性数组；读取和计算期间应停止对同一库写入。

公共六波段 L2 接口也保留轨道坐标，并默认拒绝已知的混合轨道：

```python
from landsat_grits.reader import open_common_reflectance

with open_common_reflectance([path], track="WRS2/P122") as sr:
    patch = sr.reflectance.isel(x=slice(0, 64), y=slice(0, 64)).compute()
```

需要研究不同轨道时，可明确设置 `allow_mixed_tracks=True`。这只允许各景分别进入时间轴，不会拼接或平均像元。缺轨道元数据的旧景会记录 `unknown_track_scenes`；需要保证单轨道时应始终传入 `track`。

Sentinel-2 使用其仓库的 `list_tracks/list_scenes/read_scene`，保持原生 `(time,y,x)` 变量和 10/20/60 m 格网，详见 [S2 单景读取说明](../../S2-GRiTS/docs/SINGLE_SCENE_READING_ZH.md)。两种存储布局分别保留，不为统一接口而重写原始归档。

S2 云概率的零值可能表示 0% 云概率。读取层使用同景 SCL 的有效覆盖与 `0–100` 概率范围判断有效性，保留原始 DN，并返回掩膜来源；没有同景 SCL 时明确标注保守掩膜的歧义。有效覆盖与无云筛选是两个步骤。

## GUI

选择顺序为 **格网 → 传感器／级别 → 轨道 → 场景 → 变量组与波段**。场景菜单只列出所选轨道，显示完整时间、产品 ID 与 Row。初次打开明确选择最早的一景。

对照和图集按“归档 × 轨道”分别展示。当前轨道保持用户所选景；其他轨道选取最接近该日期的一景并显示各自身份，不表示同日配对。因此归档变量数和图卡数可能不同：一个 19 波段产品有两条轨道时可形成 38 张图卡。

## 深圳大学测试区域

采用粤海校区，WGS84 代表点为 **113.9314749, 22.5359023**，标准 100 km MGRS 编号为 **49QHE**。点来自 [OpenStreetMap 校区要素](https://www.openstreetmap.org/relation/13900447)，用于定位，不是建筑测绘坐标；来源与格网表哈希保存在 [campus-location.json](evidence/shenzhen-20261021/campus-location.json)。

S1-GRiTS 的扩展 extent 在边缘重叠，49QGE、49QGF、49QHE、49QHF 都包含该点；不能用第一个 `intersects` 结果代替标准瓦片归属。49QHE 原生格网下，校区接近西北边缘。Landsat 整格重投影范围与 Sentinel-2 原生格网的像元尺寸／原点不保证完全一致；跨传感器读取需检查实际坐标，不能直接按行列相加。

## 跨 UTM 重投影精度

深圳 MSS 源影像为 UTM 50N，目标为 UTM 49N。源样本核对发现，GDAL 默认 0.125 个源像元的变换近似会使接近边界的最近邻样本选择相邻像元。新建 Landsat 归档改用 **`warp_tolerance=1e-9` 个源像元**，同时写入存储合同和逐波段来源记录。当前 Rasterio/GDAL 的 VRT 路径不接受零容限，因此使用很小的正数。

旧库缺少该合同字段时继续使用原来的 0.125 策略，保持其合同与续传一致，不在旧库中悄悄切换处理方式。需要新精度时新建库。初始失败数据及核对结果单独保留，修正后重新归档；诊断见 [重投影对照记录](evidence/shenzhen-20261021/reprojection-investigation.json)。
