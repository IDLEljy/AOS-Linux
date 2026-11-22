#!/usr/bin/env bash
# Read-only AOS kernel evidence collection. Does not invoke ioctl or change system state.
set -u
printf '%s\n' '=== Running kernel ==='
uname -r
printf '\n%s\n' '=== Active LSMs ==='
if [ -r /sys/kernel/security/lsm ]; then
  cat /sys/kernel/security/lsm
else
  printf '%s\n' 'LSM list not readable (securityfs may be unmounted)'
fi
printf '\n%s\n' '=== AOS device ==='
if [ -e /dev/aosctl ]; then
  ls -l /dev/aosctl
  mode="$(stat -c '%a' /dev/aosctl 2>/dev/null || true)"
  if [ "$mode" = 666 ] || [ "$mode" = 777 ]; then
    printf '%s\n' 'WARNING: AOS device appears world-writable; investigate udev / deployed kernel version.'
  fi
elif [ -e /dev/aos_control ]; then
  ls -l /dev/aos_control
  printf '%s\n' 'WARNING: old interface name; verify ABI, do not mix kernel/user-space versions.'
else
  printf '%s\n' 'No known AOS control device exists.'
fi
printf '\n%s\n' '=== cgroup v2 ==='
if [ -f /sys/fs/cgroup/cgroup.controllers ]; then
  printf '%s\n' 'cgroup v2: available'
else
  printf '%s\n' 'cgroup v2: not detected at /sys/fs/cgroup'
fi
printf '\n%s\n' '=== Docker availability ==='
if command -v docker >/dev/null 2>&1; then
  docker --version
else
  printf '%s\n' 'docker: not found on PATH'
fi
printf '\n%s\n' 'No AOS ioctl calls or system changes were performed.'
