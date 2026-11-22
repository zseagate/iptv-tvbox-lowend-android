# MacCMS 点播源选型（低版本盒子友好度）

> 只记录**选型标准与评测结论**，不含完整源清单/播放地址（源是公开聚合接口，自行确认可用性）。

## 选型三问

1. **API 合法**：`GET <baseUrl>` 返回 MacCMS JSON（含 `list`/`class`），状态 200。
2. **直链优先**：播放地址是 `https://<cdn>/.../index.m3u8`（**直链**）优于 `.../share/<hash>`（解析页）。
   - 直链：播放器直接拉 m3u8，无需解析。
   - `/share/`：是老站"防直链"跳转页，老盒子的系统播放器常二次请求失败。
3. **TLS 兼容**：播放域名至少能协商 **TLS 1.2 且老盒子握手组合**（`ECDHE-RSA-AES128-GCM-SHA256` 这类）。
   Android 4.4 自带 OpenSSL 1.0.1 只对部分 TLS1.2 密码套件有支持，`openssl s_client -tls1_2` 能通 ≠ 盒上必通，**最终以盒上实测为准**。

## 实测参考（某网络环境，2026-11）

| 名称 | baseUrl | 条数 | 播放地址类型 | 老盒友好 |
|---|---|---|---|---|
| 暴风资源 | `https://bfzyapi.com/api.php/provide/vod/` | ~17万 | **直链 index.m3u8** | ★★★（首选） |
| 量子资源 | `https://cj.lziapi.com/api.php/provide/vod/` | ~16万 | `/share/` 解析页 | ★★（部分能播） |
| 索尼资源 | `https://suoniapi.com/api.php/provide/vod/` | ~15万 | `/share/` 解析页 | ★★ |

> 已停/域名失效（勿用，仅记录）：`api.bdzybf.com`、`caiji.zhao1.top`、`api.kunyu77.com`、`www.tycjzy.com`、`api.wcyy.tv`、`api.hzyua.com`、`api.jyzy.com`、`vip.kuaiche.com`。

## 为什么 TVBox4.1+ 而不是 Q 版跑点播

- Q 版（q215613905 分支）用**系统 TLS**，拉 `https://bfzyapi.com` 这类配置直接
  `SSLHandshakeException`（`ssl3 alert handshake failure`）→ 点播根本连不上。
- TVBox4.1+（`com.tvbox.android44`，2026 构建）自带较新 SSL，HTTPS 源可用。
- 所以：**点播 = TVBox4.1+（MacCMS 源），直播 = Q 版（本地文件）**，各取所长。
