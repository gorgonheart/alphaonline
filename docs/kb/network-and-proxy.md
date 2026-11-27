# 网络代理与币安 IP 白名单速查

> 维护基准：`2026-11-21`。适用对象：测试机 `alphatest`（`43.173.122.28`）的 Mihomo 代理 `127.0.0.1:7897`，以及任何需要出网访问币安的进程。
> 目的：**网络排障时跳过"IP ↔ 节点"匹配过程，直接按本表测试连通性并切换。**
> 凭据策略：节点密码只存在于机器私有配置 `/home/ubuntu/.config/alpha-proxy/config.yaml`（权限 600），本文不记录任何密码/UUID。

---

## 1. 硬约束：交易与账户请求按出口 IP 绑定

交易程序和账户的签名请求（`/api/v3/*`、`/fapi/v1/*` 中带 `signature` 的接口、交易所 API Key 白名单）**按出口 IP 校验**，只接受以下 5 个 IP：

```
188.253.120.76
45.207.152.141
216.247.110.57
27.122.59.81
212.87.193.53
```

判断依据是**出口 IP**（经代理后对端看到的源地址），不是节点入口地址。切换节点前必须先确认其出口 IP 在白名单内，否则公共接口（`/ping`、`/time`、`/klines` 等无签名接口）仍可 200，但带签名的下单/查账户请求会被拒——**这是最容易误判的坑**。

出口 IP 核对命令：

```bash
curl -s -m 12 -x http://127.0.0.1:7897 https://api.ipify.org; echo
```

---

## 2. 节点 ↔ 出口 IP 对照表（2026-11-21 实测）

| 白名单 IP | 对应节点（订阅名） | 协议:端口 | 接入地址 | 实测出口 IP | 链路稳定性（2026-11-21 复测） | 结论 |
|---|---|---|---|---|---|---|
| **212.87.193.53** | **江苏联通转日本BGP[M][Trojan][倍率:0.8]** | trojan:65227 | `212.87.193.53`（原域名 `jmcuabgaa.arvexloria.site` 已不可解析） | 212.87.193.53 | TCP 40/40，RTT 101–103 ms；HTTP 20/20 | **AUTO 组 P1（当前在用）** |
| 216.247.110.57 | 日本NTT[M][AnyTLS][倍率:0.3] | anytls:65102 | `ascjp.ntt1.ccddn4.icu` | 216.247.110.57 | TCP 40/40，RTT 100–102 ms；HTTP 20/20 | **AUTO 组 P2 备选** |
| 45.207.152.141 | 香港BGP[M][AnyTLS][倍率:0.3] | anytls:65231 | `hk-bgp1.ccdnadns.cc` | 45.207.152.141 | TCP 40/40，RTT 143–158 ms；HTTP 20/20 | **AUTO 组 P3 备选** |
| 216.247.110.57 | 日本NTT2[M][AnyTLS][倍率:0.3] | anytls:65202 | `ascjp.ntt1.ccddn4.icu` | 216.247.110.57 | **TCP 26/40（65%），RTT 100–2137 ms** | **不稳定，勿用**（同日曾启用 40 分钟后回滚，见 §6 事故记录） |
| 188.253.120.76 | 江苏联通转日本NTT3[M][Trojan][倍率:0.8] | trojan:65202 | `188.253.120.76` | 188.253.120.76 | 8/8 成功，1613 ms | 可用但慢，仅兜底 |
| 27.122.59.81 | 上海联通转新加坡BGP2[M][Trojan][倍率:0.9] | trojan:65301 | `27.122.59.81` | 27.122.59.81 | 8/8 成功，1198 ms | 可用但慢，仅兜底 |
| 27.122.59.81 | 上海联通转美国AN2[M][Trojan][倍率:0.8] | trojan:65522 | `27.122.59.81` | 27.122.59.81 | 8/8 成功，1199 ms | 可用但慢，仅兜底 |
| ~~非白名单~~ | 日本BGP2[M][AnyTLS][倍率:0.6] | anytls:63502 | `ascjp.bgp1.ccddn4.icu` | **212.87.193.62** | 8/8 成功，441 ms | **出口 .62 不在白名单，禁止用于交易** |

