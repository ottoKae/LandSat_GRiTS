# 49QHE 四类 Landsat 月度显示验收

2026-10-21。入口：**http://127.0.0.1:8767**。用户确认使用深圳大学粤海校区所在的 **49QHE**；格网表中没有此前输入的 49QJE。

本测试按传感器家族与产品分别显示 MSS L1、TM L2、ETM+ L2、OLI L2。每类选择同一 UTC 自然月中的两天，共 14 景；复用 6 景、新增 8 景。保留所选日期内全部相交场景，不混合不同传感器，也不代表下载了各月所有影像。

## 覆盖结果

比例由整个原生格网统计，含海域；“有效”指通过当前波段有效性与 QA 规则，不等于经过人工确认的无云覆盖。

| 产品 | 月份 | 日期 | 场景 | 单日最佳 | 月度有效 | 增加百分点 |
|---|---|---|---:|---:|---:|---:|
| mss-l1 | 1979-09 | 12、30 日 | 2 | 24.96% | 26.72% | 1.76 |
| tm-l2 | 2010-12 | 16、23 日 | 4 | 34.11% | 34.13% | 0.02 |
| etm-l2 | 2001-11 | 20、29 日 | 4 | 30.05% | 53.85% | 23.80 |
| oli-l2 | 2025-12 | 08、17 日 | 4 | 93.29% | 99.97% | 6.67 |

MSS 的两景只覆盖部分格网，且 QA 后仍有明显云雾；TM 的 12 月 16 日仅有 3,523 个合格像元，月度补齐有限。ETM+ 的月度覆盖有明显提升。各产品均保留无合格观测处的透明空白。不同日期可能出现亮度或水色接缝，可用“来源日期”追溯，不做接缝匀色。

## 下载与存储

- 来源：Microsoft Planetary Computer 的公开 Landsat Collection 2 COG，MSS 使用 `landsat-c2-l1`，现代产品使用 `landsat-c2-l2`；沿用生产流水线按格网读取、重投影并写入 Zarr。
- 新增 MSS 1 景、TM 4 景、ETM+ 3 景，三份冻结清单全部满足，缺失场景为 0。MSS 每景保存 6 个产品变量，TM、ETM+ 每景保存 19 个产品变量，均无缺失变量。
- 新源归档位于 `outputs/pilots/monthly-sensors-20261021/49QHE/<product>/<resolution>m.zarr`，保留逐景 `(time,bands,x,y)`。旧批次只读复用。
- MGRS extent 为 109.8×109.8 km，EPSG:32649。向外对齐后 MSS 为 1831×1831 / 60 m，现代产品为 3661×3661 / 30 m。
- 月度结果仅为显示 PNG 与 `GRiTS-Resolver/outputs/viewer-cache/monthly-sensors-49QHE/` 内 NPZ 缓存；没有生成月度 Zarr。
- 候选目录、选景理由、冻结清单及下载回执见 [下载证据](evidence/monthly-sensors-20261021/)。TM 原候选月份只有一天，因此改用 2010-12；保留原候选记录。

## 月度显示规则

同一产品、卫星、格网及自然月内，沿用日合成的有效性与 QA 筛选。全部请求光学波段和 QA 共用一个来源；合格 Landsat 观测同分时按 UTC 时间、场景 ID 稳定选取。月份内可使用不同 Path，来源仍保留真实日期、Path、Row。

MSS 为原始 DN，显示近红外／红／绿假彩色与 P2–P99 线性拉伸；它没有蓝光。现代 L2 使用地表反射率，真彩色默认 0–0.30、γ=1，也可选择自适应拉伸。页面开放 4+6+6+7=23 个光学波段选项。月份与单位不同，颜色不能直接作为跨传感器定量变化证据。

## 验证

