# 深圳大学 49QHE：光学单轨道、单景验收

测试区域为深圳大学粤海校区所在的 **49QHE**，时间为 2026-10-21。本次验证同一瓦片中不同轨道的独立归档与读取，不执行跨轨道拼接或缺测填补。

**Landsat 4 个产品库、5 景与 Sentinel-2 两景均已通过真实像元验收。** 两种光学数据的单轨道、单景读取及校园缺测隔离检查通过；Landsat GUI 的 82 张变量预览与桌面、移动端交互也通过。

## 实现与设计

- 参考 [GRiTS-Resolver 的 S1 读取](../../GRiTS-Resolver/src/grits_resolver/s1.py)：先列出所有轨道，明确选择，再分别读取值和掩膜。共同时间和有效范围约束不等于空间拼接。
- Landsat 使用 WRS 系统与 Path 标识轨道，Row 保留为沿轨景框；通过 `list_tracks/list_scenes/open_scene/open_track` 选择。不同场景即使时间相同也分别保留。公共反射率视图默认拒绝已知混合轨道，显式允许时仍不合成像元。
- Sentinel-2 使用相对轨道、产品 ID 与 datatake 采集身份，通过 `list_tracks/list_scenes/read_scene` 读取唯一场景。产品时间或瓦片编号不能替代轨道身份。
- 保留现有存储：Landsat 为 `(time,bands,x,y)`；S2 为分辨率组中的各变量 `(time,y,x)`。轨道筛选在索引层完成，不复制整套像元。
- 轨道未知时逐景列出；身份冲突、未提交尾部或多景歧义会报错。读取期间停止对对应库写入。

接口示例和边界见 [光学轨道读取](OPTICAL_TRACKS.md)、[S2 单景读取说明](../../S2-GRiTS/docs/SINGLE_SCENE_READING_ZH.md)。

## 校区、格网与下载

