#!/bin/bash
# patch_ca_android44.sh — 给 Android 4.4 低端盒子补现代根 CA 证书
#
# 背景：Android 4.4 的 /system/etc/security/cacerts/ 是 2013-2014 年的旧 CA 库，
# 不含 2015+ 的新根(Let's Encrypt ISRG、Google GTS、GlobalSign R46、DigiCert 新根等)。
# 后果：VLC 等自带 TLS 的播放器能完成 TLS1.2 握手，但证书校验走系统旧 CA 库，
#       对"新根证书"站点弹"证书不受信任"而拒播。
# 本脚本把本机现代 CA 库中缺失的新根补进盒子系统 CA 目录(只加不删, 幂等, 可回滚)。
#
# 用法:
#   ./patch_ca_android44.sh [BOX_IP:PORT] [ADB_PATH] [CA_BUNDLE]
# 默认: BOX=192.168.10.148:5555  ADB=./platform-tools/adb  CA=/etc/ssl/certs/ca-certificates.crt
#
# 依赖: 盒子已 root(360 超级 ROOT 等, 有 su), 本机有 openssl
# 注意: 360 超级 ROOT 会拦截 `su -c` 的 mount/写操作(返回 Permission denied)，
#       但**交互式 su**(echo 'cmd' | su) 全放行。本脚本一律用交互式 su。

set -euo pipefail

BOX="${1:-192.168.10.148:5555}"
ADB="${2:-./platform-tools/adb}"
CA_BUNDLE="${3:-/etc/ssl/certs/ca-certificates.crt}"
STAGE="$(mktemp -d)"
CTX="u:object_r:system_file:s0"

log(){ echo "[*] $*"; }
die(){ echo "[!] $*" >&2; exit 1; }

# 交互式 su: 以 root 执行一段命令(360 全放行)
su_run(){ $ADB -s "$BOX" shell "echo '$1' | su" 2>&1 | tr -d '\r'; }

[ -f "$CA_BUNDLE" ] || die "CA bundle 不存在: $CA_BUNDLE"
command -v openssl >/dev/null || die "需要 openssl"
log "盒子: $BOX"
log "本机 CA bundle: $CA_BUNDLE"

# 0. 前置检查
$ADB -s "$BOX" shell 'true' >/dev/null 2>&1 || die "无法连接 $BOX"
su_run 'id' | grep -q 'uid=0' || die "盒子无 root(交互式 su 失败)"

# 1. 备份现有系统 CA(盒子 /data/local/tmp + 本地副本)
log "备份现有系统 CA..."
su_run 'tar czf /data/local/tmp/cacerts_bak.tar.gz -C /system/etc/security cacerts' || die "备份失败"
mkdir -p "$STAGE/backup"
$ADB -s "$BOX" pull /data/local/tmp/cacerts_bak.tar.gz "$STAGE/backup/cacerts_bak_$(date +%Y%m%d_%H%M%S).tar.gz" >/dev/null
cp "$STAGE/backup/"cacerts_bak_*.tar.gz . 2>/dev/null && log "本地备份: ./cacerts_bak_$(date +%Y%m%d_%H%M%S).tar.gz" || true

# 2. 本机: 拆分 CA bundle, 只取自签根, 生成 <md5hash>.0 的 DER(跳过盒子已有)
log "拆分本机 CA bundle..."
cd "$STAGE"
awk 'BEGIN{n=0} /-----BEGIN CERTIFICATE-----/{n++} {print > ("cert_" sprintf("%03d",n) ".pem")}' "$CA_BUNDLE"
$ADB -s "$BOX" shell 'ls /system/etc/security/cacerts/ | sed "s/\.0//"' > box_hashes.txt
box_total=$(wc -l < box_hashes.txt)
log "盒子现有系统 CA 数: $box_total"

mkdir -p push
added=0
for f in cert_*.pem; do
  iss=$(openssl x509 -in "$f" -noout -issuer 2>/dev/null | sed 's/^issuer=//')
  subj=$(openssl x509 -in "$f" -noout -subject 2>/dev/null | sed 's/^subject=//')
  in=$(echo "$iss" | tr -d ' ' | tr ',' '\n' | sort | tr '\n' ',')
  sn=$(echo "$subj" | tr -d ' ' | tr ',' '\n' | sort | tr '\n' ',')
  [ "$in" != "$sn" ] && continue
  md5=$(openssl x509 -in "$f" -noout -subject_hash_old 2>/dev/null)
  [ -z "$md5" ] && continue
  grep -qx "$md5" box_hashes.txt && continue
  openssl x509 -in "$f" -outform DER -out "push/$md5.0" 2>/dev/null
  added=$((added+1))
done
[ "$added" -eq 0 ] && die "没有需要补的新根(可能已补过)。退出。"
log "待补新根证书数: $added"

# 3. 推送到 /data/local/tmp, 交互式 su 写入 /system
log "推送证书到盒子..."
$ADB -s "$BOX" push push /data/local/tmp/ca_push/ >/dev/null

log "交互式 su: remount rw + 写入 + 权限 + SELinux + remount ro..."
su_run "mount -o remount,rw /system && cp -f /data/local/tmp/ca_push/*.0 /system/etc/security/cacerts/ && chown root:root /system/etc/security/cacerts/*.0 && chmod 644 /system/etc/security/cacerts/*.0 && chcon $CTX /system/etc/security/cacerts/*.0 && mount -o remount,ro /system && echo WROTE_OK" | grep -q WROTE_OK || { su_run 'mount -o remount,ro /system' >/dev/null; die "写入失败, 已恢复只读"; }

# 4. 验证
new_total=$($ADB -s "$BOX" shell 'ls /system/etc/security/cacerts/ | wc -l' | tr -d '\r')
log "写入完成。系统 CA 数: $box_total -> $new_total (应增加 $added)"
[ "$new_total" -gt "$box_total" ] || die "写入校验失败"

# 5. 提示: VLC 缓存
log "完成。重要: VLC/gnutls 会缓存补 CA 前的证书库, 需清 VLC 数据强制冷启才重读:"
log "  adb shell pm clear org.videolan.vlc   (会重置 VLC 设置, 仅备用播放器无妨)"
log "或: adb shell am force-stop org.videolan.vlc 后重新启动(若缓存未生效再 pm clear)"
log "验证: VLC 打开曾报'证书不受信任'的 HTTPS m3u8 应不再弹框。"
