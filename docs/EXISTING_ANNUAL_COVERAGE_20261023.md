# 停止下载后的现有数据年度覆盖

2026-10-23 用户要求停止后续下载。已确认无下载进程，保留所有已提交数据；未经新的恢复指令不再补景。
本次只读取本地归档，未下载影像、未修改源 Zarr，也未生成年度 Zarr。

**现有数据可以生成四类传感器各自年份的整格网镶嵌，但均不是严格 100% 无缺口。**
MSS、ETM+、OLI 已接近全覆盖；TM 还缺约 5%，主要缺口位于格网东南部，另有少量散布缺口。

| 传感器 | 年份 | 已有场景 | 实际采集日 / 月份 | 原生有效覆盖 | 缺测像元 |
|---|---:|---:|---:|---:|---:|
| MSS / Landsat 1 | 1973 | 4 | 2 / 2 | 99.86306% | 4,591 |
| TM / Landsat 5 | 2010 | 17 | 9 / 5 | 94.99975% | 670,180 |
| ETM+ / Landsat 7 | 2001 | 30 | 16 / 7 | 99.88324% | 15,649 |
| OLI / Landsat 9 | 2025 | 28 | 15 / 7 | 99.99814% | 249 |

MSS 使用已完成的 1973 年测试；1978 年样景及 1979 年全年对照仍保留，不混入 1973 年结果。
四类分别按各自卫星与年份合成，不跨年、不跨传感器融合；同年不同日期和轨道可在质量规则下互补。
缺口保持无效／透明，不用插值或放宽 QA 制造“完整”覆盖。

## 统计口径与检查

- 空间范围：49QHE 的既有 MGRS extent 原生格网。MSS 为 60 m、1,831×1,831，其余为 30 m、3,661×3,661。
- 检查全部参与显示的光学波段、有效性掩膜、`present`、`QA_PIXEL` 与 `QA_RADSAT`。这不是热红外或所有辅助变量的联合覆盖统计。
- 沿用 Resolver 的 MSS / TM-ETM+ / OLI 质量规则，分别使用 QA_PIXEL 掩码 9 / 59 / 63，并要求 QA_RADSAT 为 0。有效覆盖不等于人工核验的无云率。
- 全格网逐像元求可用观测并集。整数科学波段及定标参数经过有限性检查，覆盖统计只读取必要的有效性与 QA 数组，不重复渲染全部波段。
- 每类用 5 个 32×32 原生窗口与实际 Resolver 输出的 `valid` 核对，共 20,480 个位置一致；MSS 全格网计数也与先前完整渲染结果一致。
- 所有纳入的场景归档结构通过审计，场景身份无重复；计算前后源元数据指纹一致。未声称对所有数据块作前后哈希。

统计与掩膜见 [汇总 JSON](../../GRiTS-Resolver/docs/evidence/20261023-existing-coverage/summary.json)。
以下原生分辨率掩膜中，白色为有效，黑色为缺测：

- [MSS 1973](../../GRiTS-Resolver/docs/evidence/20261023-existing-coverage/mss-l1-valid-native.png)
- [TM 2010](../../GRiTS-Resolver/docs/evidence/20261023-existing-coverage/tm-l2-valid-native.png)
- [ETM+ 2001](../../GRiTS-Resolver/docs/evidence/20261023-existing-coverage/etm-l2-valid-native.png)
- [OLI 2025](../../GRiTS-Resolver/docs/evidence/20261023-existing-coverage/oli-l2-valid-native.png)

后续显示配置已固定在 `GRiTS-Resolver/examples/shenzhen_landsat_existing_years.json`。
年度只作为读取和显示组合，源数据继续逐景存储。影像覆盖接近完整，也不代表具有完整的全年时序或全年物候代表性。
