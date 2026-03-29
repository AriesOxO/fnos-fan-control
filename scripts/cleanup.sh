#!/bin/bash
# 飞牛风扇控制器 — 安装前清理脚本
# 用法：以 root 身份运行 bash cleanup.sh

echo "清理风扇控制器残留..."

userdel fan-control 2>/dev/null && echo "  已删除用户 fan-control" || echo "  用户不存在，跳过"
groupdel fan-control 2>/dev/null && echo "  已删除组 fan-control" || echo "  组不存在，跳过"

for vol in $(ls -d /vol* 2>/dev/null); do
    for dir in @appconf @appdata @apphome @appmeta @apptemp @appcenter; do
        if [ -d "$vol/$dir/fan-control" ]; then
            rm -rf "$vol/$dir/fan-control"
            echo "  已删除 $vol/$dir/fan-control"
        fi
    done
done

rm -rf /var/apps/fan-control 2>/dev/null && echo "  已删除 /var/apps/fan-control" || true

echo "清理完成，可以重新安装。"
