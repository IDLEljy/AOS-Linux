# AOS-Linux：面向 AI Agent 任务的 Linux 内核扩展原型

> 大连理工大学《高级操作系统》课程设计 · 代码与部署材料（2026 年 11 月整理）
>
> 本仓库是**内核补丁 + 用户态原型 + 验证文档**，不是完整 Linux 发行版，也不是已经证明性能优于原版 Linux 的通用操作系统。

## 1. 项目简介

AI Agent 为完成一个目标，可能连续调用 Shell、Python、编译器以及其他工具，形成跨进程、跨操作的任务链。传统 Linux 以 PID、UID、cgroup 等机制管理进程和资源，但不会天然以“一个 Agent 任务”为独立身份来描述该任务链。

AOS-Linux 基于 Linux 6.12.114 尝试补充一层 **Agent Task** 任务语义：由用户态管理程序建立任务记录，使用任务 ID 与 cgroup v2 关联任务中的进程，通过 Linux Security Modules（LSM）在内核关键路径上进行策略检查，并保留审计记录。

本阶段**不重写 Linux 调度器，也不直接修改 `task_struct`**。CPU、内存、PIDs 等资源限制仍尽可能复用 cgroup v2 和 systemd。

## 2. 实现与验证状态

| 能力 | 源码/设计依据 | 当前证据与边界 |
|---|---|---|
| Linux 6.12.114 定制内核 | `kernel/aos-linux-6.12.114.patch` | 项目历史实验曾成功启动 `6.12.114-aos-slim`；本资料包的补丁**尚未与线上最终构建逐字节核对** |
| AOS 内核任务注册和 cgroup 关联 | 补丁中的 `security/aos/aos_task.c` | 已编写对应实现；完整绑定、并发和生命周期行为缺少随包自动化实测证明 |
| 原生 LSM 安全钩子 | 补丁中的 `security/aos/aos_lsm.c` | 历史实机检查曾出现 `capability,aos`；逐类访问拦截效果仍需负向测试 |
| `/dev/aosctl` 控制入口 | 补丁中的 `security/aos/aos_control.c` | 历史实机已见设备节点；不能仅凭节点存在证明 ioctl 全部正常 |
| 用户态 `aosd`、`aosctl` | `userspace/aosd/`、`userspace/aosctl/` | 已有 Python 服务、CLI、SQLite 任务记录、Landlock/systemd 接入代码；本次已验证基本 CRUD 冒烟测试，不代表内核联调通过 |
| Docker / Benchmark | 过往实验记录（见 `TEST.md`） | 曾在腾讯云测试环境执行；**完整冻结任务/容器镜像/runner 不在本压缩包中** |
| AI 自适应调度 | 长期设计方向 | **未作为已实现功能声明** |

> “设计存在”“代码存在”“内核成功加载”“功能行为通过测试”是四种不同的证据等级。本仓库按这个顺序审慎描述成果。

## 3. 系统架构

```text
AI Agent / 工具执行框架
          |
          v
用户态 aosd + aosctl
  - Agent/Task 任务记录、SQLite、工作区、管理接口
  - systemd transient scope / cgroup v2（用户态原型）
  - 可选内核 ioctl 后端
          |
          v
/dev/aosctl（ioctl）
          |
          v
AOS 内核扩展（Linux 6.12.114 补丁）
  - Task Registry / 任务生命周期
  - 与 cgroup v2 的任务归属关联
  - LSM 执行路径与文件访问检查
  - 审计记录
          |
          v
Linux 原生进程模型 / VFS / cgroup v2 / systemd
```

`aosd` 不在内核态运行，它是用户态的任务管理守护进程；真正的 LSM 检查发生在内核。`aosctl` 是用户态命令行客户端。两者的 Python 代码包含可选内核后端，但需先核对在线内核的 ABI 才能启用。

## 4. 仓库目录

