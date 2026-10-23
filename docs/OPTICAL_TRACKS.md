# 光学数据：按轨道、按景读取

**MGRS 决定空间格网，轨道决定观测几何，场景决定一次产品观测。三者分别保存。** 同一个瓦片可以被不同轨道部分覆盖，不能为了填满瓦片而自动拼接不同轨道、日期或产品的像元。

**同一 MGRS 瓦片内，同日多景合成**由
`grits_resolver.OpticalDailyResolver.read_day/read_days` 实现：同日跨轨可以参与，
逐像元质量选景，全部请求波段取自同一来源，并返回 `source_index` 与完整来源表。
原始单景接口保持上述身份约束。详见 [Resolver 配套说明](RESOLVER.md)。

## 身份与组织

| 对象 | Landsat |
|---|---|
| 空间瓦片 | 固定 MGRS extent，经源表外扩对齐 |
| 重复地面轨道 | `WRS1/P130`、`WRS2/P122` 等 |
| 沿轨场景框 | WRS Row，例如 044；Row 不等于另一条轨道 |
| 精确产品身份 | `collection/scene_id` 与 UTC；日期不能代替产品身份 |

Landsat 1–3 使用 WRS-1，4–9 使用 WRS-2；MSS 跨越两套系统，相同 Path 数字不能直接合并。Path 表示轨迹，Row 表示沿轨的景中心位置。依据见 [USGS WRS 说明](https://www.usgs.gov/landsat-missions/landsat-shapefiles-and-kml-files)和 [NASA WRS 定义](https://science.nasa.gov/mission/landsat/worldwide-reference-system/)。

Landsat 保持原有 `tile/product/*.zarr/group` 和 `(time,bands,x,y)`，不按轨道复制像元。轨道索引从提交记录对应的原始 STAC provenance 读取，校验场景身份、时间及来源指纹。缺轨道元数据时明确标为未知；未知景分别列出。已引用的来源文件缺失、被修改或存在身份冲突时拒绝读取。

## Landsat 读取接口

```python
from landsat_grits.reader import list_tracks, list_scenes, open_scene, open_track

path = "outputs/shenzhen-l2/49QHE/oli-l2/30m.zarr"
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

## GUI

选择顺序为 **格网 → 传感器／级别 → 轨道 → 场景 → 变量组与波段**。场景菜单只列出所选轨道，显示完整时间、产品 ID 与 Row。初次打开明确选择最早的一景。

对照和图集按“归档 × 轨道”分别展示。当前轨道保持用户所选景；其他轨道选取最接近该日期的一景并显示各自身份，不表示同日配对。

## 跨 UTM 重投影精度

跨 UTM 的坐标变换近似可能改变接近边界的最近邻样本选择。新建 Landsat 归档使用 **`warp_tolerance=1e-9` 个源像元**，同时写入存储合同和逐波段来源记录。当前 Rasterio/GDAL 的 VRT 路径不接受零容限，因此使用很小的正数。

旧库缺少该合同字段时继续使用原来的 0.125 策略，保持其合同与续传一致。需要新容限时新建库；该参数不代表影像定位精度或传感器分辨率。
