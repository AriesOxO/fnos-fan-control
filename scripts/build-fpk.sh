#!/bin/bash
# 飞牛NAS 风扇控制器 — FPK 打包脚本
# 用法: bash build-fpk.sh [源码目录]
# 输出: fan-control-{version}.fpk

set -e

SRC_DIR="${1:-/tmp/fpk-src}"
BUILD_DIR="/tmp/fpk-build"
OUTPUT_DIR="/tmp"

echo "=== 开始打包 FPK ==="

# 清理构建目录
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# 1. 打包 app.tgz
echo "打包 app.tgz ..."
cd "$SRC_DIR/app"
tar czf "$BUILD_DIR/app.tgz" .

# 2. 计算 checksum
CHECKSUM=$(md5sum "$BUILD_DIR/app.tgz" | awk '{print $1}')
echo "checksum: $CHECKSUM"

# 3. 复制文件到构建目录
cp -r "$SRC_DIR/cmd" "$BUILD_DIR/"
cp -r "$SRC_DIR/config" "$BUILD_DIR/"
cp -r "$SRC_DIR/wizard" "$BUILD_DIR/"
cp "$SRC_DIR/manifest" "$BUILD_DIR/"

# 4. 写入 checksum
sed -i "s/^checksum.*=.*/checksum              = ${CHECKSUM}/" "$BUILD_DIR/manifest"

# 5. 复制图标（如果有）
for icon in ICON.PNG ICON_256.PNG; do
    if [ -f "$SRC_DIR/$icon" ]; then
        cp "$SRC_DIR/$icon" "$BUILD_DIR/"
    else
        # 生成占位图标（1x1 像素 PNG）
        printf '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82' > "$BUILD_DIR/$icon"
    fi
done

# 6. 确保脚本有执行权限
chmod +x "$BUILD_DIR/cmd/"*

# 7. 读取版本号
VERSION=$(grep "^version" "$BUILD_DIR/manifest" | sed 's/.*=\s*//' | tr -d '[:space:]')
echo "版本: $VERSION"

# 8. 打包 FPK
FPK_NAME="fan-control-${VERSION}.fpk"
cd "$BUILD_DIR"
tar czf "$OUTPUT_DIR/$FPK_NAME" .

echo ""
echo "=== 打包完成 ==="
echo "输出: $OUTPUT_DIR/$FPK_NAME"
echo "大小: $(du -h "$OUTPUT_DIR/$FPK_NAME" | awk '{print $1}')"
echo ""
cat "$BUILD_DIR/manifest"