> ⚠️ **评估节点必须用长样本**。同一天用「8 轮 K 线」短样本评估时，65202 表现为 8/8 优于 65102 的 6/8，据此切换后生产端 40 分钟内连续报错；改为 40 次 TCP 分层拨号后结论完全反转（65202 仅 65%）。**短样本 + 单一时间窗会给出错误的稳定性排序**，判定标准见第 4 节「分层判定」。

补充事实：

- **一个 IP 可对应多个节点**。`216.247.110.57` 同时承载 日本NTT(65102) 与 日本NTT2(65202)；`188.253.120.76`、`27.122.59.81`、`212.87.193.53` 是联通/专线那一族节点的**共用落地前端**，同一 IP 上开放多个节点端口（如 `188.253.120.76` 开放 65202/65332/65503/65521/65522）。
- 联通中转族（`*联通转*`、`*专线转*`）是**透传**：出口 IP = 入口 IP，因此它们天然满足白名单。
- 日本NTT / 日本NTT2 / 香港BGP 也是直出：出口 IP = 节点 IP。

### 自动故障切换组（`AUTO`，2026-11-21 11:23 起）

配置里已不再是"单节点 + `MATCH,<节点名>`"，而是 `fallback` 组，三个成员的出口 IP 全部在白名单内：

| 优先级 | 节点 | 协议:端口 | 出口 IP | 成员角色 |
|---|---|---|---|---|
| P1 | 江苏联通转日本BGP[M][Trojan][倍率:0.8] | trojan:65227 | 212.87.193.53 | 首选，日常走这条 |
| P2 | 日本NTT[M][AnyTLS][倍率:0.3] | anytls:65102 | 216.247.110.57 | 首选失联时接管 |
| P3 | 香港BGP[M][AnyTLS][倍率:0.3] | anytls:65231 | 45.207.152.141 | 前两条都失联时兜底 |

```json
"proxy-groups": [{
  "name": "AUTO", "type": "fallback",
  "proxies": ["江苏联通转日本BGP...", "日本NTT...", "香港BGP..."],
  "url": "https://www.gstatic.com/generate_204",
  "interval": 10, "timeout": 2000, "max-failed-times": 1, "lazy": false
}]
"rules": ["MATCH,AUTO"]
```

**实测切换时序**（用 `iptables -I OUTPUT -d <节点IP> -j DROP` 注入故障，测完立即 `-D` 撤销）：

| 场景 | 首次可用出口 | 耗时 |
|---|---|---|
| DROP P1 | 216.247.110.57（P2） | **约 10 s** |
| DROP P1 + P2 | 45.207.152.141（P3） | **约 16 s** |
| 撤销 DROP 后回切 | 212.87.193.53（P1） | **约 10 s** |

⚠️ **mihomo 的 `fallback` 组不做"请求内重试"**：实测 P1 拨号失败时，组直接把错误返回，不会在同一次请求里改试 P2；只有健康检查判死后，**后续**请求才走新节点。因此单次故障窗口内的那一个请求仍会失败（5 s 拨号超时），这正是生产端重试预算（`async_retry_getter`：5 次、间隔 1→2→4→8 s，合计约 15 s）能覆盖的范围——**不要把 `interval` 调大**，否则窗口会超出重试预算。

`interval` 10 s 意味着每节点每分钟约 6 次健康检查，三条节点合计约 18 次/分钟的 `generate_204` 请求，可忽略。

### 市值 / Data API 专用分流组（`QUANT`，2026-11-22 23:45 起）

起因：2026-11-22 08:45 起测试机市值更新连续失败（见 §6.3）。实测 `api.quantclass.cn`（UCloud `106.75.252.187`）从 **P1 出口不可达**——443 TLS 被重置、80 返回 0 字节 502，两者耗时都固定约 `7.4 s`（中转层 connect 超时），而同节点访问百度 / 币安 / `qtcls.jpn-tky.ufileos.com` 全部 200。属于"节点 → 该目标"的问题，既不是节点失联，也不是代理协议问题（HTTP CONNECT 与 SOCKS5 结果完全一致）。

