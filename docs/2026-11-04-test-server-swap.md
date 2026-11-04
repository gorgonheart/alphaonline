# 2026-11-04 测试服务器swap调整为8GiB

## 当前配置：20GiB（用户后续更新目标）

用户将目标由8GiB改为20GiB。复查时8GiB swap使用0、可用内存6.4GiB、磁盘空余99GiB；再次原路径swapoff/fallocate20G/mkswap/swapon成功，无需重启。

实际文件21474836480字节（20GiB）、权限600，活动swap21474832384字节（减4KiB页头），使用0B；free -h取整显示19Gi不表示文件少配置1GiB。原fstab条目保持，swap.img.swap与Mihomo均active，swappiness保持60，根盘空余约87GiB。脚本research/alpha_test_swap_resize_20g.sh。以下8GiB为先前历史。

用户明确要求测试机swap改为8GB，目标ubuntu@43.173.122.28。

调整前仅/swap.img一个文件，内核可用2084564992字节、使用约349MiB；物理内存可用约6.7GiB、空闲约1.4GiB，根盘空余约105GiB。确认内存可容纳全部旧换出页后，swapoff仅作用于/swap.img，fallocate原路径扩容8GiB、chmod600、mkswap、swapon。没有使用swapoff -a或调整分区。

实际验证：文件8589934592字节（8GiB）、权限600；活动swap8589930496字节（扣一页头部），free -h显示8.0GiB，验证时使用0B。/etc/fstab原有/swap.img none swap sw 0 0仅一条，路径不变，无需修改；swap.img.swap为active，swappiness保持60。根盘仍约99GiB可用。

没有重启机器、alpha或Mihomo；验证时mihomo-alpha.service仍active，没有修改程序/账户/行情。未实际重启验证，开机持久化依据现有fstab条目。操作脚本research/alpha_test_swap_resize_8g.sh，直接命令free -h或swapon --show可复查。
