---
name: tencent-cloud-cdn
description: Front a Cloudflare R2 custom domain with Tencent Cloud CDN to accelerate HLS playback, and diagnose 423 request loops, edge node selection, and cache misses. Use when asked to accelerate R2 media through Tencent Cloud CDN. Not for Mux or Cloudflare Stream.
---

# 腾讯云 CDN 加速 Cloudflare R2（HLS 播放域名）

把 R2 上的 HLS 通过腾讯云 CDN（香港/境外节点）对外加速。R2 侧只作为**源站**，不直接对外播放。

本文以本项目的实际域名为例：

| 角色 | 域名 |
|---|---|
| CDN 加速域名（对外播放） | `video.laybackzzz.com` |
| R2 自定义域名（源站） | `media.laybackzzz.com` |

## 核心原则（先读，否则一定踩坑）

1. **腾讯 CDN 只对「加速域名」生效**。请求 `media.laybackzzz.com` 等于直连 Cloudflare/R2，**不经过腾讯 CDN**。想走 CDN，播放地址必须用加速域名。
2. **同一个域名不能既是 R2 自定义域名（CNAME→R2），又是腾讯 CDN 加速域名（CNAME→腾讯）**。二选一，所以「源站域名」和「播放域名」必须是两个不同的主机名。
3. **回源链路是 用户 → 腾讯 CDN → CF/R2**，中间**不需要**自建反向代理。只有在需要鉴权、路径改写、防盗链等逻辑时才在 VPS 上挂反代。

## 方案 A：播放域名用 video，回源到 R2（推荐）

### 1. 腾讯云 CDN（添加加速域名 `video.laybackzzz.com`）

| 配置项 | 值 |
|---|---|
| 加速域名 | `video.laybackzzz.com` |
| 加速区域 | 与套餐对应（香港 / 中国境外 / 全球） |
| 源站类型 | **自有源**（见文末「源站类型怎么选」） |
| 源站地址 | `media.laybackzzz.com` |
| 回源协议 | **HTTPS** |
| 回源端口 | `443` |
| 回源 HOST | **使用源站域名** → `media.laybackzzz.com` |

回源 HOST 是 423 的头号诱因：设成**加速域名**（`video.laybackzzz.com`）会让源站按该 Host 再次路由回 CDN，形成回环。**绝不能填加速域名，也不能填 `video.laybackzzz.com`。**

在「HTTPS 配置」里为 `video.laybackzzz.com` 部署证书。

### 2. Cloudflare DNS

| 类型 | 名称 | 内容 | 代理状态 |
|---|---|---|---|
| CNAME | `video` | 腾讯返回的 CNAME（形如 `video.laybackzzz.com.cdn.dnsv1.com`） | **仅 DNS（灰云）** |
| CNAME | `media` | 保持现状（R2 自定义域名） | 现状不动 |

`video` **必须灰云**。开着橙云代理会让 CF 与腾讯 CDN 互相回源，正是回环 423 的成因。

### 3. R2

不动。保持 bucket 通过 `media.laybackzzz.com` 公网可读，它只被 CDN 回源。

### 4. 应用侧（最容易漏的一步）

- 播放地址从 `media.laybackzzz.com` 改为 **`https://video.laybackzzz.com/...`**（统一 https，避免混合内容拦截）。
- master / multivariant playlist 内的分段引用必须用**相对路径**，这样会自动继承 video 域名。若写死了 `media.laybackzzz.com` 的绝对地址，改成相对路径。

## 方案 B：必须保留 `media.laybackzzz.com` 做播放域名

因为同一域名不能在 R2 自定义域名与 CDN 加速域名之间复用，需要整体替换：

1. 给 R2 新建源站用域名，例如 `origin.laybackzzz.com`，绑定到 bucket。
2. 腾讯 CDN **新增**加速域名 `media.laybackzzz.com`，源站地址 `origin.laybackzzz.com`，回源 HOST 设 `origin.laybackzzz.com`。
3. CF 删除 `media` 原有记录，改为 `media` → 腾讯 CNAME，灰云。
4. R2 控制台**解绑** `media.laybackzzz.com` 自定义域名，否则两边冲突。

## 验证

```bash
# 1. 源站是否正常（应 200，且无腾讯头）
curl -sI https://media.laybackzzz.com/HLS/<id>/H264/master.m3u8

# 2. 加速域名是否走通（应 200，且含 X-NWS-LOG-UUID = 腾讯 CDN 边缘标识）
curl -sI https://video.laybackzzz.com/HLS/<id>/H264/master.m3u8

# 3. 解析是否直达腾讯（应 CNAME .cdn.dnsv1.com → .intlscdn.com → 43.x 腾讯 IP）
dig +short video.laybackzzz.com @1.1.1.1
dig +short video.laybackzzz.com CNAME @1.1.1.1
```

