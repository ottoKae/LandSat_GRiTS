本文记录 **2026-10-20（Asia/Shanghai）** 对 Microsoft Planetary Computer 与 USGS 官方数据源的核实结果。精简的首轮查询结果见 [source_probe.json](evidence/source_probe.json)；该文件保留首轮 PC/MSS 验证与一个 USGS L1 样例的历史范围，未改写成后续原生 L1 测试报告。它不是完整 STAC 响应，也不代表全球影像清单核验；后续 L1 与单传感器场景核实结果另记于本文。

根据用户选择，当前按传感器分组保存 **Landsat 1–5、7–9 的 MSS / TM / ETM+ / OLI-TIRS Collection 2 全部原生 Level-1 测量波段**，并保留已有 Level-2 产品作为独立选项。MSS 仍使用 Planetary Computer，现代 Level-1 新增 USGS `landsat-c2l1` 的产品 schema 与目录入口。ETM+ 两种热波段增益分别保存，OLI/TIRS 保留 pan、cirrus、B10 和 B11；按交付分辨率把 15 m pan 与 30 m 其它变量分开。这里的“全部原生波段”指相应 Collection 2 产品的测量波段，不包含未纳入该产品线的 RBV 等历史档案，也不代表已经下载或核验全球全部场景。USGS 实际像素读取仍取决于可用的数据访问方式，以下公开探测没有使用未知凭证或执行付费数据读取。

| 卫星与传感器 | 原生产品 | 来源 / collection | 测量资产 | 格网 |
|---|---|---|---|---|
| Landsat 1–5 MSS | `mss-l1` | PC / `landsat-c2-l1` | green、red、nir08、nir09 | 60 m |
| Landsat 4–5 TM | `tm-l1` | USGS / `landsat-c2l1` | 6 光学 + lwir(B6) | 30 m |
| Landsat 7 ETM+ | `etm-l1` | USGS / `landsat-c2l1` | 6 光学 + lwir(B6_VCID_1) + lwir_high(B6_VCID_2) + pan(B8) | pan 15 m；其它 30 m |
| Landsat 8–9 OLI/TIRS | `oli-l1` | USGS / `landsat-c2l1` | 8 光学含 cirrus(B9) + lwir11(B10) + lwir12(B11) + pan(B8) | pan 15 m；其它 30 m |

