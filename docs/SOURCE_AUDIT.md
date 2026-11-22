# 源码核验记录与版本说明

本文件记录 2026-11-22 对用户上传的 `AOS-Linux-核心源码-精简.zip` 的静态核查和本地用户态冒烟测试。它不代替内核构建、引导或安全测试。

## 1. 资料核对

- 原压缩包：65 个文件，解压后约 0.27 MiB，包含若干历史版本、修复脚本、Python 用户态代码、内核补丁及开发记录。
- `aos-linux-6.12.114.patch`：SHA256 `8bf972eb3e1816a776a3e9cdaf6e1b6bfaa0aa8b4e75f20e5b9538e015d2e096`，与《2026-10-25 内核补丁开发进度》记载一致。
- `aos.config`：SHA256 `63d9443185559d506cc39db84fa52f267a79dfed629493e3ad5645e7e0bd79e9`，同样与记录一致。
- `git apply --stat -p1` 能解析补丁，统计为 **12 个文件、517 行新增、5 行删除**。
- 本次未获取完整 Linux 6.12.114 原始源码进行应用/编译测试；不能将 `git apply --stat` 说成 `git apply --check`、`make` 或启动通过。

## 2. 补丁文件与功能映射

| 文件 | 实现意图 |
|---|---|
| `include/uapi/linux/aos.h` | `AOS_ABI_VERSION=1`、任务结构体、限制标志、ioctl 命令 |
| `security/aos/aos_task.c` | 任务 registry、cgroup v2 绑定、ID/生命周期 |
| `security/aos/aos_policy.c` | workspace/exec 路径与限制规则 |
| `security/aos/aos_lsm.c` | 程序执行、文件 open/permission/mmap、路径变更检查钩子 |
| `security/aos/aos_control.c` | `/dev/aosctl` miscdevice、权限校验、CREATE/BIND/POLICY/QUERY/FINISH |
| `security/aos/aos_audit.c` | 内核 audit 事件输出 |
| `security/Kconfig`、`security/Makefile`、`security/aos/{Kconfig,Makefile}` | Kconfig 与编译链接接入 |
| `include/uapi/linux/lsm.h` | AOS LSM ID |

补丁中 `aos_control.c` 的设备名是 `aosctl`、模式是 `0600`，入口检查 `CAP_SYS_ADMIN`；`aos_lsm.c` 使用 `DEFINE_LSM(aos)`。这些是**源码事实**，不是所有运行环境的实测事实。

## 3. 特别重要的“多版本混用”风险

原 zip 中存在互不等价的源码快照：

- 根目录早期 `aos_control.c`：设备名使用 `/dev/aos_control`；
- `aos/` 中的 `aos_control.c`：一度采用 `securityfs` 路线；
- `aos/remote/`：另有较早源码版本；
- 根目录 `aos_task.h`：`AOS_PATH_MAX=4096`，结构体布局与补丁中的 `include/uapi/linux/aos.h` 不一致；
- 2026-10-25 同步的 `aos-linux-6.12.114.patch`：定义 `/dev/aosctl`，路径长度 1024，独立 ABI 版本查询。

**部署时只以同一版本的补丁 + UAPI + 用户态客户端为一组；绝对不要把旧 `.c/.h` 文件直接覆盖到已应用补丁的源码树。** 本精选包只将主线补丁视为内核代码入口，旧快照留在用户原始压缩包中备查。

## 4. 用户态代码

- `userspace/aosd/service.py`：Unix domain socket 服务，SQLite 任务表和审计，任务创建/列出/查询/结束，cgroup 使用量查询等。
- `userspace/aosd/kernel.py`：`ctypes` 结构体与 `/dev/aosctl` ioctl 封装，包含 ABI version/create/bind/policy/query/finish。
- `userspace/aosd/landlock.py`：Landlock 工作区限制。
- `userspace/aosctl/cli.py`：`create/get/list/finish/exec/resources/status` 命令行入口。
- `userspace/aosctl/landbox.py`：命令执行前后与 kernel.activate/kernel.finish 配合的流程。

在本次沙箱中，`python -m compileall userspace` 检查通过；实际启动了无内核后端的临时 `aosd` 服务，`status`、`create`、`list` 返回正常 JSON。**没有对 `--kernel-control`、`aosctl exec`、Landlock 拦截和 ioctl 做端到端验证。**

## 5. 需要明确披露的缺口

1. 线上最终运行的 `6.12.114-aos-slim` 曾使用 `~/AOS-Linux-Experiment-v1/scripts/apply-aos.py` 等构建流程；本 zip 的补丁是 10 月 25 日版本，未证明是最后部署二进制的准确来源。
2. 最终内核完整 `.config`、内核构建日志、启动日志没有包含在本包中；只有 `aos.config` 小片段。
3. 真实 `/dev/aosctl` 历史记录曾显示对所有用户可写，和此补丁的 `0600` 设计不一致，需要按实际内核版本核查，避免安全误判。
4. 缺少覆盖 ioctl 负向路径、任务绑定、访问拒绝/放行和审计的完整自动化测试及原始日志。
5. 缺少完整 Harbor/TerminalBench v4 runner、冻结任务快照、Docker 镜像、最终逐题 CSV/summary，因此本精选包不能独立重跑 Benchmark。
6. Python 服务中的系统级执行路径需要 Linux cgroup v2、systemd、Landlock 与内核 ioctl 共同配合；用户态 CRUD 冒烟测试并不能证明这一整条链可运行。
7. 早期开发脚本包含机器地址等环境信息，因此精选 GitHub 包没有带上它们。请勿直接将原始 Windows 整个 `OS project` 文件夹公开提交。
8. 尚未对最终许可证、贡献人、第三方依赖和安全性进行完整审计。

## 6. 正式报告建议用语

**可写：**“设计并实现 AOS Task 相关内核补丁及用户态管理原型；在历史测试环境中成功启动定制内核并加载 AOS LSM；完成 Docker 兼容适配与两类 Benchmark 测试。”

**暂不宜写：**“全部 Agent 生命周期/权限功能已通过内核级安全验证”“AOS 增加了 28% 的内核性能”“同版本下 AOS 全面优于原版 Linux”“完整源码可一键重建答辩时完全相同的内核”。
