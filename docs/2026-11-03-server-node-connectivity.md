# 测试服务器Cloudflare节点连通性检测

检测时间：2026-11-03 19:50:18 UTC+8。服务器：43.173.122.28；来源：用户指定RpKoM1tWNRsX.yaml。

本轮只在服务器用Cloudflare DNS-over-HTTPS查询A记录，再用返回的公网IPv4测试TCP及按原配置进行TLS握手。127条配置、27个不同地址名称（23个NOERROR、4个NXDOMAIN）；6个并发端口/握手检查，4个并发DNS查询。没有采用本机解析结果替代Cloudflare结果，没有安装/启动VPN、改变DNS/代理或修改alpha。

| 结果 | 节点数 |
| --- | ---: |
| TCP+TLS握手通过 | 23 |
| 仅TCP通过（Mieru，本检测不适用TLS） | 3 |
| Cloudflare无法解析，未测TCP | 98 |
| 已解析但TCP超时 | 3 |

**这些是网络层检测，不是密码/协议认证或实际代理出口验收。** TLS按原配置的SNI/证书设置处理；TCP毫秒值不是代理完整请求延迟。NXDOMAIN仅代表本轮Cloudflare解析失败，不能断言线路在其他DNS路径也失效。

## 本轮网络可达节点

| 节点名 | 协议 | TCP连接ms | TLS握手 |
| --- | --- | ---: | --- |
| 台湾HiNet[M][Trojan][测试][倍率:0.5] | trojan | 141.0 | 通过 |
| 台湾HiNet2[M][Trojan][测试][倍率:0.5] | trojan | 142.24 | 通过 |
| 德国DF[M][Trojan][倍率:0.5] | trojan | 154.25 | 通过 |
| 德国DF2[M][AnyTLS][倍率:0.5] | anytls | 149.76 | 通过 |
| 德国DF3[M][Mieru][倍率:0.5] | mieru | 149.24 | 不适用/未测协议认证 |
| 新加坡BGP[M][AnyTLS][倍率:0.7] | anytls | 166.93 | 通过 |
| 日本BGP2[M][AnyTLS][倍率:0.6] | anytls | 105.29 | 通过 |
| 日本NTT[M][AnyTLS][倍率:0.3] | anytls | 101.41 | 通过 |
| 日本NTT2[M][AnyTLS][倍率:0.3] | anytls | 101.31 | 通过 |
| 美国AN[M][AnyTLS][测试][倍率:0.5] | anytls | 2.52 | 通过 |
| 美国AN2[M][AnyTLS][倍率:0.8] | anytls | 11.53 | 通过 |
| 美国AN3[M][AnyTLS][测试][倍率:0.5] | anytls | 2.49 | 通过 |
| 美国BGP[M][AnyTLS][倍率:0.6] | anytls | 11.94 | 通过 |
| 美国BGP2[M][AnyTLS][倍率:0.6] | anytls | 10.96 | 通过 |
| 美国BGP3[M][AnyTLS][倍率:0.6] | anytls | 11.1 | 通过 |
| 美国BGP5[M][Mieru][测试][倍率:0.6] | mieru | 11.14 | 不适用/未测协议认证 |
| 美国BGP6[M][AnyTLS][倍率:0.6] | anytls | 11.26 | 通过 |
| 美国BGP7[M][AnyTLS][倍率:0.6] | anytls | 11.17 | 通过 |
| 美国BGP8[M][AnyTLS][倍率:0.6] | anytls | 14.09 | 通过 |
| 美国BGP9[M][AnyTLS][倍率:0.6] | anytls | 11.29 | 通过 |
| 美国BGP10[M][Mieru][测试][倍率:0.6] | mieru | 13.32 | 不适用/未测协议认证 |
| 英国CV[AnyTLS][倍率:0.5] | anytls | 135.77 | 通过 |
| 韩国KT[M][AnyTLS][倍率:0.5] | anytls | 136.28 | 通过 |
| 韩国SK[M][AnyTLS][倍率:0.6] | anytls | 138.2 | 通过 |
| 香港BGP[M][AnyTLS][倍率:0.3] | anytls | 153.99 | 通过 |
| 香港HKT[M][Trojan][倍率:0.5] | trojan | 152.85 | 通过 |

## 已解析但TCP超时

| 节点名 | 协议 | 结果 |
| --- | --- | --- |
| 香港BGP2[M][AnyTLS][倍率:0.8] | anytls | TimeoutError |
| 香港BGP3[VLESS][倍率:1] | vless | TimeoutError |
| 香港BGP5[VLESS][倍率:1] | vless | TimeoutError |

## 完整明细与清理

[本地完整CSV](../research/node-connectivity-20261103.csv) · [本地JSON](../research/node-connectivity-20261103.json)。服务器相同文件位于/home/ubuntu/alpha-deploy-logs/node-connectivity-20261103.csv和.json；日志为同名.log。所有127条结果含解析失败节点均在明细内。

只上传无密码/UUID/私钥的连接元数据；完成后已删除服务器临时节点域名输入。结果只保留节点显示名/协议/端口/状态/耗时，无密码、域名或IP地址；服务器结果权限600。卸载状态复核：Mihomo二进制、systemd服务和私有配置目录仍不存在，无mihomo进程，7897无监听。

没有自动选择、安装或切换任何节点。