响应头判读：

| 头 | 含义 |
|---|---|
| `X-NWS-LOG-UUID` | 腾讯云 CDN 边缘节点标识 —— **出现即代表走了腾讯 CDN** |
| `X-Cache-Lookup` | 腾讯边缘缓存结果 |
| `Server: cloudflare` / `cf-cache-status` / `CF-RAY` | 源站（media→R2）透传上来的，属正常 |

## 排错

### 423 Locked = 请求回环

腾讯把 423 定义为**回环**（CDN 回源时发现又绕回自己）。特征：`X-Cache-Lookup` 连续出现多次（正常只有 1 条）。

排查顺序：
1. 先直连源站 `curl -sI https://media.laybackzzz.com/...`：**只要源站 200，问题就在 CDN 回源配置**，与 R2 无关。
2. CF 上 `video` 是否灰云。
3. 源站地址是否误填成加速域名自己。
4. 回源 HOST 是否被设成了加速域名 → 改为「使用源站域名」。

### 缓存一直 Miss

三个原因，逐个排除：

1. **用了 `curl -I`（HEAD）**：多数 CDN 不缓存 HEAD，永远 Miss。测缓存必须用 **GET**：
   ```bash
   curl -s -o /dev/null -D - https://video.laybackzzz.com/<path>.m3u8 | grep -i x-cache
   ```
2. **DNS 轮询到不同节点**：`video` 解析出多个 IP，各节点独立缓存。用 `--resolve` 固定节点连打 3 次（预期第 1 次 Miss、之后 Hit）：
   ```bash
   for i in 1 2 3; do
     curl -s -o /dev/null -D - \
       --resolve video.laybackzzz.com:443:43.175.44.11 \
       https://video.laybackzzz.com/<path>.m3u8 | grep -i x-cache
   done
   ```
   ⚠️ `--resolve` 的端口必须与 URL 协议一致：`http://` 用 `:80`，`https://` 用 `:443`，否则该参数静默失效，请求仍走 DNS 轮询。
3. **CDN 没配缓存规则**：R2 响应里**没有 `Cache-Control`**，CDN 无依据不会缓存 `.m3u8`。在「缓存配置」按后缀配：

| 后缀 | 缓存时间 |
|---|---|
| `.m3u8` | 60 秒（或遵循源站） |
| `.ts` `.m4s` `.mp4` | 30 天 |
| `.vtt` `.srt` `.json` | 7 天 |
| `.key` | 不缓存 |

### 解析结果是 `198.18.x.x`

`198.18.0.0/15` 是 RFC 2544 网络基准测试保留段，不是任何真实 CDN IP。本机跑着 mihomo/Clash 的 **fake-ip** 模式时会返回这类伪造地址。绕过办法：

```bash
dig +short video.laybackzzz.com @1.1.1.1
# 若 53 端口也被 TUN 劫持，用 DoH：
curl -s "https://cloudflare-dns.com/dns-query?name=video.laybackzzz.com&type=CNAME" \
  -H "accept: application/dns-json"
```

### 节点不是香港（显示 EWR 等）

CDN 按**请求出口 IP、网络路由和调度策略**选节点，**不保证固定香港**；解析到匈牙利/美国节点不代表配置错误。本机 Clash/VPN 出口在美国时会调度到美国节点。临时关闭代理再测；最终以腾讯 CDN 控制台访问日志里的命中节点为准。

### 源站类型怎么选

| | 自有源 | 第三方对象存储 |
|---|---|---|
| 本质 | 手动填域名/IP 作源站 | 选厂商，CDN 内置该厂商回源规则 |
| 鉴权 | 不支持内置签名回源，源站须公网可读 | 支持私有桶（填 AK/SK，回源自动签名） |
| 灵活性 | 回源协议、HOST、端口、主备、权重均可配 | 受厂商预置限制 |

**R2 场景选「自有源」**：`media.laybackzzz.com` 本身就是公网可访问的 HTTPS 站点，直接回源即可。R2 不在腾讯的预置厂商列表里（那是 AWS S3 / 阿里 OSS / 华为 OBS / 七牛等），走「第三方对象存储」反而要额外处理 S3 endpoint 与签名，没必要。只有需要**私有桶 + CDN 带签名回源**时才用后者。

## 检查清单

- [ ] `video` 在 CF 上是**灰云**
- [ ] 源站地址 = `media.laybackzzz.com`（不是自己）
- [ ] 回源 HOST = 「使用源站域名」= `media.laybackzzz.com`
- [ ] 回源协议 HTTPS / 端口 443
- [ ] 已部署 `video.laybackzzz.com` 证书
- [ ] 按后缀配了缓存规则
- [ ] 播放地址与 playlist 内引用统一指向 `https://video.laybackzzz.com/`
