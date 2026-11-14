# MillCut：面向造纸与印后车间的省纸 × 省工时排样与切割系统

**MillCut** 是一个面向造纸分切与印后包装裁切车间的**双目标三阶段断头台排样与切割系统**。我们的核心目标是：**根据原纸（板材）规格、客户订单需求与车间设备参数，输出完整、可执行、综合成本更低的排样与三阶段直刀切割方案**——同时解决车间最核心的两个矛盾维度：

1. **最省原纸**：在固定纸纹方向、零缺件零超产约束下减少母板张数与废料；
2. **最省时间（少换刀／叠切高效率）**：在批量订单上利用切纸机压纸叠切（如 200 张一叠一刀切透），大幅压缩切割版式数（换版调刀次数）与物理推刀次数，给出**极限省纸 / 综合总成本最低 / 最少换刀极速**的帕累托（Pareto）方案供车间按单价与工价自由选择。

- **在线双目标车间算账与 3D 裁切交互演示**：[https://paper.standora.cn](https://paper.standora.cn)

---

## 输入与交付输出

### 输入
- **板材规格（`*_bins.csv`）**：同规格矩形母板尺寸 `WIDTH × HEIGHT`（如大度 `889×1194mm`、正度 `787×1092mm` 或任意特规，无限供应）。
- **零件需求（`*_items.csv`）**：每种矩形成品零件的编号 `ID`、尺寸 `WIDTH × HEIGHT` 及需求件数 `COPIES`（100% 精确交付，不缺件、不超产积压）。
- **车间经济参数（可选，支持实时调节）**：单张大纸价格（默认 `¥2.00`）、每次换版调刀耗时（程控切纸机默认 `6 分钟`／分切圆刀机 `15 分钟`）、机台含人工小时费率（默认 `¥180/h`）、单叠最大压纸张数（默认 `200 张`）。
- **固定工艺约束**：三阶段非精确断头台通刀裁切（`3-Stage NonExact Guillotine`）、首刀水平（`Horizontal`）、禁止旋转（`oriented = true`）、零刀缝（`cut_thickness = 0`）、单次在线预算 `10.0` 秒。

### 最终交付输出

| 交付文件 | 包含内容与车间用途 |
| --- | --- |
| `pareto_summary.json`（批量单） | **多目标车间成本账单**：同时输出「极限省纸」「综合总成本最低」「最少换刀极速」全套非支配方案的张数、版式数、叠数、总推刀数、工时与人民币总成本 |
| `layout.json` | **排样方案**：每套版式的叠切张数，以及每个零件所在的版号、左下角坐标 `(x, y)`、精确尺寸与方向 |
| `cutting_plan.md` / `cutting_plan.json` | **裁切作业指导书**：第 1 阶段整板横向分条刀位、第 2 阶段纵向分列刀位、第 3 阶段高度齐头及非精确修边顺序 |
| `material_summary.json` | **用料与废料账单**：总用板张数、理论面积下界、整单材料利用率、总废料面积与废料率、尾板可复用余料高度 |
| `verification_certificate.json` | **双独立核验合格证**：需求数量 100% 匹配、尺寸准确、不越界、不重叠、满足三阶段非精确横首刀约束的 Python 与 C++ 双重独立核查记录 |
| `cut_tree.csv` | **标准切割树**：兼容上游 `PackingSolver` `SolutionBuilder::read` 与零依赖 `mill_cxx23` 校验器的精确切割树表 |

---

## 核心验证成果与相对 `PackingSolver` 的增量

### 一、批量生产订单：省原纸 × 省换刀工时双目标综合成本（R60，全部基线公平去重同版）

原版 `PackingSolver` 仅最小化母板张数，不建模切纸机换版调刀耗时与压纸叠切物理效应。MillCut 在 **100% 精确交付件数零超产** 且对所有基线执行同构版式合并去重的前提下，单次预算内生成双目标帕累托前沿：

| 评估数据集（100% 精确件数零超产） | 方法 / 方案选择 | 相对 `default` 综合成本胜／平／负 | 总用纸（张） | 切割版式数（换版次数） | 叠切总推刀数 | 车间总工时 | 程控切纸机总成本 | 分切圆刀机总成本 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **未见隔离验证集 `repeated_demand`**（8 例 × 2 = 16 次，`10s`） | `PackingSolver default` | — | **130** | 130 | 2,852 | 1,207.8 min | ¥3,883.40 | ¥9,200.80 |
|  | **`factory_pareto`（极限省纸）** | **4 / 10 / 2** | 132 | **118** | **2,608** | **1,099.2 min** | **¥3,561.60** | ¥8,427.20 |
|  | **`factory_pareto`（总成本最低）** | **16 / 0 / 0** | 153 | **70 (-46%)** | **1,439 (-50%)** | **635.9 min (-47%)** | **¥2,213.55 (-43.0%)** | **¥5,081.60 (-44.8%)** |
| **12 份标准白卡车间批量单**（共 14,585 件，独立统计） | `PackingSolver default (10s)` | — | **1,069** | 135 | 2,474 | 19.69 h | ¥5,681.30 | ¥11,227.60 |
|  | **`factory_pareto (10s)` · 极限省纸** | **9 / 2 / 1** | **1,071** | **118** | **2,220** | **17.35 h** | **¥5,265.00** | ¥10,166.00 |
|  | **`factory_pareto (10s)` · 总成本最低** | **12 / 0 / 0** | 1,174 | **40 (-70%)** | **659 (-73%)** | **5.65 h (-71%)** | **¥3,364.55 (-40.8%)** | **¥4,973.60 (-55.7%)** |
|  | **零依赖 `cxx23_pareto (3s)` · 浏览器内核** | **12 / 0 / 0** | 1,199 | **42 (-69%)** | **680 (-73%)** | **5.90 h (-70%)** | **¥3,460.00 (-39.1%)** | ¥5,376.00 |

### 二、单件／异形散单：同预算减少原纸用量（R54 未见验证 & R55 最终保留测试，`10.0` 秒）

通过两层动态宽度列/条带定价、LP/拉格朗日对偶重采样、精确覆盖分板求解与缩放不变在线三分支几何路由：

| 独立评估阶段 | 规模（实例 × 种子） | 原自动 `default` | 原生 `guide1` | 前序候选 `R50` | **交付系统 `mill_router`** | 相对 `default` 胜 / 平 / 负 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| **全新未见验证集（R54）** | 24 例 × 2 = 48 次/方法 | 212 张 (77.96%) | 210 张 (80.40%) | 210 张 (78.55%) | **205 张 (81.97%，废料 18.03%)** | **7 / 41 / 0（净省 7 张）** |
| **最终保留测试合计（R55）** | 48 例 × 2 = 96 次/方法 | 685 张 (81.49%) | 686 张 (80.61%) | 677 张 (82.58%) | **674 张 (84.26%，废料 15.74%)** | **12 / 83 / 1（净省 11 张）** |
| ↳ 其中：目标分条异构族 | 12 例 × 2 = 24 次/方法 | 82 张 | 82 张 | 74 张 | **70 张（省纸 14.6%）** | **12 / 12 / 0（零退化）** |

### 三、公开学术基准（Berkey & Martello `3NHO`）最好已知值对标与差距收敛（R58–R59）

- 在 R58 对照的 **20 个公开测试实例**（8 例 Berkey 开发集 + 12 例 Martello 集）上：将计算时限对齐原论文的 `60` 秒后，系统 **20 / 20（100%）全部追平长期公开最好已知记录**（证实 R58 中 Martello 差 1 张的 3 例源于 10 秒与 60 秒预算差异）；在 `10` 秒预算下，引入盲测目标张数精确下探（`hp_descent_10s`）也在 `5.8s` 内追平了 R52 唯一差 1 张的 `Class_03.2bp_100_5`（`8 / 8` 全部等于最好已知值）。
- 在包含新增 12 例 `n=100` 大单的全部 **32 个公开基准实例**中，盲测目标下探追平 **29 例**（连同 60 秒原生分支共追平 **30 例**），仅在极少数高难百件纯装箱题上差 1 张，未出现差 2 张及以上情况。

### 四、适用范围与诚实边界
- **批量多件单（`COPIES ≥ 2`）**：不仅在“极限省纸”档常能以零额外原纸直接减少版式数（12 单中 11 单原纸不增、8 单直接少换版），在“总成本最低”档更能以约 `9.8%` 的纸张余量换取 **`70%` 以上的换版与工时缩减**，显著降低工厂真实综合成本。
- **单件异形散单（`COPIES = 1`）**：无法叠切，自动聚焦省纸，在 2–6 张规模的分条／异构订单上常少用 1 张母板（利用率达 `95.8%–98.3%`）；在公开随机纯装箱大单（10–30 张母板、无批量结构）上并未刷新学术历史最好已知值，大单分支直接复用或对齐 `PackingSolver` 原生搜索。
- **问题范围**：`PackingSolver` 是支持十余类几何与阶段变体的通用排样框架；MillCut 专注深耕 **`3NHO`（造纸与印后固定纸纹、三阶段横首刀直切）** 这一垂直工业场景。真实企业 ERP 历史生产单仍有待现场进一步挂接验证。

---

## 仓库结构与快速开始

```text
millcut/
├── mill_cxx23/                 # 纯标准库零依赖 Modern C++23 排样、双目标帕累托与验真内核（CLI + Wasm）
├── web/                        # 浏览器离线双目标车间算账、双屏 2D 排样对比与 Three.js 3D 切纸演示站
├── experiments/                # R01–R60 完整研究方法、结论记录与服务器端混合引擎源码
│   ├── README.md               # 60 轮实验方法、正负结论与严谨归因全记录
│   ├── factory_pareto_cutter.py# 服务器端车间双目标帕累托与高精度下探引擎（Python + CP-SAT + PackingSolver）
│   ├── paper_mill_cutter.py    # 单件冻结三分支几何路由与六件套工单生成入口
│   ├── mill_policy.json        # 冻结在线几何路由策略参数
│   └── native/                 # C++ 动态宽度条带生成插件（colstrip_gen.cpp）与原生切割树校验器（ejection.cpp）
└── packingsolver/              # 上游基线子模块（fontanf/packingsolver @ 28ac801d，含公开学术基准数据）
```

### 1. 本地一键体验交互式 Web 车间算账与 3D 裁切台
仓库 `web/wasm/` 已附带预编译的 `~426 KB` 零依赖 WebAssembly 内核，无需任何后端即可离线运行：
```bash
python3 -m http.server 8080 --directory web
# 浏览器访问 http://localhost:8080
```

### 2. 编译零依赖 Modern C++23 原生 CLI 或 WebAssembly
仅需支持 C++23 的编译器（如 GCC 13+ / Clang 17+）与 CMake 3.25+，无任何第三方库依赖：
```bash
# 编译原生命令行工具 mill_cxx23_cli
cmake -S mill_cxx23 -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j

# 编译浏览器/Node 通用 WebAssembly（需 Emscripten SDK）
emcmake cmake -S mill_cxx23 -B build_wasm -DCMAKE_BUILD_TYPE=Release
cmake --build build_wasm -j
```

### 3. 服务器端混合引擎调用
拉取 `packingsolver` 子模块并构建 Python 扩展与 `experiments/native/colstrip_gen.cpp` 后，可通过 `experiments/factory_pareto_cutter.py`（双目标批量单）或 `experiments/paper_mill_cutter.py`（单件散单路由）直接读取 `bins.csv` 与 `items.csv` 输出完整车间六件套工单包（详见 [experiments/README.md](experiments/README.md)）。

---

## 数据源位置与复现说明

为保持代码仓库轻量，实验运行产出的中间证书与 `.zip` 归档包不纳入 Git 版本库；全部研究方法、实验协议、正负结果结论及数据源生成逻辑均完整保留，可按以下位置获取或确定性重建：

1. **研究方法与 60 轮完整结论记录**：见 [experiments/README.md](experiments/README.md)（完整保留 R01–R60 的正结果、负结果、消融诊断与冻结 SHA256 记录）。
2. **公开学术基准数据源（Berkey & Wang 1987、Martello & Vigo 1998 及 `3NHO` 最好已知值表）**：
   - 执行 `git submodule update --init` 拉取上游子模块 [`packingsolver`](https://github.com/fontanf/packingsolver)（固定提交 `28ac801df472e68736410411d70bfdb15f06e281`）；
   - 实例文件位于 `packingsolver/data/rectangle/berkey1987/` 与 `packingsolver/data/rectangle/martello1998/`；
   - 同规则历史最好已知值对照表位于 `packingsolver/data/rectangle/data_bin_packing_3nho.csv`。
3. **合成路由语料库与标准白卡车间批量订单**：
   - 1,760 例分层去重路由语料库由 [experiments/data_expand_colab.py](experiments/data_expand_colab.py) 与 [experiments/data_finalize_colab.py](experiments/data_finalize_colab.py)（固定随机种子 `SEED = 83001`）确定性生成；
   - 12 份标准规格白卡／牛卡车间批量订单规格与复现流程内置于 [experiments/r59_r60_factory_cost_and_bks_colab.py](experiments/r59_r60_factory_cost_and_bks_colab.py)（代表性订单与方案亦内置于前端静态数据 [web/factory_showcase.json](web/factory_showcase.json) 与 [web/showcase_presets.json](web/showcase_presets.json)）。

---

## 致谢与开源许可

- 本项目以 [**MIT License**](LICENSE) 开源。
- 衷心致谢 Florian Fontan 开发的开源排样求解器 [**PackingSolver**](https://github.com/fontanf/packingsolver)（MIT License），本项目的研究基线、大单回退分支及原生切割树格式交叉核验均建立在其扎实工作之上。
- 前端 3D 切纸机可视化基于 [**Three.js**](https://threejs.org/)（MIT License）；服务器端混合规划脚本可选调用 [**HiGHS**](https://highs.dev/)（MIT License）与 [**Google OR-Tools**](https://developers.google.com/optimization)（Apache-2.0 License）。
