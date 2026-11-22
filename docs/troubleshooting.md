# 排障手册（老盒子 + ADB）

## 遥控器方向键失灵 / 授权页点不开

**现象**：硬件红外遥控 `/dev/input/event0` 能抓到 `KEY_DOWN/RIGHT/UP/ENTER/BACK`，
但 360 超级ROOT 授权页、部分输入框**是触屏 UI，方向键无焦点**，点不了"允许/确定"。

**诊断**：先确认遥控器硬件是好的——

```bash
adb -s <IP>:5555 shell getevent -c 20 /dev/input/event0   # 按遥控应吐 keycode
```

**兜底**：ADB `input tap` 点按（坐标来自 `screencap` 截图观察）。

```bash
adb -s <IP>:5555 shell screencap -p /sdcard/x.png
adb -s <IP>:5555 pull /sdcard/x.png .        # 用看图软件找按钮坐标 (x,y)
adb -s <IP>:5555 shell input tap <x> <y>
```

- root 授权：`su` 的授权库在 `/data/data/com.qihoo.root/databases/su.db`（360超级ROOT）。
  把「控制台程序」「当贝桌面」等设为允许；`su -c id` 返回 `uid=0(root)` 即成功。
- `com.qihoo.permmgr` 是授权管理端，**不可卸载**，否则 root 失效。

## `adb install` 报 INSTALL_FAILED_OLDER_SDK

APK `minSdk` 高于盒子。`aapt`/`apkanalyzer` 不在场时，用安装器直接判定：

```bash
adb -s <IP>:5555 install -r xxx.apk   # 失败信息就是权威判据
```

## 配置/播放报 SSL 握手失败

```
javax.net.ssl.SSLProtocolException: SSL handshake aborted: ssl3 alert handshake failure
SSL23_GET_SERVER_HELLO
```

根因：Android 4.4 系统 TLS 太旧（见 README）。对策：

- 直播：改**本地文件** `clan://localhost/...`（推荐）。
- 点播：换 **TVBox4.1+** + MacCMS 直链源（`scripts/vod_probe.sh` 先探）。
- 若某源必须走 http：用 `scripts/serve.sh`（busybox nc，4.4 无 toybox）。

## App 装完但首页点不开 / 点播地址"拉取配置失败"

- 确认 App 内「配置地址/视频源」里的**点播**字段是否为空：Q 版 `do=api` 网页下发可能被
  服务端"拉取后校验"拒绝，电视端对话框手工输入更可靠（注意别被中文输入法吞掉——用英文/ASCII，
  或用 ADBKeyboard 类工具）。
- 直播/点播分开配：Q 版直播填 `clan://localhost/iptv/live.txt`，点播留空给 TVBox4.1+。

## 内存不足 / 卡顿

`free` 看可用；选**无原生 so、体积小**的壳（TVBox4.1+ 约 3MB，无 lib/）；别同时常驻多个播放器。