因此新增只含 P2/P3 的 `QUANT` 组，把 quantclass 链路从交易链路中剥离：

```json
"proxy-groups": [
  {"name": "AUTO", "...": "P1 → P2 → P3（交易/行情）"},
  {"name": "QUANT", "type": "fallback",
   "proxies": ["日本NTT...", "香港BGP..."],
   "url": "https://www.gstatic.com/generate_204",
   "interval": 10, "timeout": 2000, "max-failed-times": 1, "lazy": false}
],
"rules": ["DOMAIN-KEYWORD,quantclass,QUANT", "MATCH,AUTO"]
```

- 用 `DOMAIN-KEYWORD,quantclass` 而非 `DOMAIN-SUFFIX,quantclass.cn`：市值链路有三个主机——`api.quantclass.cn`、`quantclass.cn-sh2.ufileos.com`（UCloud 桶名含 quantclass 但不以 `quantclass.cn` 结尾）、下载域名 `tencent-cos.quantclass.cn`，只有 keyword 能全覆盖。
- `QUANT` 组**刻意不含 P1**：P1 对该目标是硬不可达，留作兜底只会把本来能成功的请求变成 502。
- 交易 / 行情仍走 `AUTO`（P1 优先），两条链路互不拖累；`quantclass` 流量计费倍率由 `0.8` 降为 `0.3`。

---

## 3. 已知坑（排障前先读）

1. **`*.arvexloria.site` 域名解析已断**。`jmcuabgaa.arvexloria.site` → CNAME `jucuraui.ccatnet.com`，权威 NS（`vip7.alidns.com`）返回 **NXDOMAIN**；8.8.8.8 / 1.1.1.1 / 223.5.5.5 / 119.29.29.29 与阿里 DoH 全部无 A 记录。**所有使用该域名的 64 个联通/专线节点按域名配置必然连接失败**，必须把 `server` 直接改写成上表 IP，并保留 `skip-cert-verify: true`。后端 IP 本身是活的（实测 8/8 成功）。
2. **`skip-cert-verify: true` 是必需项**。日本NTT 系列对上游做严格证书校验会抛 `SSLCertVerificationError`；节点方未提供可验证证书，故保留跳过校验，不代表证书经过认证。
3. **节点入口 IP ≠ 出口 IP 时以出口为准**。判断"能不能用于交易"只看 `api.ipify.org` 的返回值。
4. **重启 `mihomo-alpha` 会掐断在途连接**。若 `realtime_data` 正在拉历史 K 线，会在下一次请求处失败/重试；尽量避开重建窗口，或重启后确认生产者仍在推进。
5. 节点名里的"倍率"是服务商计费倍率，不是路由质量承诺。
6. **同一 IP 的不同端口稳定性可能天差地别**。`216.247.110.57` 的 65102 稳定 40/40，65202 只有 26/40——不要因为"同一台机"就认为可以随便换端口。生产端报 `ConnectionResetError` 时，第一件事是查 mihomo 的 `failed to create session` 日志，而不是怀疑币安。
7. **`fallback` 组只保证"下一次请求"能用备用节点，不保证"当前请求"成功**。故障切换依赖健康检查周期（现为 10 s），单次请求内不会自动改试其他成员，详见 §2 自动切换组的说明。
8. **生产端重试预算只有约 15 s**（`core/utils/network.py::async_retry_getter`：5 次、间隔 1→2→4→8 s）。节点抖动超过 15 s 就会抛 `剩余 0 次重试机会`。若日后仍频繁出现硬失败，可评估把这个预算拉到 ~60 s（属上游生产者代码改动，需单独授权与验证）。
9. **`proxy-groups` 改动必须重启才生效**。本机未启用 `external-controller`，无法热重载，也没有 API 可查组状态；判断当前实际走哪条节点，用 `curl .../api.ipify.org` 得到的出口 IP 反查 §2 表格。
10. **健康检查通过不代表业务目标可达**。`AUTO`/`QUANT` 的健康检查是 `gstatic/generate_204`，只能证明"节点 → 互联网"通。P1 全程健康检查通过、币安也正常，但到 `api.quantclass.cn` 是 443 重置 / 80 空 502（2026-11-22，见 §6.3）。**特定目标报错时要单独探该目标，不要用"节点健康"下结论**；多个业务目标分属不同线路时，应拆独立 `proxy-group` + `DOMAIN-KEYWORD` 规则，而不是只调一个 `AUTO` 的顺序。

