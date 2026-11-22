# AOS-Linux 代码与部署说明

**目标：** 为课程设计提供基于真实文件的部署过程，并明确“已验证的历史环境”与“仓库可复现的操作”之间的差距。下文指令默认在 Linux/WSL Bash 中执行；**不会自动替换当前内核**。

## 1. 项目环境

| 项目 | 项目历史实测环境/要求 |
|---|---|
| 内核来源 | Linux 6.12.114（上游源码） |
| 历史运行内核 | `6.12.114-aos-slim` |
| 对照内核 | Ubuntu `5.15.0-181-generic`；主线 `6.12.114-0612114-generic` |
| 实验服务器 | 腾讯云 Ubuntu，2 vCPU，约 4 GiB RAM |
| 内核扩展 | `security/aos/`、`include/uapi/linux/aos.h` 及 LSM 接入 |
| 控制接口 | `/dev/aosctl`（以本仓库 2026-10-25 补丁为准） |
| Python 用户态 | `python3 -m userspace.aosd.service` / `python3 -m userspace.aosctl.cli` |
| 资源控制 | Linux cgroup v2、systemd（用户态） |
| Benchmark | UnixBench、TerminalBench 2.1/Harbor/Docker（历史实验） |

**重要缺口：** 本压缩包没有历史线上 `~/aos-kernel-build/out/aos-slim/.config` 的完整文件，也没有当时运行的 `apply-aos.py` 最终构建脚本与所有日志。因此，当前的 `kernel/aos-linux-6.12.114.patch` 虽然与 10 月 25 日开发记录匹配，却**不能直接证明与后来线上启动的 `aos-slim` 镜像逐字节相同**。如需严格复现答辩中的内核构建，必须补齐最终 `.config` 与构建脚本。

## 2. 先校验交付物

在本仓库根目录执行：

```bash
sha256sum kernel/aos-linux-6.12.114.patch kernel/aos.config
```

本资料包核对到的 SHA256：

```text
8bf972eb3e1816a776a3e9cdaf6e1b6bfaa0aa8b4e75f20e5b9538e015d2e096  kernel/aos-linux-6.12.114.patch
63d9443185559d506cc39db84fa52f267a79dfed629493e3ad5645e7e0bd79e9  kernel/aos.config
```

补丁通过 `git apply --stat -p1` 的语法解析；这**不等于**在原始 Linux 6.12.114 上 `--check` 成功、更不等于编译通过。历史记录曾记载 `git apply --check -p1` 在当时源码树通过，但本次没有该完整源码树用于重新验证。

## 3. 获取 Linux 6.12.114 原始源码

建议使用隔离的 Ubuntu/WSL 构建环境，安装相应构建工具（Ubuntu/Debian 示例）：

```bash
sudo apt update
sudo apt install -y build-essential bc bison flex libssl-dev libelf-dev \
    libncurses-dev dwarves rsync kmod cpio xz-utils git
```

