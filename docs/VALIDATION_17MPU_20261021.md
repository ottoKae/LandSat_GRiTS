# 17MPU：每类传感器一景的完整格网验收

日期：2026-10-21（Asia/Shanghai）。**四类产品各一景，完整格网下载、归档与验收全部通过。** 汇总见[验收结果 JSON](evidence/17MPU-20261021/acceptance-summary.json)。

## 范围

按用户确认，使用免费的 Planetary Computer 路线：MSS L1、TM L2、ETM+ L2、OLI/TIRS L2 各一景。
每景读取完整 `17MPU` 目标格网所需的全部产品波段，按窗口重投影后直接写 Zarr，不生成中间 TIFF。
这是一组真实像元验收，不是完整历史清单，也不是现代 L1 全原生波段的在线验收。

格网来自 `S1-GRiTS/src/s1grits/data/mgrs.parquet`，SHA-256 为
`5c1ebd1f234ae74d9c249d91cb9c269eb6defa37a901da0c7bbcb7e50523fc6b`。
输出 CRS 为 `EPSG:32717`；MSS 为 60 m、`1831 × 1831`，现代 L2 为 30 m、`3661 × 3661`。
验收重新从原始 GeoParquet 生成格网并逐字段核对，确保没有把小窗口测试当作完整 tile。

## 场景选择

| 传感器／产品 | 卫星 | 日期 | 场景 | 标称足迹覆盖目标格网 |
|---|---|---|---|---:|
| MSS／L1 | Landsat 2 | 1978-11-07 | `LM02_L1GS_011061_19781107_02_T2` | 89.01% |
| TM／L2 | Landsat 5 | 2000-05-16 | `LT05_L2SP_010061_20000516_02_T1` | 61.56% |
| ETM+／L2 | Landsat 7 | 2000-09-20 | `LE07_L2SP_011061_20000920_02_T1` | 63.12% |
| OLI/TIRS／L2 | Landsat 8 | 2024-09-30 | `LC08_L2SP_011061_20240930_02_T1` | 65.94% |

各传感器分别检索，最多返回 64 个候选；要求光谱、温度、辅助和 QA 资产齐全，先按足迹覆盖率、再按场景云量排序。
ETM+ 选择 SLC 故障前的时期。候选清单和最终单景清单均保留在 [证据目录](evidence/17MPU-20261021)。
这些候选并非全部历史，更不代表全历史中云量最低或覆盖最好的影像。

**完整格网不等于单景覆盖整个格网，也不等于无云。** 17MPU 跨越 Landsat 场景边界，景外像元保留为无效值；获得连续覆盖需要增加相邻景。
MSS 所选产品为 L1GS、Tier 2；此次验证归档与读写正确性，不证明跨年代精确配准质量。

## 数据位置与组织

```text
outputs/pilots/17MPU-20261021/17MPU/
├── mss-l1/60m.zarr/    # optical、qa，共 6 个变量通道
├── tm-l2/30m.zarr/     # optical、thermal、ancillary、qa，共 19 个变量通道
├── etm-l2/30m.zarr/    # optical、thermal、ancillary、qa，共 19 个变量通道
└── oli-l2/30m.zarr/    # optical、thermal、ancillary、qa，共 19 个变量通道
```

每库只有一个时间项，维度为 `(time,bands,x,y)`；Zarr v2、Blosc/Zstd 5，默认块为 `(1,1,512,512)`。
每库旁的提交记录与来源目录需一并保留。这里的通道总数包含辅助变量和 QA，不等同于传感器光谱波段数。
四景 `data + valid` 的未压缩逻辑大小共 `2,338,835,345` 字节，像元块位置上限为 `7,488`；这两项均不是网络传输量或压缩后磁盘占用。
实际四个产品目录（含提交与来源记录）共 `334,484,361` 字节，约 **319 MiB**、`5,748` 个文件；不含目录外的清单、回执和验收报告。未计量网络传输字节数。

## 验收方式

1. 使用生产 `run` 写入完整格网，并通过生产 `verify` 检查结构、场景身份与来源记录。
2. 遍历全部本地 `data` 和 `valid` 块，检查尺寸、类型、缺测填充值，计算逐波段内容哈希和有效覆盖率。
3. 在中心、边缘和分布于不同区域的有效像元位置，独立反算源像元，读取原始 TIFF 数值与掩膜进行比较。
4. 检查 xarray 维度、坐标、时间、比例系数和原始波段编号。
5. 再次执行同一清单，要求新增为 0、已提交场景被跳过、提交记录不变；扫描持久化元数据中的访问凭据。

全部本地块均解码；与远端源数据的比较采用空间抽样，不宣称所有像元都与源数据逐一比较。
影像有效覆盖以光谱测量波段为准，不能用 QA_RADSAT 的有效率代替。MSS 的 QA_RADSAT 在源矩形内以合法 0 值覆盖整个格网，而光谱有效率约 89.01%。

## 逐景结果