---

## 4. 排障 SOP（跳过匹配，直接测试）

```bash
# 1) 看组结构与成员优先级
python3 -c "
import json;d=json.load(open('/home/ubuntu/.config/alpha-proxy/config.yaml'))
g=d['proxy-groups'][0]
print('group:',g['name'],g['type'],'interval=',g['interval'])
for i,p in enumerate(d['proxies'],1): print(f'  P{i} {p[\"name\"]} {p[\"server\"]}:{p[\"port\"]}')
print('rules:',d['rules'])
"

# 2) 连通性（无签名接口，快速判断链路）
curl -s -o /dev/null -w 'spot=%{http_code}/%{time_total}s\n' -x http://127.0.0.1:7897 -m 12 https://api.binance.com/api/v3/ping
curl -s -o /dev/null -w 'fapi=%{http_code}/%{time_total}s\n' -x http://127.0.0.1:7897 -m 12 https://fapi.binance.com/fapi/v1/ping

# 3) 出口 IP 是否在白名单（决定能否下签名单）→ 同时也能反查当前走哪条节点
curl -s -m 12 -x http://127.0.0.1:7897 https://api.ipify.org; echo

# 4) 当前实际建连的节点端口
ss -tnp | grep mihomo | head -3
```

当前出口 IP ↔ 节点对照（用于步骤 3 反查）：`212.87.193.53` = P1 江苏联通转日本BGP（正常态）、`216.247.110.57` = P2 日本NTT（P1 故障中）、`45.207.152.141` = P3 香港BGP（P1/P2 均故障中）。**出现 P2/P3 说明组已自动切换，此时应查 P1 链路为什么不健康，而不是继续排币安。**

判定顺序：**步骤 3 的出口 IP 不在白名单 → 立刻换节点**（不必再看 2 的结果，公共接口 200 具有欺骗性）。步骤 2 全绿且出口 IP 合规则链路正常，问题在上游交易所或账号侧，不要再折腾代理。

### 分层判定（定位坏在哪一层）

生产端报 `ClientConnectorError` / `ConnectionResetError` 时，先看 mihomo 日志里有没有 `failed to create session: dial tcp <节点IP>:<端口>: i/o timeout`——有就是**代理到节点的 TCP 层**问题，与币安、与交易所限流都无关：

```bash
sudo journalctl -u mihomo-alpha --since '-40 min' --no-pager | grep 'failed to create session' | tail -10
```

三层依次确认，每层都要**多次采样**（≥20 次，短样本会误判）：

| 层 | 命令 | 通过标准 |
|---|---|---|
| L1 节点可达 | `python3` 直接 `socket.connect((节点IP, 端口))` × 40，间隔 0.4 s | 成功 ≥ 39/40，RTT 抖动 < 20% |
| L2 经代理取数 | `curl -x http://127.0.0.1:7897 .../fapi/v1/ping` × 20 | 20/20 且单次 < 1 s |
| L3 出口白名单 | `curl -x ... https://api.ipify.org` | 命中 5 个白名单 IP |

L1 不过 → 换节点（会话建立都失败，L2/L3 无意义）。L1 过、L2 不过 → 查协议/密码/SNI，或节点侧限流。L1/L2 都过、L3 不过 → 该节点出口不在白名单，**禁止用于交易**。

**不要用并发数去判断节点承载能力**：2026-11-21 实测中并发 1 和 4 也失败、并发 32 却全成功，抖动是时间随机的，与并发无关。