从 [kernel.org](https://www.kernel.org/) 下载**明确版本** `linux-6.12.114.tar.xz`，并保存在自己的下载目录。示例（下载地址需在使用时核实）：

```bash
mkdir -p "$HOME/aos-rebuild"
cd "$HOME/aos-rebuild"
# 如已从 kernel.org 下载好文件，可跳过此下载步骤
curl -fL -O https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.114.tar.xz
sha256sum linux-6.12.114.tar.xz
# 10 月 25 日历史记录的校验值：
# 7a8589912bd7a8adb07fed074283652cf482a35facef8954554654f0154b4af3
# 如校验不一致，停止，不要应用补丁
printf '%s  %s\n' \
 '7a8589912bd7a8adb07fed074283652cf482a35facef8954554654f0154b4af3' \
 'linux-6.12.114.tar.xz' | sha256sum -c -
tar -xf linux-6.12.114.tar.xz
```

> 不要用目录名相似的其他 6.12.x 次版本代替，因为内核 API 和补丁上下文可能不同。

## 4. 应用 AOS 内核补丁

假设 `PROJECT` 是**这个 GitHub 仓库的绝对路径**：

```bash
PROJECT="$(realpath /path/to/AOS-Linux)"  # 修改为自己的实际路径
cd "$HOME/aos-rebuild/linux-6.12.114"

git apply --check -p1 "$PROJECT/kernel/aos-linux-6.12.114.patch"
# 上一步无报错，才实际写入源码：
git apply -p1 "$PROJECT/kernel/aos-linux-6.12.114.patch"
```

补丁预计修改/新增 12 个路径，包括：

```text
include/uapi/linux/aos.h
include/uapi/linux/lsm.h
security/aos/{Kconfig,Makefile,aos_audit.c,aos_control.c,
              aos_internal.h,aos_lsm.c,aos_policy.c,aos_task.c}
security/Kconfig
security/Makefile
```

**不要**同时复制根目录里旧版本的 `aos_control.c`、`aos_lsm.c` 等文件覆盖补丁结果。旧文件有与本补丁不同的 ABI、设备路径和初始化逻辑。

## 5. 配置和构建（隔离环境优先）

这里给出可供复核的**建议性构建步骤**，并非声称本资料包已经重新编译出与历史 `aos-slim` 完全一致的二进制。

```bash
PROJECT="$(realpath /path/to/AOS-Linux)"  # 修改为实际路径
SRC="$HOME/aos-rebuild/linux-6.12.114"
BUILD="$HOME/aos-rebuild/out"
mkdir -p "$BUILD"
cd "$SRC"

# 仅作起点：使用当前系统内核配置（与历史 final .config 不一定相同）
cp "/boot/config-$(uname -r)" "$BUILD/.config"

# 将项目配置片段叠加到构建输出目录
./scripts/kconfig/merge_config.sh -m -O "$BUILD" \
  "$BUILD/.config" "$PROJECT/kernel/aos.config"

# 使用与正在编译的源码一致的配置脚本复核
make O="$BUILD" olddefconfig

grep -E '^(CONFIG_SECURITY_AOS=|CONFIG_LSM=)' "$BUILD/.config"
# 期望 CONFIG_SECURITY_AOS=y 且 CONFIG_LSM 包含 aos

# 先尝试低并行度编译，以减轻 4 GiB 机器的内存压力
make O="$BUILD" -j1 bzImage modules
```

**配置提醒：** 项目 `kernel/aos.config` 内的 `CONFIG_LSM` 可能包含目标机器未启用的 LSM 项，应在 `olddefconfig` 后检查实际结果。还需保证 `CONFIG_SECURITY`、`CONFIG_CGROUPS`、`CONFIG_SECURITY_PATH`、`CONFIG_MISC_DEVICES` 等相关依赖得到满足。对照实验必须保存完整 `.config`，而不是仅保存片段。

## 6. 安装与启动：人工审批步骤

**不要在当前腾讯云教学/生产服务器上直接照抄以下命令并重启。** 内核安装会影响下一次启动路径；只有在具备快照/控制台恢复手段、已验证备用内核并获得操作许可的机器上才继续。

在隔离测试虚拟机中，确认上一步构建完成后才考虑：

```bash
# 以下仅列出标准安装操作，不由本仓库脚本自动执行
sudo make O="$BUILD" modules_install
sudo make O="$BUILD" install
# Ubuntu/Debian 还需按实际安装结果检查 initramfs 与 GRUB 菜单
# sudo update-initramfs -c -k <new-kernel-release>
# sudo update-grub
```

重启前请保存 `uname -r`、完整 `BUILD/.config`、安装日志、GRUB 可回退项和控制台救援方案。只有内核实际启动后，才可以将该构建记为运行验证通过。

## 7. 验证已经启动的 AOS 内核（只读）

在启动了 AOS 的机器上：

```bash
uname -r
cat /sys/kernel/security/lsm
ls -l /dev/aosctl
sudo dmesg | grep -i 'aos' | tail -n 30
bash scripts/check-runtime.sh
```

历史实验曾记录 `6.12.114-aos-slim`、`capability,aos` 及 `/dev/aosctl` 的存在，但本仓库不把该历史状态当作此补丁每次构建的必然结果。

**安全检查：** 本补丁内核设备目标权限为 `0600`，而某次历史实机显示 `/dev/aosctl` 为 `0666`。以当前 `ls -l` 实测为准；若对普通用户开放写权限，应先排查来源（udev、实际构建版本、启动脚本），**不要在共享环境下进行权限策略试验**。

## 8. 用户态服务与 CLI（可不启用内核 ioctl）

Python 代码使用了 `str | None` 等类型标注，建议 Python **3.10 或更新版本**。

在仓库根目录可以使用无 root 的临时路径运行基本服务：

```bash
export PYTHONPATH="$PWD"
mkdir -p /tmp/aos-course-demo
python3 -m userspace.aosd.service \
  --state-dir /tmp/aos-course-demo/state \
  --workspace-root /tmp/aos-course-demo/workspaces \
  --socket /tmp/aos-course-demo/aosd.sock \
  --socket-group ''
```

另开终端：

```bash
export PYTHONPATH="$PWD"
python3 -m userspace.aosctl.cli --socket /tmp/aos-course-demo/aosd.sock status
python3 -m userspace.aosctl.cli --socket /tmp/aos-course-demo/aosd.sock create --agent demo
python3 -m userspace.aosctl.cli --socket /tmp/aos-course-demo/aosd.sock list
```

这里的示例只覆盖任务记录与 IPC，不启动内核访问拦截。使用 `--kernel-control` 时，`aosd` 将通过 `/dev/aosctl` 发送 ioctl，必须先校验 ABI、设备权限及当前内核是否匹配。**不建议直接在云服务器上启用未经验证的策略写入接口。**

注意：`aosctl exec` 依赖 systemd scope、Landlock 和内核激活交互；本资料包没有对该端到端执行路径做成功验证，不能只凭 `create/list` 正常就认定 `exec` 可用。

## 9. 历史 Docker / Benchmark 部署

- 旧内核/精简内核的 iptables、raw table 与 Docker 网桥适配是项目历史部署的一部分，不等同于本仓库补丁自动修复 Docker。
- TerminalBench 的真实运行依赖 Harbor、Docker、冻结任务镜像、基线 CSV、runner 脚本及模型 API 配置。本包只带聚合结果，**不包含执行这些任务所需的全量材料**。
- 不应把 DeepSeek API Key 写入仓库、命令参数或报告截图。更多实验口径参见 `TEST.md`。

## 10. 常见问题与排查

| 现象 | 检查项 | 建议 |
|---|---|---|
| 补丁无法应用 | 源码版本、`git apply --check -p1` | 确保全新未修改 Linux 6.12.114 源码；不要强制合并 |
| 配置无 `CONFIG_SECURITY_AOS=y` | Kconfig 依赖、`CONFIG_LSM` | 保存 `olddefconfig` 输出和完整 `.config` |
| 系统启动后无 `aos` | `/sys/kernel/security/lsm`、`dmesg` | 区分“编译进内核”和“LSM 实际初始化” |
| `/dev/aosctl` 不存在 | miscdevice、内核日志 | 排查初始化失败，不要用手工 `mknod` 假冒功能 |
| `/dev/aos_control` 存在 | 旧版实现 | 不要拿旧版 ABI 调用本仓库的 `aosctl` |
| ioctl 返回 EPERM/ENOTTY | 权限、ABI、运行内核版本 | 先只读核验；禁止盲目反复调用 |
| Benchmark 结果不同 | kernel config、Docker、镜像、负载、任务 seed | 核实固定条件，重复测量并保留原始日志 |

## 11. 复现等级

1. **静态材料复现**：可查看补丁、配置与文档；本包已具备。
2. **用户态最小运行**：`aosd` 与 CLI 可创建/查询任务；本次已完成冒烟验证。
3. **内核重新构建/启动**：历史环境有记录，但本资料包缺少最终精确 `.config`，本次未在容器里构建或引导内核。
4. **内核安全策略端到端演示**：需要单独留存允许/拒绝场景、审计日志和失败恢复记录。
5. **严谨性能归因**：需要相同内核配置、相同负载、机制开/关与重复实验；现阶段尚不能完成因果归因。
