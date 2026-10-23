# 原生 Level-1 验证记录

用户确认采用“按传感器分组，保留全部原生波段”后，v0.2 默认配置改为
MSS/TM/ETM+/OLI-TIRS 的 Level-1 主档案；现代 Level-2 保留为可选产品。
本次记录于 2026-10-21（Asia/Shanghai），自动测试与免费目录试点在
2026-10-20 夜间执行。没有执行收费像元下载。

## 已通过

- **161 项自动测试全部通过**，7 条 Shapely 既有 API 弃用提示。
  [完整输出](evidence/pytest-native-20261020.txt)。
- OLI 8 个多光谱、2 个热红外、1 个全色及 QA 的合成像元逐项保留。
- 15 m pan 有独立坐标、形状和分辨率，未合并为 30 m；
  pan 失败时所有组一起回滚，续传保持此前已提交像元。
- ETM+ B6_VCID_1/B6_VCID_2 分开定义；L8/9 的 cirrus、B10、B11 均纳入。
- OLI-only 与 TIRS-only 场景保留实际仪器的波段，其余组明确缺失；
  instrument 与严格 C2 ID 矛盾时拒绝，必需波段缺失时拒绝。
- 本地产品文件匹配、重名／缺失检测、MTL 产品 ID 校验。
- AWS Requester Pays 的显式配置、SDK 参数和错误路径通过 mock 测试；
  **未使用真实 AWS 凭证或访问该付费桶的像元**。
- 默认 `auto` 下遇到现代 USGS L1，在任何场景像元下载前明确停止。
- 旧 MSS/L2 四个真实 S1 格网合同哈希与 v0.1 一致；既有归档可继续使用。
- Ruff 与构建检查通过，版本为 0.2.0，依赖由 `uv.lock` 固定。

运行命令：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp D:\Project\claude-demo\.tmp\tests\landsat_grits\native-1020e
.venv\Scripts\python.exe -m ruff check src tests tooling
uv lock --check --quiet
uv build --quiet
```

## 真实免费目录试点

实际运行新版 CLI：

```powershell
.venv\Scripts\landsat-grits.exe plan config\example.yaml `
  --limit-per-search 1 --manifest docs\evidence\native_l1_pilot_manifest.json
```

生成的[冻结清单](evidence/native_l1_pilot_manifest.json)明确标记
`selection=pilot`、`search_complete=false`。它保留原始 PC/USGS item，
并记录每个 product/group 的固定 MGRS 格网和原始 GeoParquet SHA-256。
只是目录规划，不包含栅格读取，也不证明全时段覆盖。

免费 USGS 元数据另核对了 TM、ETM+、OLI/TIRS 及四个 sensor-only 产品；
具体源条目与官方链接见 [SOURCES.md](SOURCES.md)。

## 验证边界

本次原生 L1 验证是**真实目录＋本地合成像元＋模拟 AWS 调用**，
不是 USGS 在线像元全链路验收。正式执行现代 L1 仍需要用户提供已下载并解包的
Collection 2 产品目录，或显式选择自己已配置的 AWS requester-pays 账户。
当前不实现 EarthExplorer 登录／M2M 自动认证下载。

此前 PC MSS/L2 的两景真实小窗口检查保留在
[VALIDATION.md](VALIDATION.md)，不能把它当作 USGS L1 像元已验收的证据。
没有执行整 tile、全时段、全球或长时间生产压力验证。
