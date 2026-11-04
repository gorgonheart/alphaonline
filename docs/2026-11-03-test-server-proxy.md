# 2026-11-03 测试服务器 Mihomo 本机代理

## 当前状态：日本NTT已重新安装并验证可用（2026-11-03 20:10 UTC+8）

用户明确重新授权安装Mihomo并连接日本NTT。已从MetaCubeX官方v1.19.33发行重新安装兼容版、核验发行方摘要，选择原profile的“日本NTT[M][AnyTLS][倍率:0.3]”直连主节点（AnyTLS/65102，不是NTT2或上海中转）。仅导入这一条节点，私有config.yaml权限600；本地临时凭据文件传完已删除。

使用Cloudflare DoH动态解析节点：dns.enable=true、nameserver=system、proxy-server-nameserver=https://cloudflare-dns.com/dns-query；未硬编码本机IP，不修改系统DNS。mihomo -t通过，mihomo-alpha.service以ubuntu运行，active/running且enabled，仅127.0.0.1:7897混合HTTP/SOCKS5监听，未开放公网控制器或TUN。

实际认证后验证结果：HTTP与SOCKS5经Cloudflare trace均返回出口216.247.110.57、loc=JP、TLSv1.3；币安现货/api/v3/time及合约/fapi/v1/time均HTTP200并返回serverTime。此结果高于此前仅TCP/TLS握手检测，证明本轮实际代理请求成功，不代表长期稳定性承诺。

严格上游证书检查得到SSLCertVerificationError，因此保留用户原profile的skip-cert-verify=true，未宣称节点证书经过严格认证。若后续需要强制证书认证，应让提供方提供可验证证书/正确SNI，不能凭连接成功掩盖该限制。

alpha/config.json的proxy仍为null，程序和四类行情目录保持；本轮没有启动/重启alpha、交易或原生产实例。若应用需要走代理，应由维护者显式配置本机http://127.0.0.1:7897；本次不自动接管应用代理。

维护：systemctl status mihomo-alpha.service；sudo systemctl restart/stop mihomo-alpha.service。配置/home/ubuntu/.config/alpha-proxy/config.yaml；运行目录/home/ubuntu/.local/share/alpha-proxy；配置检查日志/home/ubuntu/alpha-deploy-logs/mihomo-ntt-config-check.log。

**下方卸载、台湾节点失败记录均为此前历史，以本条为当前状态。**

## 当前状态：用户取消，测试服务器VPN安装已卸载

用户明确要求“尝试失败，删除mihomo等vpn程序的安装”，已停止并禁用本轮mihomo-alpha.service，删除/usr/local/bin/mihomo、systemd单元和自启动链接，以及本轮私有alpha-proxy配置目录、运行/cache目录与三份专用诊断日志。配置目录内的节点密码和主/BGP2候选配置一并删除；没有安装electron-ssr或桌面组件。

卸载后实际验证：systemd LoadState=not-found、ActiveState=inactive；无mihomo进程，7897与临时17997端口均无监听，二进制/单元/自启动链接/私有目录均不存在。alpha程序、alpha311和四个已迁移行情目录保留；未改动本机原有Clash或生产服务器。

最后排查事实：用户要求本机解析后，现有Clash命名管道DNS接口可取得主线路和BGP2真实IPv4，公共DNS的NXDOMAIN不能推断线路本身失效。本机连接这两条上游端口可达；测试服务器BGP2使用真实IP仍TCP超时，临时HTTP测试未通过，临时进程已退出，未将正式7897切换至BGP2。用户随后取消，后续不再继续VPN安装/解析/连通性尝试。

**以下均为历史过程，不代表当前仍有VPN服务或配置。**

## 2026-11-03 公共DNS获授权后的结果：主线路CNAME不存在

用户明确允许将所选节点域名提交Google/Cloudflare查询，不发送密码。测试服务器两者均返回DNS Status=3（NXDOMAIN）、A记录0；本机直接向Google复核新订阅同名节点，返回一条CNAME但最终无A记录。没有因此更改系统DNS或硬编码猜测IP。

进一步只直连原订阅提供方、使用Clash输出格式，成功取得127个实际节点。其主线路配置与用户本机profile的全部字段一致，没有可替换的新主线路地址；SS/SSR输出格式仅返回套餐提示不代表Clash订阅完全无节点。完整订阅和凭据不入本文/Git，不使用第三方转换器或此前被拒绝的本机7897代理补取。

当前根因是主线路的CNAME落点公开DNS不可解析，不是测试机单一系统解析器设置。服务仍已enable/active且仅本机7897监听，但HTTP/SOCKS5台湾出站尚未通过，不能宣称代理可用。已询问用户提供主线路真实IP，或授权检查BGP2备用线路并仅在通过验证后切换；在答复前不切其他节点，不改alpha或系统全局代理。前文公共DNS“未执行”是授权前历史状态，以本条为准。