### 调整优先级 / 增删成员（`AUTO` 组）

日常不需要再"换节点"，只需调 `proxy-groups[0].proxies` 的顺序（顺序即 `fallback` 优先级），或增删成员。**三个成员必须都在白名单内**，否则故障切换后会出现"公共接口 200、签名请求被拒"。

```bash
C=/home/ubuntu/.config/alpha-proxy/config.yaml
cp -a $C $C.bak-$(date +%Y%m%d-%H%M)
python3 - <<'PY'
import json
p="/home/ubuntu/.config/alpha-proxy/config.yaml"
d=json.load(open(p))
order=["日本NTT[M][AnyTLS][倍率:0.3]","江苏联通转日本BGP[M][Trojan][倍率:0.8]","香港BGP[M][AnyTLS][倍率:0.3]"]
have={x["name"] for x in d["proxies"]}
d["proxy-groups"][0]["proxies"]=[n for n in order if n in have]
json.dump(d,open(p,"w"),ensure_ascii=False,indent=2)
print("new order:",d["proxy-groups"][0]["proxies"])
PY
mkdir -p /tmp/vt && /usr/local/bin/mihomo -d /tmp/vt -f $C -t; rm -rf /tmp/vt   # 必须先验配置
sudo systemctl restart mihomo-alpha && sleep 5 && systemctl is-active mihomo-alpha
curl -s -m 12 -x http://127.0.0.1:7897 https://api.ipify.org; echo                # 确认出口符合预期
```

### 验证自动切换是否有效（可重复执行，10 秒内可回滚）

用防火墙临时屏蔽节点 IP 制造故障，观察出口 IP 是否自动跳档：

```bash
sudo iptables -I OUTPUT -d 212.87.193.53 -j DROP            # 屏蔽 P1
sleep 12; curl -s -m 8 -x http://127.0.0.1:7897 https://api.ipify.org; echo   # 期望 216.247.110.57
sudo iptables -D OUTPUT -d 212.87.193.53 -j DROP            # 立刻撤销
sudo iptables -C OUTPUT -d 212.87.193.53 -j DROP && echo "STILL-EXISTS(必须处理)" || echo clean
```

**撤销后务必用 `iptables -C` 复查规则已删除**，否则该节点会一直不可用（虽然是备用节点接管，但等于降级运行）。

### 回滚

```bash
sudo cp /home/ubuntu/.config/alpha-proxy/config.yaml.bak-<时间戳> /home/ubuntu/.config/alpha-proxy/config.yaml
sudo systemctl restart mihomo-alpha
```

当前备份：

- `config.yaml.bak-20261121-ntt2-broken`（**当前有效回滚点**：切换到不稳定的 65202 之前的 日本NTT 65102 版本）
- `config.yaml.bak-20261121-112326-fallback`（**当前有效回滚点**：改成 `AUTO` 组之前的单节点 日本NTT 65102 版本）
- `config.yaml.bak-20261121-ntt2-broken`（切换到不稳定的 65202 之前的 日本NTT 65102 版本）
- `config.yaml.bak-20261121-ntt2`（最初切换前的 日本NTT 65102 版本，内容同上）

---

## 5. 服务维护

| 项 | 值 |
|---|---|
| 服务 | `mihomo-alpha.service`（ubuntu 用户，非 root，enable 常驻） |
| 二进制 | `/usr/local/bin/mihomo`（Mihomo Meta v1.19.33，go1.26.10） |
| 配置 | `/home/ubuntu/.config/alpha-proxy/config.yaml`（600） |
| 运行目录 | `/home/ubuntu/.local/share/alpha-proxy` |
| 监听 | `127.0.0.1:7897`（mixed HTTP/SOCKS5，`allow-lan=false`，无 TUN、无公网控制器） |
| 当前出网 | `AUTO` fallback 组：P1 江苏联通转日本BGP(`212.87.193.53:65227`) → P2 日本NTT(`216.247.110.57:65102`) → P3 香港BGP(`45.207.152.141:65231`)，健康检查 10 s |
| 分流 | `DOMAIN-KEYWORD,quantclass` → `QUANT` 组（P2 日本NTT → P3 香港BGP），其余 `MATCH,AUTO`；2026-11-22 23:45 起 |
| 当前出口 | 212.87.193.53（中国大陆联通中转，`loc=JP` 落地）；quantclass 链路为 216.247.110.57 |

