#!/bin/bash
# 红笔系统启动脚本

cd "$(dirname "$0")"

echo "🚀 启动红笔系统..."

# 检查Python环境
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 未安装"
    exit 1
fi

# 启动系统
python3 main.py &

echo "✅ 红笔系统启动完成"
echo "📡 访问地址: http://localhost:8080"
echo "📱 手机端地址: http://localhost:8080/mobile"
echo "📊 API文档: http://localhost:8080/api/docs"