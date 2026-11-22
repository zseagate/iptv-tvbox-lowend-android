# IPTV / TVBox on 低版本安卓盒子（Android 4.4 / API 19）

在 **Android 4.4（API 19）、armeabi-v7a** 这类老电视盒子上稳定看 **直播 + 点播** 的方法与脚本。
这类盒子是存量最大的一批"报废边缘"设备：现代 IPTV/TVBox App 要么装不上，要么装上连不上源。
本仓库只讲**兼容性结论 + 脚本 + 方法论**，**不含任何实际节目源清单**（源请自备/自测）。

> 参考设备：小米盒子 MiBOX2（`dredd`），Android 4.4.2，SDK 19，armeabi-v7a，Amlogic Meson8，~755MB RAM。
> 方法对同代盒子（海思/晶晨/瑞芯微，4.4.x）通用。

## 为什么老盒子这么难

| 障碍 | 现象 | 根因 |
|---|---|---|
| **minSdk 门槛** | `adb install` 报 `INSTALL_FAILED_OLDER_SDK` | 新版 App（如 TiviMate 2.x）`minSdk 21`，4.4 装不上 |
| **系统 TLS 太旧** | App 内 `SSLHandshakeException` / `ssl3 alert handshake failure` / `SSL23_GET_SERVER_HELLO` | Android 4.4 自带 OpenSSL 1.0.1，默认只稳协商到 **TLS 1.0/1.1**，现代 CDN 要求 TLS 1.2/1.3 + 新密码套件 |
| **没有 toybox / busybox 不全** | `toybox: not found` | 4.4 时代多用 busybox，无 Android 6+ 的 toybox，盒内起服务方式不同 |
| **内存小（<1GB）** | 大 App 卡顿/被杀 | 优先选无原生 so、体积小、系统 TLS 可用的壳 |

## 方案总览（实测分工）

| 用途 | App | 包名 | 关键点 |
|---|---|---|---|
| **点播** | **TVBox4.1+**（Android 4.1+ 兼容版） | `com.tvbox.android44` | 2026 新构建、自带较新 SSL，HTTPS MacCMS 源可用；接口是 **MacCMS** 格式 |
| **直播** | **TVBox Q 版**（q215613905 分支） | `com.github.tvbox.osc(.jun)` | 系统 TLS 拉不动远程 HTTPS，**直播走本地文件** `clan://localhost/...` 规避 |
| 备用播放 | **VLC for Android** 3.7.x（armeabi-v7a） | `org.videolan.vlc` | 自带 LibVLC TLS，HTTPS 流可播；可开本地 m3u |
| ❌ 装不了 | TiviMate 2.x | `ar.tvplayer.tv` | `minSdk 21`，4.4 直接被安装器拒 |

核心思路：**能本地化就本地化**（直播源落成盒子本地文件，绕开 TLS），
**必须联网就挑老协议也收的服务器**（点播 MacCMS 源：TLS 1.2 + 直链 m3u8）。

## 目录

```
scripts/   gen_live.py  box_live_setup.sh  serve.sh  tls_probe.sh  vod_probe.sh
configs/   macms_sources.md  tvbox_live.config.json
docs/      troubleshooting.md  persistence.md
```

## 快速开始

### 0. ADB 连盒子

```bash
adb connect <盒子IP>:5555            # 需先在盒子"开发者/网络调试"里开 ADB
adb -s <盒子IP>:5555 shell getprop ro.build.version.sdk   # 期望 19
```

### 1. 直播（TVBox Q 版，本地文件，重启有效）

把你自己的 m3u 转成 TVBox 直播 txt 并推到盒子：

```bash
python3 scripts/gen_live.py /你的路径/merged.m3u live.txt
adb -s <盒子IP>:5555 shell 'mkdir -p /sdcard/iptv'
adb -s <盒子IP>:5555 push live.txt /sdcard/iptv/live.txt
```

电视上：TVBox → 设置 → 配置地址 → **直播** 填：

```
clan://localhost/iptv/live.txt
```

或用网页下发（Q 版在 9978 提供 `/action`）：

```bash
curl -X POST -d 'do=liveApi' --data-urlencode 'url=clan://localhost/iptv/live.txt' \
  http://<盒子IP>:9978/action
```

> 走本地文件 = 不依赖主机 IP、不依赖 TLS、开机即在，最稳。
> 如果你的 Q 版只认 http：用 `scripts/serve.sh`（busybox `nc` 版，适配 4.4，无 toybox）。

### 2. 点播（TVBox4.1+，MacCMS 源）

电视上：TVBox4.1+ → 设置 → 视频源 → **扫码添加接口**，屏幕显示 `http://<盒子IP>:9979/<token>`（约 5 分钟有效）。
主机侧直接 POST（token 从屏幕读，或 `uiautomator dump` 抓）：

```bash
TOKEN=***
curl -X POST -d 'name=暴风资源' --data-urlencode 'baseUrl=https://bfzyapi.com/api.php/provide/vod/' \
  "http://<盒子IP>:9979/$TOKEN"
```

先测一个 MacCMS 站对老盒子是否友好（直链 m3u8 + TLS1.2 可达）：

```bash
bash scripts/vod_probe.sh https://bfzyapi.com/api.php/provide/vod/
bash scripts/tls_probe.sh v.fengbao14.com        # 测播放域名 TLS
```

源挑选标准见 `configs/macms_sources.md`。

## 排障 & 持久化

- 遥控器方向键在部分授权/输入页无焦点、点不开内容：见 `docs/troubleshooting.md`（用 ADB `input tap`/`am` 兜底）。
- 重启后是否还在：直播走本地文件天然持久；8099 这类手起 HTTP 服务重启即失效，尽量别依赖；见 `docs/persistence.md`。

## 安全与合规

仅整理开源壳（TVBox 系）+ 兼容性诊断脚本，**不内置、不转载、不提供任何片源/接口清单**。
请仅用于播放你自有或已授权内容，遵守当地法规。