| 产品 | 完整块解码 | 独立源样本 | 光谱有效覆盖 | 温度有效覆盖 | 重复运行 |
|---|---:|---:|---:|---:|---|
| MSS L1 | 20,115,366 个变量像元 | 726，全通过 | 89.01% | 无温度组 | 跳过 1 景 |
| TM L2 | 254,655,499 个变量像元 | 2,077，全通过 | 61.45% | 45.08% | 跳过 1 景 |
| ETM+ L2 | 254,655,499 个变量像元 | 2,104，全通过 | 63.10% | 28.62% | 跳过 1 景 |
| OLI/TIRS L2 | 254,655,499 个变量像元 | 2,053，全通过 | 65.94% | 31.26% | 跳过 1 景 |

像元数包含光谱、温度、辅助和 QA 各数组的全部位置及对应 `valid` 掩膜，包括缺测位置。
有效覆盖不等于无云覆盖；L2 温度源产品的有效范围可以小于光谱范围，归档保留源缺测，不填补数值。
详细记录：[MSS](evidence/17MPU-20261021/mss-l1-full-verify.json)、[TM](evidence/17MPU-20261021/tm-l2-full-verify.json)、[ETM+](evidence/17MPU-20261021/etm-l2-full-verify.json)、[OLI/TIRS](evidence/17MPU-20261021/oli-l2-full-verify.json)。

合计 **63 个变量通道、784,081,863 个变量像元及对应掩膜**完成解码，覆盖 `7,488` 个 data/valid 块位置；**6,960 个独立源样本全部一致，零排除**。
四次复跑均为新增 0、跳过 1；扫描 440 个归档元数据文件，未发现访问凭据。

公共六波段反射率接口也通过真实数据检查：TM、ETM+、OLI 组成 `(3,6,3661,3661)` 的惰性读取视图，时间顺序正确。
中心和两个角落的三个 `64 × 64` 窗口共比较 `221,184` 个值，包含有效值与缺测值；掩膜及 `DN × 0.0000275 - 0.2` 缩放一致。
没有生成完整浮点副本，没有混入 MSS，也没有执行去云或光谱一致化。[读取记录](evidence/17MPU-20261021/common-reflectance-read.json)。

## 代码审查与测试

检查了全部生产源码、入口、配置、依赖和关键测试，未发现新的可复现生产缺陷。
本轮未改变生产存储格式或写入代码，新增了选景和完整格网验收辅助。

| 检查 | 结果 | 证据 |
|---|---|---|
| 现有全套测试 | 217 passed；12 条 Shapely 弃用警告 | [测试日志](evidence/17MPU-20261021-tests.txt) |
| 新增验收工具测试 | 7 passed；18 条 Shapely 弃用警告 | [辅助测试日志](evidence/17MPU-20261021-helper-tests.txt) |
| Ruff：src、tests、tooling | 通过 | 本轮命令检查 |
| `uv lock --check` | 通过 | 本轮命令检查 |
| wheel 与 sdist 构建 | 通过 | `uv build --quiet` |

真实 MSS 验收曾暴露诊断脚本的问题：源使用北半球 UTM 编码南纬负 northing，目标使用南半球 UTM；像元中心转换后恰好位于源像元边界。
初版脚本排除了全部这类点，导致无法完成比较。已按 GDAL 的最近邻包含像元规则修正，并增加跨半球、整数边界和错误邻居检出测试。
修正后 MSS 的 726 个源样本全部比较成功，零排除；生产像元没有变化。[初次失败记录](evidence/17MPU-20261021/mss-l1-full-verify-initial.json)、[修正后验收](evidence/17MPU-20261021/mss-l1-full-verify.json)。
当前生产源码、辅助脚本、配置、文档和验收记录的 SHA-256 见[文件校验清单](evidence/17MPU-20261021/acceptance-sha256.json)；逐波段像元内容哈希保存在各景验收报告中。

## 复现

选景脚本不会覆盖已有的冻结清单；重跑下载命令可检查场景级续传。

```powershell
.venv\Scripts\python.exe tooling\diagnostics\20261021_17mpu\prepare_pilot.py mss-l1 tm-l2 etm-l2 oli-l2

# 下列以 MSS 为例，其他产品替换 mss-l1 即可
.venv\Scripts\landsat-grits.exe run docs\evidence\17MPU-20261021\mss-l1-manifest.json
.venv\Scripts\python.exe tooling\diagnostics\20261021_17mpu\verify_full_tile.py docs\evidence\17MPU-20261021\mss-l1-manifest.json --report docs\evidence\17MPU-20261021\mss-l1-full-verify.json --check-resume
```

## 未验证范围

本轮未使用 AWS 计费访问，未执行现代 L1 的真实全色、卷云、ETM+ 双增益或 TIRS B11 下载；不覆盖全球／全历史生产、断电恢复或损坏磁盘。
L2 的公共反射率视图不包括 MSS，不做跨传感器光谱一致化。产品区别和设计见 [README](../README.md#l1-与-l2-的区别)及[存储设计](DESIGN.md)。