TM、ETM+ 的 L1 测量数据为 uint8；OLI/TIRS 为 uint16。各 L1 band 保存 DN、nodata=0、identity scale/offset；每景物理标定仍从原 MTL 解释。QA 为独立变量，其位宽不随光谱数据的 uint8 改变。[USGS Level-1 数字格式说明](https://www.usgs.gov/landsat-missions/using-usgs-landsat-level-1-data-product)

以下是仍然支持的 PC 产品：

| 卫星与传感器 | 当前产品 | PC collection | 光谱资产键 | 输出像元间距 |
|---|---|---|---|---|
| Landsat 1–3 MSS | `mss-l1` | `landsat-c2-l1` | `green, red, nir08, nir09` | 60 m |
| Landsat 4–5 MSS | `mss-l1` | `landsat-c2-l1` | `green, red, nir08, nir09` | 60 m |
| Landsat 4–5 TM | `tm-l2` | `landsat-c2-l2` | `blue, green, red, nir08, swir16, swir22` | 30 m |
| Landsat 7 ETM+ | `etm-l2` | `landsat-c2-l2` | `blue, green, red, nir08, swir16, swir22` | 30 m |
| Landsat 8–9 OLI/TIRS | `oli-l2` | `landsat-c2-l2` | `coastal, blue, green, red, nir08, swir16, swir22` | 30 m |

表中的输出间距来自交付产品；它不是所有传感器的原生空间分辨率。MSS 实测 STAC `gsd=79`、COG 仿射步长为 60 m。温度虽然交付到 30 m 格网，也不代表原生热红外观测达到 30 m。当前可查询平台及资产定义见 [PC MSS Collection 元数据](https://planetarycomputer.microsoft.com/api/stac/v1/collections/landsat-c2-l1) 和 [PC Level-2 Collection 元数据](https://planetarycomputer.microsoft.com/api/stac/v1/collections/landsat-c2-l2)。

**代际与产品范围的边界**

- Landsat 6 发射后未进入轨道，没有遥感影像，不能补造一个空卫星产品来表示“支持 1–9”。[USGS Landsat 6](https://www.usgs.gov/landsat-missions/landsat-6)
- MSS 没有官方 Collection 2 Level-2 地表反射率产品。MSS Level-1 DN、TOA 辐亮度、TOA 反射率、现代 Level-2 地表反射率是不同物理量；统一波段名称和格网不会消除这些差异。[USGS Level-2 范围说明](https://www.usgs.gov/faqs/how-do-landsat-collection-2-level-2-products-compare-products-collection-1)
- Level-2 不包含 ETM+/OLI 的全色波段、OLI 的 cirrus 波段和 TIRS 原生 B11；也不代表所有只有 Level-1 的场景均有对应 Level-2。新增 `tm-l1`、`etm-l1`、`oli-l1` 正是为保留这些原生测量，不能用 L2 代替它们。[USGS Level-1 波段清单](https://www.usgs.gov/landsat-missions/landsat-collection-2-level-1-data)
- 早期 RBV 不在上述 Collection 2 / PC 路线内。USGS 的 Landsat Legacy 档案明确说明：EarthExplorer 当前提供 Landsat 3 RBV 胶片扫描产品，不提供 Landsat 1–2 RBV 胶片产品；已扫描文件可下载，未扫描影像涉及按需扫描服务。它们不能直接按 MSS 标定或当作本仓库已经覆盖的 COG 数据。[USGS RBV Film Only 档案说明](https://www.usgs.gov/centers/eros/science/usgs-eros-archive-landsat-legacy-return-beam-vidicon-rbv-film-only)
- 当前 MSS 产品只有四个光谱资产，不覆盖 Landsat 3 历史试验热红外观测。其历史存在不等于 PC 集合存在相应资产；本实现不生成 MSS thermal 变量。[USGS 历史 Landsat 3 Band 8 报告](https://pubs.usgs.gov/of/1983/0123/report.pdf)

**波段编号与保存语义**

| 产品 | green | red | nir08 | nir09 | 其它 |
|---|---|---|---|---|---|
| Landsat 1–3 MSS | B4 | B5 | B6 | B7 | 无 blue / SWIR |
| Landsat 4–5 MSS | B1 | B2 | B3 | B4 | 无 blue / SWIR |
| Landsat 4–5 TM / Landsat 7 ETM+ L2 | SR_B2 | SR_B3 | SR_B4 | 无 | blue=SR_B1，swir16=SR_B5，swir22=SR_B7 |
| Landsat 8–9 OLI L2 | SR_B3 | SR_B4 | SR_B5 | 无 | coastal=SR_B1，blue=SR_B2，swir16=SR_B6，swir22=SR_B7 |

MSS 的 `nir08` 在 PC 元数据中的中心波长是 0.75 µm，`nir09` 是 0.95 µm；TM/ETM+/OLI 的 `nir08` 具有各自不同的光谱响应。相同 common name 只提供语义索引，不证明跨传感器已经完成光谱一致化。原生波段标识须随观测保存，MSS 通过 `source_band_for_item` 区分两套编号。

所有科学数组保留原始整数值；下表的 scale/offset 是解释字段。物理量转换必须先处理填充值，再按各波段的元数据执行。

| 类型 | source dtype | nodata | scale | offset | 物理单位 |
|---|---|---:|---:|---:|---|
| MSS Level-1 光谱 DN | uint8 | 0 | 1 | 0 | DN；不是反射率转换系数 |
| TM / ETM+ Level-1 测量 DN | uint8 | 0 | 1 | 0 | DN；逐景读取 MTL 标定 |
| OLI / TIRS Level-1 测量 DN | uint16 | 0 | 1 | 0 | DN；逐景读取 MTL 标定 |
| L2 地表反射率 | uint16 | 0 | 0.0000275 | -0.2 | 无量纲 |
| L2 地表温度 | uint16 | 0 | 0.00341802 | 149 | K |
| ST_QA 温度不确定度 | int16 | -9999 | 0.01 | 0 | K |
| ST_TRAD / ST_URAD / ST_DRAD | int16 | -9999 | 0.001 | 0 | W m⁻² sr⁻¹ µm⁻¹ |
| ST_ATRAN / ST_EMIS / ST_EMSD | int16 | -9999 | 0.0001 | 0 | 无量纲 |
| ST_CDIST | int16 | -9999 | 0.01 | 0 | km |
| SR_ATMOS_OPACITY（TM/ETM+） | int16 | -9999 | 0.001 | 0 | 无量纲 |

这些 dtype 与填充值也在 [USGS TM Level-2 格式规范](https://d9-wret.s3.us-west-2.amazonaws.com/assets/palladium/production/s3fs-public/atoms/files/LSDS-1336_Landsat4-5-TM-C2-L2-DFCB-v4.pdf) 中定义。辅助变量的 `-9999` 必须保留，不能直接强转 uint16。

**实际遇到的标定缺失**

对 `LM01_L1GS_043044_19780107_02_T2`，PC 的 `green` 资产没有 `raster:bands.scale/offset`；读取其签名后的 MTL JSON，B4 的 `RADIANCE_MULT/ADD` 与 `REFLECTANCE_MULT/ADD` 均为字符串 `"NULL"`。对 `LM05_L1GS_039039_20130107_02_T2`，`nir09` 对应的 B4 也有同样情况。

因此，“STAC 未写 scale 就默认 1”不能产生 TOA 反射率；即使读到 scale，MSS STAC 当前提供的 scale/offset 对应的是辐亮度。当前 `mss-l1` 的 identity scale 仅表示保存 DN。后续若加入物理转换，应读取原始 MTL 中有效的 `REFLECTANCE_MULT_BAND_x`、`REFLECTANCE_ADD_BAND_x`，结合 `SUN_ELEVATION`，并逐波段处理 `"NULL"`；不可套用现代 L2 的全局反射率系数。[USGS Level-1 转换公式](https://www.usgs.gov/landsat-missions/using-usgs-landsat-level-1-data-product)

**温度、辅助资产与 QA**

| 资产类别 | PC 资产键 | 适用产品 |
|---|---|---|
| 温度 | `lwir` → ST_B6 | TM / ETM+ L2 |
| 温度 | `lwir11` → ST_B10 | OLI/TIRS L2 |
| 连续 ST 辅助 | `qa, trad, urad, drad, atran, emis, emsd, cdist` | 有温度处理结果的 L2 |
| 大气光学厚度辅助 | `atmos_opacity` | TM / ETM+ L2 |
| 像元 / 饱和度位标志 | `qa_pixel, qa_radsat` | 所有当前产品 |
| SR 云位标志 | `cloud_qa` | TM / ETM+ L2 |
| SR 气溶胶位标志 | `qa_aerosol` | OLI/TIRS L2 |

`qa` 是 **ST_QA 连续温度不确定度**，不能因为资产键含 qa 就和位编码数组一起解释。`qa_radsat=0` 和 `cloud_qa=0` 都有实际含义，不能当作缺测。QA 的位定义还与传感器有关，MSS 不具有 OLI 的完整云影、积雪与 cirrus 检测语义。[USGS QA 说明](https://www.usgs.gov/landsat-missions/landsat-collection-2-quality-assessment-bands)

实测 `LC09_L2SR_086110_20261011_02_T2` 只有 SR 和对应 QA，没有 `lwir11` 或 ST 中间资产。这仍是有效光学场景，不能因缺温度而被整景丢弃；缺失资产必须以 presence 元数据记录。另实测 `LE07_L2SP_135047_20240119_02_T1` 可查询并有温度及 ST 辅助资产；不能依据旧介绍页把 Landsat 7 查询上限硬编码到 2022 年。

**原生 L1 的实测资产与单传感器场景**

本次免费 STAC 查询核实了 `LT05_L1TP_045033_20001231_20200906_02_T1` 的 7 个 TM 测量资产，以及 `LE07_L1TP_135047_20240119_20240213_02_T1` 的 9 个 ETM+ 测量资产。后者的 instruments 实际写为 `["ETM"]`，`lwir` 对应 `B6_VCID_1`，`lwir_high` 对应 `B6_VCID_2`，两个增益不能合并。USGS 样例没有 `raster:bands`，因此 dtype 依据官方产品文档，不能说已从公开 COG 像素读取确认。

USGS `eo:bands.gsd` 对热探测器分别标为 TM=120 m、ETM+=60 m、TIRS=100 m；官方说明这些热测量在 Level-1 产品处理时重采样至 30 m。`group_resolutions` 描述交付格网，不能用它宣称热探测器具有 30 m 原生观测能力。[USGS 传感器波段及交付分辨率](https://www.usgs.gov/faqs/what-are-band-designations-landsat-satellites)

Landsat 8–9 并非每个 L1 场景同时包含 OLI 与 TIRS。USGS Collection 2 数据字典定义 `LC08/09` 为联合采集，`LO08/09` 为 OLI-only，`LT08/09` 为 TIRS-only；其中 `LT04/05` 仍代表 TM。本次通过 `query: {"landsat:scene_id": {"startsWith": "LO8"}}` 等前缀查询获得以下实际结果。[USGS Collection 2 命名与传感器定义](https://www.usgs.gov/centers/eros/science/landsat-collection-2-data-dictionary)

| 实际 USGS item ID | instruments | 实际测量资产 |
|---|---|---|
| `LO08_L1GT_041038_20230423_20230425_02_T2` | `["OLI"]` | 8 光学 + pan；无热 |
| `LO09_L1GT_055118_20230228_20230307_02_T2` | `["OLI"]` | 8 光学 + pan；无热 |
| `LT08_L1GT_137208_20261009_20261015_02_T2` | `["TIRS"]` | lwir11、lwir12；无光学与 pan |
| `LT09_L1GT_137208_20261017_20261017_02_T2` | `["TIRS"]` | lwir11、lwir12；无光学与 pan |

这些 item 可从 USGS `collections/landsat-c2l1/items/{item_id}` 端点核查。固定 `oli-l1` schema 仍有 11 个测量波段，`required_bands_for_item` 根据实际采集模式返回本景必需波段：联合采集为 11，OLI-only 为 9，TIRS-only 为 2；TM/ETM+ 原生产品要求全部 7/9 个测量波段。没有采集的变量允许 `present=false`；已采集却缺资产仍报错，不能把文件遗漏解释成合法的单传感器采集。

分类时优先读取 instruments，并与完整 Collection 2 产品 ID 交叉核验；两者矛盾时拒绝处理。缺 instruments 时，只允许通过完整且与 platform 一致的标准 C2 ID 判定，不能从 `LT09` 之类残缺前缀或现有资产数量猜测。MSS 与 L2 维持原光学必需、L2 温度和辅助项可选的规则。

**数据访问验证与后续扩展**

PC STAC API 为 `https://planetarycomputer.microsoft.com/api/stac/v1`。本次直接查询两个 collection，按 platform 抽样，随后经 PC SAS 签名服务成功读取两个 MSS MTL JSON。证据文件只保存未签名的来源地址及必要元数据，不包含临时 SAS 参数。签名有有效期，运行时必须重新获得可用地址；本次验证不证明全部历史对象长期可读。

USGS 官方 STAC 为 [landsatlook.usgs.gov/stac-server](https://landsatlook.usgs.gov/stac-server)。实测 Level-1 collection 为 `landsat-c2l1`，其 platform 值形如 `LANDSAT_9`，MTL 资产键为 `MTL.json`，与 PC 的命名存在差异。抽样 `LC09_L1GT_030202_20261019_20261019_02_T2` 包含 `pan, cirrus, lwir11, lwir12`；其中 `lwir12` 对应 TIRS B11，不能与 PC Level-2 的 ST_B10 混为同一温度产品。

对该 USGS MTL 的 HTTPS 地址执行 HEAD 后，最终状态虽为 200，内容类型却为 `text/html; charset=UTF-8`，最终跳到 `ers.cr.usgs.gov/login`。下载器必须验证最终内容和认证状态；HTTP 200 本身不表示已取得 JSON 或影像。另一条官方通路是 `s3://usgs-landsat/collection02/`，USGS 与 AWS 均将其标记为 **requester pays**，需要相应 AWS 认证与费用安排。新增 L1 schema 和查询能力不改变这一访问条件；本文记录的 USGS 核验仅涉及免费元数据，尚不构成真实原生 L1 像素下载成功的证据。运行所需的明确访问配置与验证状态以仓库 README、VALIDATION 为准。[USGS 云访问说明](https://www.usgs.gov/landsat-missions/landsat-commercial-cloud-data-access)、[AWS 官方数据登记](https://registry.opendata.aws/usgs-landsat/index.html)

参考仓库 `https://github.com/lzxcas51651/Landsat_GRITS` 在本次探测中无法读取：`main/README.md` 和 `master/README.md` 的 raw 地址均返回 404，GitHub API 返回 403。该结果不能单独证明仓库不存在；本实现没有据此声称复用其源码或核实其下载源。