历史沿革与安装细节见 [2026-11-03 测试服务器 Mihomo 本机代理](../2026-11-03-test-server-proxy.md)。

---

## 6. 事故记录

### 6.1 2026-11-21 切换到 65202 导致生产端连续报错

| 项 | 内容 |
|---|---|
| 时间线 | `09:51` 切换到 日本NTT2(65202) → `10:25:54` / `10:26:08` 生产端报 `ClientConnectorError(... ConnectionResetError)` → `10:33` 回滚到 65102 |
| 表象 | 日志显示 `api.binance.com` / `fapi.binance.com` 连接被重置，请求重试耗尽；容易误判为币安故障或交易所限流 |
| 真实原因 | mihomo 日志 `failed to create session: dial tcp 216.247.110.57:65202: i/o timeout` / `context deadline exceeded` —— 连节点本身握手失败，aiohttp 将其包装为 `ClientConnectorError(ConnectionResetError())` |
| 判定依据 | 同 IP 两端口 40 次 TCP 拨号：`65102` 40/40（100–102 ms）、`65202` **26/40（65%）**，且出现 2137 ms 离群值 |
| 误判来源 | 切前用「8 轮 K 线」短样本评估，65202 恰好落在正常窗口（8/8），65102 有 2 次抖动（6/8），据此得到错误排序 |
| 恢复 | 回滚 65102 → `mihomo -t` 通过 → 重启服务 → HTTP 20/20 全通过 → 建连确认走 65102；生产端自行重试恢复（`10:32` 资金费推进 280/767） |
| 结论 | **同机不同端口的稳定性可能完全不同**；端口级稳定性必须用 ≥40 次 TCP 拨号长样本判定，不能只看 HTTP 峰值窗口 |

### 6.2 2026-11-21 11:12 首次初始化单次失败（单节点时期的固有抖动）

| 项 | 内容 |
|---|---|
| 时间线 | `11:12:34` 资金费下载完成（49.51 min）→ `11:12:35` `首次初始化: ClientConnectorError` → `11:13:37` 重试成功，正常取到 spot 520 / swap 767 |
| 影响 | 仅延迟 62 s，无数据损失 |
| 判定过程 | 同期 ICMP 120 包 **0% 丢包**、100.5 ms（mdev 0.037）；顺序 TCP 拨号 60/60；并发 10/20/40/80 全通过；经代理取数 5/5 → 排除线路、并发、节点不可达 |
| 真实原因 | 节点侧周期性 1–2 s 拒收/重置窗口。mihomo 全天记录成对出现：`10:36:59 ×2`、`10:42:04 ×2`、`10:42:29`、`10:52:09 ×2`、`10:52:34`，约每 5–10 分钟一次；11:12 那次未留 dial 日志，属隧道建立后被重置 |
| 暴露的短板 | 单节点配置下，抖动窗口 > 重试预算（约 15 s）即硬失败；`10:25` 那次就是 `剩余 0 次重试机会` |
| 处置 | 改为 `AUTO` fallback 三节点组，健康检查 10 s，实测 10–16 s 完成跳档并自动回切（见 §2） |

### 6.3 2026-11-22 测试机市值更新连续失败约 6 小时（P1 → `api.quantclass.cn` 不可达）