- 独立逐块重建四个月度显示，对全部 **43,561,324 个原生空间位置**、**268,065,743 个输出光学波段值**核对一致，同时核对 QA、来源、有效掩膜与候选数。
- 此次全像元核对针对光学月度显示及 QA；热红外与辅助变量完成归档结构、存在性和来源检查，不声称本次逐像元核对了全部 19 个变量。
- 真实 Chromium 桌面／手机验收通过：7 种模式、23 个波段、日/月切换、校园窗口、20 个原生像元探针、4 份 PNG 导出及移动布局；无 JavaScript 或请求错误。
- 显示验收前后 755 份源元数据、提交与来源记录 SHA-256 一致；检查未声称对所有源数据块做前后哈希。
- 查看器相关回归 47 项通过；Ruff、JavaScript 语法检查通过。wheel 构建成功，查看器模块及静态资源与源码字节一致。
- OLI 此次请求全部 7 个光学波段；共同有效覆盖比此前 4 波段显示少 11 个像元，属于请求波段集合改变后的有效性差异。

[浏览器结果](../../GRiTS-Resolver/docs/evidence/20261021-monthly-sensors/browser-acceptance.json) · [桌面截图](../../GRiTS-Resolver/docs/evidence/20261021-monthly-sensors/desktop-four-sensor-monthly.png) · [来源日期图](../../GRiTS-Resolver/docs/evidence/20261021-monthly-sensors/desktop-monthly-source_date.png) · [手机截图](../../GRiTS-Resolver/docs/evidence/20261021-monthly-sensors/mobile-four-sensor-monthly.png)

| 产品 | 全格网核对 | 月度图 |
|---|---|---|
| mss-l1 | [JSON](../../GRiTS-Resolver/docs/evidence/20261021-monthly-sensors/mss-l1-audit.json) | [PNG](../../GRiTS-Resolver/docs/evidence/20261021-monthly-sensors/mss-l1-month.png) |
| tm-l2 | [JSON](../../GRiTS-Resolver/docs/evidence/20261021-monthly-sensors/tm-l2-audit.json) | [PNG](../../GRiTS-Resolver/docs/evidence/20261021-monthly-sensors/tm-l2-month.png) |
| etm-l2 | [JSON](../../GRiTS-Resolver/docs/evidence/20261021-monthly-sensors/etm-l2-audit.json) | [PNG](../../GRiTS-Resolver/docs/evidence/20261021-monthly-sensors/etm-l2-month.png) |
| oli-l2 | [JSON](../../GRiTS-Resolver/docs/evidence/20261021-monthly-sensors/oli-l2-audit.json) | [PNG](../../GRiTS-Resolver/docs/evidence/20261021-monthly-sensors/oli-l2-month.png) |

## 场景贡献

贡献为月度选择后实际保留的原生像元数，零贡献场景仍保留在归档与来源表。现代产品每一天均为同 Path 的相邻 Row；不同 Path 出现在不同日期。本批没有宣称完成真实同日跨 Path 验证。

| 产品 | 场景 ID | 贡献像元 |
|---|---|---:|
| mss-l1 | LM03_L1TP_130044_19790912_02_T2 | 312,989 |
| mss-l1 | LM03_L1TP_130044_19790930_02_T2 | 582,909 |
| tm-l2 | LT05_L2SP_121044_20101216_02_T2 | 3,523 |
| tm-l2 | LT05_L2SP_121045_20101216_02_T2 | 0 |
| tm-l2 | LT05_L2SP_122044_20101223_02_T1 | 2,032,239 |
| tm-l2 | LT05_L2SP_122045_20101223_02_T1 | 2,538,576 |
| etm-l2 | LE07_L2SP_122044_20011120_02_T1 | 1,805,270 |
| etm-l2 | LE07_L2SP_122045_20011120_02_T1 | 1,969,099 |
| etm-l2 | LE07_L2SP_121044_20011129_02_T2 | 0 |
| etm-l2 | LE07_L2SP_121045_20011129_02_T2 | 3,443,017 |
| oli-l2 | LC09_L2SP_122044_20251208_02_T1 | 2,441,322 |
| oli-l2 | LC09_L2SP_122045_20251208_02_T1 | 1,874,973 |
| oli-l2 | LC09_L2SP_121044_20251217_02_T1 | 2,207,616 |
| oli-l2 | LC09_L2SP_121045_20251217_02_T1 | 6,874,657 |

边界：这是四个传感器家族的代表性选景测试，不是 Landsat 1–9 每颗卫星、所有产品和全时段验收。未实施跨传感器融合、月平均、云雾修复或缺口插值。
