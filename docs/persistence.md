# 持久化（重启后是否还在）

| 方式 | 重启后 | 说明 |
|---|---|---|
| **本地文件** `clan://localhost/iptv/live.txt` | ✅ 有效 | 直播推荐。文件在 `/sdcard`，App 配置在应用数据里，均在。 |
| TVBox4.1+ 扫码添加的 MacCMS 源 | ✅ 有效 | 存在 App 应用数据，重启保留。 |
| 手起 HTTP 服务（`busybox nc ... &`） | ❌ 失效 | 进程随重启消失；Android 4.4 无标准开机自启钩子，除非写到 `/system/etc/init/*.rc`（需 root，且 4.4 对 init.rc 支持有限）或装"开机自启"类工具。**尽量别依赖**。 |
| 8099 端口服务 | ❌ 失效 | 同上手起进程，重启即没；所以本方案直播走本地文件。 |

## 如果必须开机自启 HTTP 服务

- Android 4.4（Amlogic）常支持 `/system/etc/init.d/`（部分固件），可放脚本；不是所有固件都有，先 `ls /system/etc/init.d`。
- 或把 `busybox nc` 启动放到 `/system/bin/install-recovery.sh`（部分系统保留此钩子）。
- 或接受"手动/由某 App 拉起"，不依赖开机自启。

## 定时刷新直播源

主机侧用 systemd user timer（每 4h）：

```ini
# ~/.config/systemd/user/iptv-lowend.service
[Unit]
Description=Refresh TVBox live.txt for low-end box
[Service]
Type=oneshot
ExecStart=%h/project/iptv/iptv-tivimate/deploy/iptv-push-lowend.sh
# 也可直接用 public 仓库的 box_live_setup.sh + 你自己的 m3u
```

timer 每 4h 触发一次，盒子离线时脚本自动跳过（best-effort）。