| 项 | 内容 |
|---|---|
| 时间线 | 最后成功 `08:15`（此时数据仍停 `2026-11-20`）→ `08:45` 起每 10 分钟 `❌ 更新市值数据发生错误, 跳过本周期`，持续到 `23:25`，约 40 次失败 |
| 表象 | `ClientConnectorError(... ConnectionResetError)` + `SSL_ERROR_SYSCALL in connection to api.quantclass.cn:443`，报错中带 `proxy=URL('http://127.0.0.1:7897')` |
| 真实原因 | `AUTO` 组当时选中 P1（`212.87.193.53`）。P1 到 `106.75.252.187`：443 被重置、80 返回 **0 字节 502**，两者耗时均约 `7.4 s`（中转层 connect 超时，非源站响应）；同节点访问百度 / 币安 / `qtcls.jpn-tky.ufileos.com` 全 200，`www.quantclass.cn` 也 200 → 只有该 API 线路不通 |
| 为什么没自动跳档 | 健康检查是 `gstatic/generate_204`，P1 对它完全正常，组始终判定健康 |
| 排除项 | ① 非协议问题：`http` CONNECT 与 `socks5h` 结果完全一致；② 非节点失联：同节点其他目标全通；③ 非 API key 失效：不带 key 探测时 P2/P3 是 302/500（TLS+HTTP 已通），P1 连 TLS 都建不起来；④ 非 DNS：测试机与正式机解析同为 `106.75.252.187` |
| 影响 | 测试机 `data/coin_cap` 停在 `2026-11-20`（缺 `11-21` 一天），市值因子沿用前值填充 `circulating_supply`；测试机交易端日志无市值报错、轮次 `status=ok`；正式机 `alpha2` 为**直连**（`config.json` 无 proxy / `coin_cap_use_proxy`），当日 141 次成功、0 次失败 |
| 处置 | 新增 `QUANT` 分流组（见 §2）；`mihomo -t` 通过 → `systemctl restart mihomo-alpha` → `api.quantclass.cn` **302**、`quantclass.cn-sh2.ufileos.com` **200**、`jpn-tky.ufileos.com` **200**、币安 **200** |
| 补数 | 无需手工上传：`coin_cap_update_to_recent` 按 `date_range(max_date, product.update_time, 左闭右开)` 逐日回补。`product_infos.update_time = 2026-11-22`、库内 max = `2026-11-20` → 自动补 `11-21` 一天（换节点后已用真实 key 验证可拉到 `830 行 / 828 币种` 且含 `circulating_supply`） |
| 遗留短板 | 失败被 `core/coin_cap.py::run_coin_cap_update_to_recent` 的 `except` 吞掉（只 `logger.exception` + 返回 `False`），不重试、不告警 → **静默 6 小时**。正式机虽是直连，但一旦被目标封 IP 会复现同样的静默断档，建议后续接 `error_webhook_url` 告警并加重试（生产端代码改动，需单独授权） |

---

## 7. 本文数据的取得方式（供复核）

1. 订阅 profile 中 41 个 `server` 全部为域名 → 在测试机上用 4 个公共解析器 + 阿里 DoH 逐个解析，得到 `216.247.110.57`（`ascjp.ntt1`）与 `45.207.152.141`（`hk-bgp1`）两处直接命中。
2. 其余 3 个 IP 用**全量 83 个节点端口并发探测**回查：这些 IP 上开放的端口全部命中联通/专线族节点定义，确认其为该族共用前端。
3. 每个候选节点用临时 Mihomo 实例（独立 17890 端口，不影响生产 7897）逐一实测：K 线 500 根 × 8 轮成功率与延迟、`api.ipify.org` 出口 IP。
4. 稳定性复测（2026-11-21 10:3x）：对 4 个候选目标各做 40 次 `socket.connect` 分层拨号，间隔 0.4 s；生产节点另做 20 次经代理 HTTP 取数。
5. 故障切换实测（2026-11-21 11:2x）：`iptables -I OUTPUT -d <节点IP> -j DROP` 注入，逐级叠加 P1→P1+P2，记录出口 IP 变化与耗时，测后立即 `-D` 撤销并用 `iptables -C` 复查。
6. 临时实例与含凭据的临时配置在测试结束后已删除，生产 `mihomo-alpha` 全程未被临时实例影响。

**下次排障可跳过步骤 1–2**，直接查第 2 节表格并按第 4 节 SOP 执行。
