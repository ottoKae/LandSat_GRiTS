# 验证记录：2026-10-20

本文件下文记录首版 PC MSS/L2 验证，不替代后续新增原生 L1 的检查。
**v0.2 全套结果为 161 passed**，原生 L1 范围与尚未验证部分见
[原生 L1 验证记录](VALIDATION_NATIVE_L1.md)。

## 自动化与构建

在仓库独立 Python 3.12.12 环境执行：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp D:\Project\claude-demo\.tmp\tests\landsat_grits\final-1020d
.venv\Scripts\python.exe -m ruff check src tests tooling
uv lock --check --quiet
uv build --quiet
```

结果：**88 passed**；ruff 通过；依赖锁校验通过；wheel/sdist 构建通过。
测试产生 3 条 Shapely `ops.transform` API 弃用提示，不影响本次像元核验。
完整 pytest 输出见 [pytest-20261020.txt](evidence/pytest-20261020.txt)。

测试覆盖：

- 固定格网、南北半球、坐标中心、外扩对齐、无效输入和源表 SHA。
- 1–9 产品选择、MSS 原波段编号、L6 无数据、产品级别隔离。
- GET/POST 分页、重复页检测、网络失败、日期边界、SAS 不落盘。
- 合成源像元到 `(time,bands,x,y)` 的确切位置和值。
- QA 合法零值、缺失温度、int16 辅助变量、源 nodata 标签回退。
- 同时间不同场景、重复续传、场景身份和来源指纹变更。
- 网络写入失败回滚、异常中断尾部恢复、缺 commit 和缺 group 明确失败。
- 规划失败不发布部分清单、pilot 不宣称完整、验证报告缺景和来源变更。
- L2 公共 band 读取、掩膜与缩放、xarray/Dask 延迟读取。

## 真实数据

[live_probe.json](evidence/live_probe.json) 是联网端到端试点的机器记录；
状态、每景结果、像元比对、实际 dtype、网格、时间、来源和脚本哈希均在其中。
只有 `status=passed` 才代表该次运行全部完成。

脚本：[tooling/diagnostics/20261020/live_probe.py](../tooling/diagnostics/20261020/live_probe.py)。
使用官方 PC STAC 搜索、即时 URL 签名、MTL 读取、COG 窗口读取、真实
SceneStore 写入、xarray 打开、独立 WarpedVRT 对照及直接源像元对照。
每个试点仅为 50RKV 完整 MGRS 网格中 **64 × 48** 的整数偏移窗口：

- Landsat 9：`LC09_L2SP_122039_20251208_02_T1`。
- Landsat 5 MSS：`LM05_L1TP_122038_19850422_02_T2`。

最终结果：**两景均 passed**。L9 为 19 个数据波段，MSS 为 6 个，
合计 **76,800 个 band 像元及 valid 掩膜**与独立源读取一致；
25 个直接源像元对照也一致。两库各提交一景，重复运行均跳过。
特别验证了 QA_RADSAT 的有效零值未被当作 nodata。
最终记录完成于北京时间 **2026-10-20 21:27**，脚本哈希与当前文件一致。

试点数据位于专用 `.tmp/tests/landsat_grits/live-1020c`；
此处保存证据，不把该目录或截窗当作正式 MGRS 生产库。
源 COG 实际网络传输量未测量；小窗口请求可能读到更大的源压缩块。
未执行整幅、全时段或全球下载。

另有 [source_probe.json](evidence/source_probe.json) 记录五个来源样本、MTL
标定缺失、USGS HTTPS 登录跳转和参考 GitHub 地址可达性；它本身不证明像元正确。

## 这些验证不证明什么

没有证明所有年代、所有轨道、所有 CRS 边界和所有特殊产品均已通过联网试点；
没有验证独立的 TM/ETM+ 真实像元流程；没有做长期断电、文件系统损坏、
多机并发、对象存储写入和全量压缩块校验。

本版本的 `verify` 是结构、身份、来源指纹及清单对账。
即使清单满足，也不代表 USGS 全球档案不存在漏景或源端缺口。