校园代表点为 WGS84 **113.9314749°E，22.5359023°N**，来自 [OpenStreetMap 校区对象](https://www.openstreetmap.org/relation/13900447)，不是建筑测绘坐标。S1-GRiTS 扩展 extent 有重叠；不能将第一个相交格网当成标准 100 km MGRS 归属。位置、源表 SHA-256 和重叠分析见 [校区记录](evidence/shenzhen-20261021/campus-location.json)。

Landsat 使用 Planetary Computer 的公开 MSS L1 和现代 L2 COG，按窗口读取、在内存重投影后写 Zarr。S2 使用 Earth Search 的公开 `sentinel-2-c1-l2a` COG，本次保留全部源文件并校验 SHA-256，再按原生格网归档。未使用 AWS Requester Pays。

| 数据 | 完整数组大小 | 数据目录 |
|---|---|---|
| Landsat MSS L1 | 1831 × 1831，60 m | `outputs/pilots/shenzhen-20261021/49QHE/mss-l1/60m.zarr` |
| Landsat TM / ETM+ / OLI L2 | 3661 × 3661，30 m | 同一批次的 `tm-l2/etm-l2/oli-l2/30m.zarr` |
| S2 L2A | 10980² / 5490² / 1830²，10 / 20 / 60 m | `S2-GRiTS/outputs/pilots/49QHE-20261021/49QHE/scenes_L2A_native/zarr/` |

这些是整格网的数组尺寸，单景有效覆盖通常不足 100%。Landsat 的外扩对齐格网和 S2 的原生格网不能直接按同一行列对应。

## 真实场景

| 产品 | 日期 | 轨道／景框 | 首个光学波段整格有效比例 | 校园点 |
|---|---|---|---:|---|
| MSS L1，Landsat 3 | 1979-09-30 | WRS1 / P130 / R044 | 25.0970% | 有效 |
| TM L2，Landsat 5 | 2011-06-01 | WRS2 / P122 / R044 | 14.8053% | 有效 |
| ETM+ L2，Landsat 7 | 2001-11-20 | WRS2 / P122 / R044 | 13.4785% | 有效 |
| OLI/TIRS L2，Landsat 9 | 2025-12-08 | WRS2 / P122 / R044 | 18.2194% | 有效 |
| OLI/TIRS L2，Landsat 9 | 2025-12-17 | WRS2 / P121 / R045 | 95.8948% | 32 × 32 光学窗口全缺测 |
| S2B L2A | 2024-11-26 | R132 | 82.0211% | 32 × 32 窗口全缺测 |
| S2B L2A | 2024-11-29 | R032 | 55.0541% | 32 × 32 窗口全部有效 |

比例表示数据有效覆盖，不表示无云。各分辨率的 32 × 32 窗口地面面积不同。两组双轨道测试均证明：整格覆盖较高的一景可能不覆盖校园，读取层没有借另一景补值。

## 检查方法与发现

Landsat 对全部 `data/valid` 空间块解码、核对数据类型、波段、NoData、坐标和比例系数；另将目标像元中心投影回原 TIFF，做独立源值抽样。抽样不是全量远程像元比对。第二景追加后检查第一景解码哈希不变，重复执行要求跳过已完成场景；校园窗口通过公开读取接口再与原始数组逐值比对。

Landsat 的 **82 个场景波段平面、1,038,737,362 个变量像元、9,920 个 data/valid 块位置**完成解码。**6,898 个独立源样本全部一致，没有排除样本**；重复执行写入 0 景、跳过 5 景。测试批次目录共 **199,317,769 字节、4,562 个文件**，含归档、提交／来源记录及 6 份运行回执；该大小不是网络传输量。P122 校园点有效，P121 的 32 × 32 光学窗口全部无效；未指定场景的歧义选择和默认混合轨道公共视图均正确报错。显式单轨公共视图保留 TM、ETM+、OLI 三景，显式混合视图保留四景，均不合成。

完整结果：[Landsat 验收汇总](evidence/shenzhen-20261021/acceptance-summary.json)、[读取接口验收](evidence/shenzhen-20261021/reader-acceptance.json)、[五景冻结清单](evidence/shenzhen-20261021/all-scenes-manifest.json)。

深圳 MSS 的源为 UTM 50N、目标为 UTM 49N。测试发现默认 0.125 源像元变换近似使一个最近邻样本选择相邻源行。新库使用 `warp_tolerance=1e-9`，记录到合同和来源信息；旧库维持原策略，避免混用。MSS 修正后重新下载并通过独立样本检查。OLI 也新建精度合同，其 P122 的 19 对 `data/valid` 解码哈希与原始版本完全一致。初始 MSS/OLI 数据与记录保存在 `outputs/pilots/shenzhen-20261021-initial-warp/`，不作正式结果。

S2 的 **1,460,120,400 个变量像元**在 **5,872 个窗口**中与源 COG 逐值一致，全部 28 个源文件 SHA-256 通过；Zarr 为 **875,346,516 字节**，保留源 COG 为 **1,387,863,179 字节**。读取和重复提交前后 Zarr 内容摘要一致。

另外修复了 S2 云概率零值掩膜：源 COG 同时用 0 表示 0% 云概率与 NoData，读取时改用同景 SCL 覆盖和 `0–100` 范围，并声明 `validity_method`。完整核对两景 **60,280,200 个云概率掩膜像元**，R032 校园窗口保留全部 1024 个有效像元，R132 仍为零。没有改写原始 DN；初始保守掩膜统计单独保留。

## GUI 验收

使用 Chromium 检查桌面 1500 × 1050 和手机 390 × 844 页面：默认 49QHE、两个 OLI 轨道的精确场景切换、5 张独立对照图、82 张完整变量图、图集跳转、温度和 QA 图例、PNG 导出均通过。校园按钮将代表点居中，只缩放既有预览，不重新请求更高分辨率影像。无 JavaScript 错误、失败请求或外部请求，手机无横向溢出。

浏览前后逐文件比较 **4,556 个 Zarr、提交和来源文件**，共 199,309,294 字节，全部路径、大小和 SHA-256 相同。聚合摘要为 `caf3087047f02c515f3435cfe32e870c8b9ddbe34908279f8f5aa9ed28743217`。该集合不含 6 份批次运行回执，故与下载目录的文件数不同。

报告及截图：[浏览器验收](evidence/shenzhen-20261021/gui/browser-acceptance.json)、[多传感器／轨道对照](evidence/shenzhen-20261021/gui/desktop-comparison.png)、[P122 校园覆盖](evidence/shenzhen-20261021/gui/desktop-campus-p122.png)、[P121 校园缺测](evidence/shenzhen-20261021/gui/desktop-campus-p121.png)、[手机校园定位](evidence/shenzhen-20261021/gui/mobile-campus.png)。

```powershell
.venv\Scripts\landsat-grits.exe gui outputs\pilots\shenzhen-20261021
```

打开 http://127.0.0.1:8765 。该 GUI 展示 Landsat 归档；S2 双轨道数据通过 S2-GRiTS 的读取接口使用。

## 自动化检查

- Landsat：**263 passed**；Ruff、依赖锁和源码包／wheel 构建通过。
- S2：**227 passed，3 skipped**；跳过项均因 Windows 无符号链接权限。新模块 Ruff 通过。
- 已对 Landsat 轨道身份、场景消歧、窗口 transform、来源校验和新旧精度合同做独立只读复核，未发现阻塞问题。

原始结果：[Landsat 代码检查](evidence/shenzhen-20261021/code-checks.json)、[S2 代码检查](../../S2-GRiTS/docs/evidence/49QHE-20261021/code-checks.json)、[S2 像元验收](../../S2-GRiTS/docs/evidence/49QHE-20261021/acceptance-summary.json)。

本次覆盖四类 Landsat 产品和两个 S2 轨道的真实样本，不代表 Landsat 1–9 每颗卫星、全部历史或现代 L1 在线下载均已验收；没有做并发读写压力测试。GUI 的降采样和逐图拉伸用于空间查看，不作为跨景定量比较。