```text
AOS-Linux/
├── kernel/
│   ├── aos-linux-6.12.114.patch    # 基于 6.12.114 的 AOS 内核增量补丁
│   └── aos.config                  # 2026-10-25 导出的配置片段
├── userspace/
│   ├── aosd/                       # Unix Socket 服务、SQLite registry、ioctl/Landlock
│   └── aosctl/                     # CLI、任务执行封装
├── scripts/
│   ├── check-runtime.sh            # 只读核验已启动内核
│   └── smoke-userspace.sh          # 不启用内核 ioctl 的用户态冒烟测试
├── benchmark/
│   └── benchmark_summary.csv       # 历史统计汇总，不是原始逐题日志
├── docs/
│   ├── 历史开发记录_2026-10-25.md
│   ├── 架构设计方案_V1.1.md
│   └── SOURCE_AUDIT.md             # 代码来源、版本不一致与核验结果
├── README.md
├── DEPLOY.md
├── TEST.md
└── SUBMISSION_CHECKLIST.md
```

这份精选仓库**有意没有混入**开发目录里的多套旧 `aos*.c`、`aos/remote/`、生成脚本、`.conda/`、`.pyc`、整个 Linux 内核源码及大型容器镜像。内核补丁自身包含实现所需的 `security/aos/` 新增文件。

## 5. 快速查看（无需修改系统）

在 Linux 或 WSL 中：

```bash
# 进入仓库根目录
sha256sum kernel/aos-linux-6.12.114.patch kernel/aos.config
# 仅检查补丁文件能否被 git 解析；不是“应用成功/构建通过”的证明
git apply --stat -p1 kernel/aos-linux-6.12.114.patch
# 只读检查已启动的内核、LSM 和设备
bash scripts/check-runtime.sh
# 用户态服务与任务 CRUD 冒烟测试（不需要 root / 不调用内核 ioctl）
bash scripts/smoke-userspace.sh
```

详细内核编译及部署步骤见 [`DEPLOY.md`](DEPLOY.md)，功能测试与性能结果见 [`TEST.md`](TEST.md)。

## 6. Benchmark 结果摘要

| 指标 | Ubuntu 5.15 | 原版 Linux 6.12 | AOS-Linux 6.12 |
|---|---:|---:|---:|
| UnixBench 单并发（3 轮均值） | 711.03 | **796.27** | 753.93 |
| UnixBench 双并发（3 轮均值） | 1020.47 | **1132.13** | 1079.17 |
| TerminalBench Oracle 满分通过 | 13/25 | 13/25 | **20/25** |
| TerminalBench DeepSeek Agent 满分通过 | 14/22 | 14/22 | 14/22 |

这些数据来自项目此前留存的实验输出，不是本压缩包内重新执行得到的结果。Oracle 的 80% 对 52% 是观察到的**实验环境级差异**，尚未证明由 AOS 机制单独造成。通用 UnixBench 分数最高的是原版 Linux 6.12；Agent 总通过数三组相同。

## 7. 使用与安全边界

- **课程实验原型，不建议用于生产或多租户服务器。** 内核安全模块需要单独进行安全审查、权限测试与稳定性验证。
- 只读检查可在已有机器上进行；首次构建与启动应先用隔离虚拟机，并保留可回滚的旧内核。
- 历史实机出现过 `/dev/aosctl` 权限过宽的记录；本补丁声明的设备模式是 `0600`，必须用 `ls -l /dev/aosctl` 核对实际系统，不能假定一致。
- 旧版源码中还出现过 `/dev/aos_control` / `securityfs` 路线，与本仓库的补丁不同，不能混用。
- 本仓库不包含 DeepSeek API Key、云服务器凭据或完整 Benchmark 容器数据。
- 许可：内核增量文件包含相应 SPDX 标记，重新发布须遵守 Linux 内核及相关代码的许可证要求；用户态代码的最终授权和完整贡献/许可证文件应由小组确认后补充。

## 8. 相关文档

- [`DEPLOY.md`](DEPLOY.md)：补丁应用、配置、编译、谨慎安装、验证以及部署差异。
- [`TEST.md`](TEST.md)：测试命令、实验口径与 Benchmark 结果。
- [`docs/SOURCE_AUDIT.md`](docs/SOURCE_AUDIT.md)：本次实际核对到的源码证据、风险与缺口。
- [`SUBMISSION_CHECKLIST.md`](SUBMISSION_CHECKLIST.md)：课程设计与 GitHub 提交前核对清单。
