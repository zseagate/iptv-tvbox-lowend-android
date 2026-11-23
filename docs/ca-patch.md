# 给 Android 4.4 盒子补现代根 CA 证书

## 问题

Android 4.4（API 19）系统 CA 库 `/system/etc/security/cacerts/` 固化在 2013-2014 年，**不含 2015 年后签发的新根证书**：

- Let's Encrypt（ISRG Root X1/X2，2015+）
- Google Trust Services（GTS Root R1/R3/R4）
- GlobalSign Root R46/E46、DigiCert 新根、Amazon Root CA 等

## 现象与根因

VLC 等**自带 TLS 栈**的播放器能完成 TLS1.2 握手（不受系统旧 TLS 限制），但**证书校验仍走系统旧 CA 库**。对"新根证书"站点：

```
libvlc tls client: TLS handshake: Success.
libvlc tls client: Certificate verification failure: The certificate is NOT trusted. The certificate issuer is unknown.
```

VLC 弹出证书警告框（只有 ABORT / VIEW CERTIFICATE，**无"继续"选项**），等于拒播。

注意区分两个层面：
| 层面 | 谁负责 | 老盒子问题 |
|---|---|---|
| TLS 协议版本 | 系统栈（TVBox 等） | 太旧（OpenSSL 1.0.1）→ 握手失败 |
| 证书信任 | 系统 CA 库 | 太旧 → 新根不被信任 |

VLC 自带 TLS1.2，协议层能过；但证书层仍受系统 CA 库限制。

## 实测结论（VLC 3.7.2, armeabi-v7a, Android 4.4）

**补 CA 前**：

| 频道 | 源 | 结果 | 说明 |
|---|---|---|---|
| CCTV-1 (720p) | HTTP | ✅ 播 | |
| CCTV-14 (1080p) | HTTP | ✅ 播 | 高码率无压力 |
| CGTN (1080p) | HTTPS 海外 | ✅ 播 | 链交叉签名到老根 Starfield G2（原库有） |
| CCTV-9 | HTTPS GitHub | ❌ 证书不受信任 | 链根 ISRG X1（原库无） |
| CCTV-4 America | HTTPS wcetv | ❌ 证书不受信任 | 链根 ISRG X1（原库无） |
| CCTV+ 1 | HTTPS cctvplus | ❌ 证书不受信任 | 链根 DigiCert Global Root G2（原库无） |

**补 CA 后**：三个 HTTPS 源（cctvplus / github.io / wcetv）**全部能播**（实测出画面）。

直播源 HTTP 占约 23.5%、HTTPS 占约 76.5%。补 CA 后 HTTPS 可播率大幅上升。

## 关键结论

1. **VLC/gnutls 读的是系统 CA**：把 `/system/etc/security/cacerts` 移走后 CGTN 也失败（铁证），且 VLC 数据目录/APK 均无内嵌 CA bundle。
2. **"信任与否"取决于证书链根是否在系统 CA 库**：CGTN 靠 Amazon Root CA 1 **交叉签名**到老根 Starfield Services G2（原库有）→ 补 CA 前就能播；cctvplus/wcetv/github 链根是新根（DigiCert G2 / ISRG X1）→ 补 CA 前拒播。
3. **VLC 会缓存补 CA 前的 CA 库**：补完需 `pm clear org.videolan.vlc`（或确保 VLC 进程完全冷启）才重读；否则仍按旧证书库校验。

## 修复：补 CA 到系统

使用 `../scripts/patch_ca_android44.sh`（需盒子 root）：

```bash
./scripts/patch_ca_android44.sh 192.168.10.148:5555
```

脚本流程（幂等、只加不删、自动备份）：
1. 备份盒子现有 `/system/etc/security/cacerts/`（tar 到 `/data/local/tmp` + 本地副本）。
2. 拆分本机 `/etc/ssl/certs/ca-certificates.crt`，识别**自签根**。
3. 按 `subject_hash_old`（MD5）命名生成 `<hash>.0` 的 DER，**跳过盒子已有**。
4. `remount rw`，写入 `/system/etc/security/cacerts/`，`chown root:root`、`chmod 644`、`chcon u:object_r:system_file:s0`（SELinux Enforcing 必须），`remount ro`。
5. 校验文件数增加。

## 回滚

```bash
# 用备份 tar 覆盖回 /system（remount rw 后）
```

备份在 `./cacerts_bak_<时间>.tar.gz`（本地）与 `/data/local/tmp/cacerts_bak.tar.gz`（盒子）。

## 注意事项

- **SELinux Enforcing**：新文件必须 `chcon u:object_r:system_file:s0`，否则进程读不到。
- Android 4.x 系统 CA 用 `subject_hash_old`（MD5）命名；新文件同时命名 `<md5>.0`。
- **360 超级 ROOT 会拦截 `su -c` 的 mount/写操作**（返回 `Permission denied`），但**交互式 su**（`echo 'cmd' | su`）全放行。脚本一律用交互式 su。
- **VLC 缓存补 CA 前的 CA**：补完需 `pm clear org.videolan.vlc`（或完全冷启）强制重读。
- 本脚本只影响 `cacerts` 目录，不改任何系统策略；重启保留（/system 持久）。
- 补 CA 对 VLC 有效；对**用系统 TLS 栈**的 App（握手就失败）**无效**，那类问题需换自带栈播放器或走本地文件方案。
- 若 `pm clear org.videolan.vlc`，VLC 设置会被重置（VLC 作备用播放器通常无关键配置，可接受）。
