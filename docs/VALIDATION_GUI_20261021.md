# 本地 GUI 验收记录

日期：2026-10-21。数据根目录：仓库的 `outputs/`。本次使用真实已下载归档验证目录、制图、全部变量浏览和只读性。

**结果：6 个归档、6 景、2 个格网、88 个变量均可浏览。** 其中 `17MPU` 的 4 个整格网归档覆盖 63 个变量；此前 `50RKV` 的 2 个窗口归档覆盖其余 25 个变量。窗口数据在页面中明确标为“窗口数据”。

| 格网 | 产品 | 栅格尺寸（x × y） | 变量数 | 数据范围 |
|---|---|---:|---:|---|
| 17MPU | MSS L1 | 1831 × 1831 | 6 | 完整格网 |
| 17MPU | TM L2 | 3661 × 3661 | 19 | 完整格网 |
| 17MPU | ETM+ L2 | 3661 × 3661 | 19 | 完整格网 |
| 17MPU | OLI/TIRS L2 | 3661 × 3661 | 19 | 完整格网 |
| 50RKV | MSS L1 | 64 × 48 | 6 | 格网内窗口 |
| 50RKV | OLI/TIRS L2 | 64 × 48 | 19 | 格网内窗口 |

完整格网指固定栅格范围完整写入，并不表示所有像元均有效。原始下载验收见 [17MPU 验收记录](VALIDATION_17MPU_20261021.md)。

## 检查结果

- **目录与地理定位**：独立读取归档元数据建立基准，核对产品、景号、卫星、波段顺序、源波段编号、单位、`present`、分组尺寸和地理边界。窗口范围由实际栅格变换计算，未将原始 MGRS 多边形误用为窗口影像范围。
- **全部变量**：通过 HTTP 获取 88 个单波段预览，再由 GDAL 独立解码 PNG，检查图像尺寸、有效透明度、非空像元、单位、统计和显示范围。额外通过 14 个 RGB 预设与 6 个有效性掩膜预览。
- **并发读取**：3 次同时请求目录与 1024 像素预览，确认目录仍包含 6 个归档，后续请求继续识别原归档 ID。
- **真实浏览器**：Chromium 151.0.7922.34，桌面 1500 × 1050、移动端 390 × 844。88 张变量缩略图均完成浏览器解码，变量标识集合与目录完全一致；`17MPU` 筛选返回 63 项。验证 4 传感器对照、全部 6 归档对照、`ST_QA` 搜索和点击、温度单位、QA 离散图例、有效性掩膜、地图缩放与复位。
- **导出与离线**：浏览器下载的 PNG 与当前显示预览字节完全一致。浏览期间无外部网络请求、无请求失败、无控制台错误或页面异常。
- **只读性**：前后逐文件 SHA256 比较全部 **6046 个归档文件、提交账本与来源记录**，无新增、缺失或内容变化。运行锁文件不计入归档内容。

核心回归测试为 226 passed，GUI 后端测试为 20 passed，分别运行。完整结果以各测试报告和本次仓库检查输出为准。

## 截图

![格网地图](evidence/gui-20261021/desktop-map.png)

![多传感器对照](evidence/gui-20261021/desktop-comparison.png)

其他截图：[变量图集](evidence/gui-20261021/desktop-gallery.png)、[温度](evidence/gui-20261021/desktop-temperature.png)、[有效性掩膜](evidence/gui-20261021/desktop-validity.png)、[MSS 假彩色](evidence/gui-20261021/desktop-mss.png)、[ST_QA 搜索](evidence/gui-20261021/desktop-st-qa.png)、[窗口数据](evidence/gui-20261021/desktop-window.png)、[移动端](evidence/gui-20261021/mobile-map.png)。

## 证据与复现

| 证据 | 内容 |
|---|---|
| [expected-inventory.json](evidence/gui-20261021/expected-inventory.json) | 从归档独立建立的 6 归档、88 变量基准 |
| [catalog.json](evidence/gui-20261021/catalog.json) | HTTP 服务返回的实际目录 |
| [api-acceptance.json](evidence/gui-20261021/api-acceptance.json) | 每个变量的预览、解码统计及并发检查 |
| [browser-acceptance.json](evidence/gui-20261021/browser-acceptance.json) | 浏览器操作、完整图集、导出与网络检查 |
| [archive-sha256-before.json](evidence/gui-20261021/archive-sha256-before.json) | 浏览前 6046 个文件的 SHA256 |
| [archive-immutability.json](evidence/gui-20261021/archive-immutability.json) | 浏览后内容一致性结果 |

启动页面：

```powershell
.\.venv\Scripts\landsat-grits.exe gui outputs --port 8765
```

重跑 API 与只读检查：

```powershell
.\.venv\Scripts\python.exe tooling/diagnostics/20261021_gui/verify_api.py --evidence docs/evidence/gui-20261021
.\.venv\Scripts\python.exe tooling/diagnostics/20261021_gui/expected_inventory.py --root outputs --evidence docs/evidence/gui-20261021 --check
```

浏览器脚本为 [`verify_browser.cjs`](../tooling/diagnostics/20261021_gui/verify_browser.cjs)，通过 `--playwright` 指定测试环境已有的 `playwright-core` 路径，通过 `--evidence` 指定报告目录。Playwright 仅用于验收，不是页面运行依赖。

首次 API 验收发现目录刷新与预览并发时会暂时丢失归档 ID，已用归档级进程内锁修复，并通过 HTTP 并发回归。[初始失败记录](evidence/gui-20261021/api-initial-failure.json)保留以便追溯。浏览器初始脚本曾错误假定 QA 图例标题包含“QA”；页面实际使用通用标题“数值与颜色”，脚本改为检查 QA 说明与离散图例后通过。该脚本断言调整没有改变数据或页面功能。

## 验证边界

预览采用有界抽样、最近邻重投影和独立 P2–P98 拉伸；统计反映预览采样，不替代原始分辨率分析。MSS 使用近红外假彩色；不同传感器的显示颜色不可直接定量比较。QA 按打包整数分类着色，未解码每个质量标志。当前真实数据每个归档只有一景，因此本次浏览器验收不代表真实长时间序列的全部交互已逐景测试。

页面显示本地已下载数据，不提供在线底图、全球未下载格网目录或新增下载操作。