## 2026-11-03 后续：已导入主节点，出站被DNS阻塞

用户明确提供本机Clash profile作为替代来源。仅提取其中“上海电信转台湾BGP[M][Trojan][倍率:1]”主节点（不含BGP2或其他节点），协议Trojan、端口65304，保留原节点连接/TLS设置；与用户手填SS参数不同，按其授权的profile方案使用。配置写入/home/ubuntu/.config/alpha-proxy/config.yaml、权限600；本地临时私有配置上传后已删除，无节点密码入Git或输出。

mihomo -t通过，mihomo-alpha.service已enable并启动，且仅127.0.0.1:7897监听。但HTTP出站实际失败，日志明确为节点域名dns resolve failed；系统resolvectl报节点域名不存在。本机系统解析返回Clash 198.18/15虚拟IP，不能搬至服务器；profile没有hosts映射，也无该端口的本地已有代理连接可提取真实地址。尚未验证台湾出口或SOCKS5可用，不能宣称代理开通成功。

Google/Cloudflare公共DNS诊断被自动审批拒绝，未执行，理由为向未明确授权第三方披露节点域名；已询问用户是否允许仅查询该域名（不发密码），或提供真实节点IP。等待答复期间不改解析路径、不切换BGP2、不修改alpha配置或系统全局代理。下方“尚未启用”是上一阶段历史状态，以本条为准。

## 用户确认的范围

目标 `ubuntu@43.173.122.28`；用户最初指定electron-ssr，检查发现无图形桌面/显示服务后，用户明确选择Mihomo无界面服务。期望节点“上海电信转台湾BGP”，监听服务器本机127.0.0.1:7897，HTTP/SOCKS5混合端口，不开放公网、不修改alpha/config.json或系统全局代理。

## 已完成

- 从官方MetaCubeX/mihomo安装v1.19.33 Linux amd64 compatible版，发行方提供的二进制摘要已核验，`mihomo -v`运行成功。
- 可执行文件 `/usr/local/bin/mihomo`。
- 机器私有配置目录 `/home/ubuntu/.config/alpha-proxy`，运行目录 `/home/ubuntu/.local/share/alpha-proxy`；目录700，不把订阅URL或节点密码写进Git/日志。
- systemd服务 `/etc/systemd/system/mihomo-alpha.service`，ubuntu非root运行，引用私有config.yaml；systemd-analyze verify退出0。检查时有云厂商既有tat_agent.service旧PIDFile路径提示，与本服务无关，未修改它。

## 尚未启用的原因

用户给定订阅在测试服务器直连（浏览器/Clash客户端标识）返回HTTP403；本地显式禁用所有代理直连返回200，但仅包含“剩余流量”和“过期时间”两条SSR提示，没有任何实际目标节点；Mihomo/Shadowrocket标识结果相同。未把这两条提示伪装成可用节点。

尝试通过本机已有7897代理补取订阅的诊断被自动审批拒绝，未执行；后续仅做直连，不通过第三方订阅转换服务。订阅完整URL和凭据不记录于本文。

**当前服务inactive、未enable，7897没有监听；尚未完成HTTP/SOCKS5代理连通性验收。** 已请求用户提供包含实际节点的Clash/Mihomo订阅，或目标节点的ss://、ssr://导出链接。拿到后才能写入0600私有config.yaml、执行mihomo -t、启动服务并验证出口。未修改/启动alpha、交易或原生产服务器服务。

## 有效配置准备后的维护命令

```bash
/usr/local/bin/mihomo -t -d /home/ubuntu/.local/share/alpha-proxy -f /home/ubuntu/.config/alpha-proxy/config.yaml
sudo systemctl enable --now mihomo-alpha.service
systemctl status mihomo-alpha.service
curl --proxy http://127.0.0.1:7897 --max-time 15 https://www.cloudflare.com/cdn-cgi/trace
curl --socks5-hostname 127.0.0.1:7897 --max-time 15 https://www.cloudflare.com/cdn-cgi/trace
```

上面的启动命令本轮未执行；config.yaml待有效节点后生成。配置监听仅loopback，allow-lan=false；Mihomo无需系统管理员网络能力或TUN模式。节点名代表服务商提供的线路标签，不把它当作已验证的电信/BGP物理路由承诺。

参考官方文档：[SSR配置](https://wiki.metacubex.one/en/config/proxies/ssr/)、[混合端口](https://wiki.metacubex.one/en/config/inbound/port/)、[systemd服务](https://wiki.metacubex.one/en/startup/service/)。
