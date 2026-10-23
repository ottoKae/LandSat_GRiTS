# 数据来源与产品语义

本仓库保存 Landsat Collection 2 的 MSS、TM、ETM+ 和 OLI/TIRS 产品。
按传感器族与产品级别分组，保留交付产品的测量波段、质量标志和原始 STAC／MTL。
RBV、历史试验热红外及未纳入 Collection 2 的档案不在范围内。

| 卫星与传感器 | 原生产品 | 来源 / collection | 测量资产 | 格网 |
|---|---|---|---|---|
| Landsat 1–5 MSS | `mss-l1` | PC / `landsat-c2-l1` | green、red、nir08、nir09 | 60 m |
| Landsat 4–5 TM | `tm-l1` | USGS / `landsat-c2l1` | 6 光学 + lwir(B6) | 30 m |
| Landsat 7 ETM+ | `etm-l1` | USGS / `landsat-c2l1` | 6 光学 + lwir(B6_VCID_1) + lwir_high(B6_VCID_2) + pan(B8) | pan 15 m；其它 30 m |
| Landsat 8–9 OLI/TIRS | `oli-l1` | USGS / `landsat-c2l1` | 8 光学含 cirrus(B9) + lwir11(B10) + lwir12(B11) + pan(B8) | pan 15 m；其它 30 m |

TM、ETM+ 的 L1 测量数据为 uint8；OLI/TIRS 为 uint16。各 L1 band 保存 DN、nodata=0、identity scale/offset；每景物理标定仍从原 MTL 解释。QA 为独立变量，其位宽不随光谱数据的 uint8 改变。[USGS Level-1 数字格式说明](https://www.usgs.gov/landsat-missions/using-usgs-landsat-level-1-data-product)

Planetary Computer（PC）提供以下公开在线产品：

| 卫星与传感器 | 当前产品 | PC collection | 光谱资产键 | 输出像元间距 |
|---|---|---|---|---|
| Landsat 1–3 MSS | `mss-l1` | `landsat-c2-l1` | `green, red, nir08, nir09` | 60 m |
| Landsat 4–5 MSS | `mss-l1` | `landsat-c2-l1` | `green, red, nir08, nir09` | 60 m |
| Landsat 4–5 TM | `tm-l2` | `landsat-c2-l2` | `blue, green, red, nir08, swir16, swir22` | 30 m |
| Landsat 7 ETM+ | `etm-l2` | `landsat-c2-l2` | `blue, green, red, nir08, swir16, swir22` | 30 m |
| Landsat 8–9 OLI/TIRS | `oli-l2` | `landsat-c2-l2` | `coastal, blue, green, red, nir08, swir16, swir22` | 30 m |

表中的输出间距来自交付产品；它不是所有传感器的原生空间分辨率。MSS 的交付像元间距为 60 m。温度虽然交付到 30 m 格网，也不代表原生热红外观测达到 30 m。当前可查询平台及资产定义见 [PC MSS Collection 元数据](https://planetarycomputer.microsoft.com/api/stac/v1/collections/landsat-c2-l1) 和 [PC Level-2 Collection 元数据](https://planetarycomputer.microsoft.com/api/stac/v1/collections/landsat-c2-l2)。

**代际与产品范围的边界**

- Landsat 6 发射后未进入轨道，没有遥感影像，不能补造一个空卫星产品来表示“支持 1–9”。[USGS Landsat 6](https://www.usgs.gov/landsat-missions/landsat-6)
- MSS 没有官方 Collection 2 Level-2 地表反射率产品。MSS Level-1 DN、TOA 辐亮度、TOA 反射率、现代 Level-2 地表反射率是不同物理量；统一波段名称和格网不会消除这些差异。[USGS Level-2 范围说明](https://www.usgs.gov/faqs/how-do-landsat-collection-2-level-2-products-compare-products-collection-1)
- Level-2 不包含 ETM+/OLI 的全色波段、OLI 的 cirrus 波段和 TIRS 原生 B11；也不代表所有只有 Level-1 的场景均有对应 Level-2。`tm-l1`、`etm-l1`、`oli-l1` 保留这些原生测量，不能用 L2 代替它们。[USGS Level-1 波段清单](https://www.usgs.gov/landsat-missions/landsat-collection-2-level-1-data)
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

**L1 标定**

L1 的恒等 scale/offset 仅表示保存 DN。MSS 的部分 MTL 标定系数可能为字符串
`"NULL"`；缺失系数不能按 1 处理或套用现代 L2 系数。若需转换为 TOA 反射率，应读取
有效的 `REFLECTANCE_MULT_BAND_x`、`REFLECTANCE_ADD_BAND_x`，并结合 `SUN_ELEVATION`。
[USGS Level-1 转换公式](https://www.usgs.gov/landsat-missions/using-usgs-landsat-level-1-data-product)

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

L2SR 场景只含反射率及对应 QA，没有温度或 ST 辅助变量时仍保留该景，缺失项标记
`present=false`。日期范围由配置与目录查询决定，不按旧产品介绍页硬编码结束年份。

**L1 单传感器场景**

Landsat 8–9 的 `LC08/09` 表示 OLI 与 TIRS 联合采集，`LO08/09` 为 OLI-only，
`LT08/09` 为 TIRS-only；`LT04/05` 仍代表 TM。
[USGS Collection 2 数据字典](https://www.usgs.gov/centers/eros/science/landsat-collection-2-data-dictionary)

| 采集模式 | 必需测量通道 | 未采集部分 |
|---|---:|---|
| OLI/TIRS 联合 | 11 | 无 |
| OLI-only | 9：8 光学 + pan | thermal |
| TIRS-only | 2：B10、B11 | optical、pan |

固定 `oli-l1` schema 保留全部 11 个通道。未采集项允许 `present=false`，
应采集却缺资产仍报错。分类读取 instruments 并与完整 Collection 2 产品 ID 核对；
信息矛盾时拒绝处理，缺 instruments 时只接受与 platform 一致的完整标准 ID。
TM、ETM+ 分别要求全部 7、9 个测量通道。

**访问方式**

- PC STAC：`https://planetarycomputer.microsoft.com/api/stac/v1`。
  MSS L1 和现代 L2 通过临时 SAS 签名读取公开 COG；签名在运行时获取，持久化来源只保存未签名 URL。
- USGS STAC：[landsatlook.usgs.gov/stac-server](https://landsatlook.usgs.gov/stac-server)，
  Level-1 collection 为 `landsat-c2l1`。免费目录查询与影像访问分开；
  本仓库支持导入已下载解包的 TIFF／MTL，或显式启用 AWS Requester Pays。
- `s3://usgs-landsat/collection02/` 的 Requester Pays 需要 AWS 凭证，费用由请求方承担。
  默认 `auto` 不启用该路线，凭证通过 AWS 标准配置提供，不能写入 YAML 或来源记录。

USGS HTTPS 资产可能重定向到登录页，HTTP 200 不保证返回的是影像或 JSON。
本仓库尚未实现 EarthExplorer／M2M 登录下载；免费获取现代 L1 后可按
[本地导入说明](QUICKSTART.md#完整-l1-波段)使用。
数据本身免费，云端请求及传输可能收费，见
[USGS 云访问说明](https://www.usgs.gov/landsat-missions/landsat-commercial-cloud-data-access)与
[AWS 数据登记](https://registry.opendata.aws/usgs-landsat/index.html)。
