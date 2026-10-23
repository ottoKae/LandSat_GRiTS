# 独立发布准备验收

版本：`0.2.0b1`。日期：2026-10-23。验证环境：Windows、CPython 3.12.12。
这是首次公开测试版的准备结果，不表示已经上传或公开发布。

| 检查 | 结果 |
|---|---|
| 全部自动测试 | 281 passed；72.98 秒 |
| 历史格网合同回归 | 通过，显式设置 `LANDSAT_GRITS_LEGACY_MGRS`；普通 CI 不依赖此表 |
| Ruff（src、tests、examples、tooling/release） | 通过 |
| `uv lock --check --offline` | 通过 |
| 源码包→wheel 构建及内容检查 | 通过；包含许可证、GUI、示例和测试所需脚本，排除影像与日志 |
| 解压源码包后关键测试 | 独立目录、已安装 wheel 下 23 项通过，覆盖格网、离线示例和诊断脚本依赖 |
| 全新环境安装 wheel | 通过，确认导入来自 site-packages |
| 安装包离线两景演示 | TIFF→Zarr、校验、64×64 空间维度、两天有效性检查通过 |
| 安装包单景 GUI | HTML、JS、CSS、目录 API 的 HTTP 检查通过 |
| 可选 Resolver | 在上述环境单独安装本地 0.3.0 源码，年度预览与两处来源查询通过 |
| 官方格网 | ZIP SHA-256 与固定值一致；49QHE、50RKV 在 15／30／60 m 的几何、原点和尺寸均与历史表一致 |

官方 KML 来自 [Copernicus S2 Products](https://sentiwiki.copernicus.eu/web/s2-products)，
源 ZIP SHA-256 为 `67e222547cfea235f69d1dd35317babb0296c8c61556c198fc9f1e2dec7bcf29`。
转换优先使用高精度 `LL_WKT`，避免 KML 显示坐标舍入影响向外对齐。
新 GeoParquet 的文件摘要与历史表不同，因此不能替换已经冻结的历史清单输入。

代码采用 MIT；第三方数据未重新授权、未捆绑分发。卫星影像下载仍保持停止，本次只获取了
官方格网元数据。原始卫星 Zarr 与正在使用的四年份 GUI 没有改动。

仍未验证：远程 GitHub Actions 的 Linux／Python 3.13 矩阵、现代 L1 真实付费读取、
全球完整历史和多机并发。48 条测试警告主要为上游弃用提示与人工栅格的地理参考提示。
本轮未重新执行历史全格网下载或全部浏览器图层验收。

构建文件位于 `dist/0.2.0b1/`，使用说明见 [发布步骤](RELEASE.md)。
