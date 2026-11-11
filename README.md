# 红笔 RedPen · 教师作业批改助手

**开发者：JunyuZhan（397791043@qq.com）**

基于大模型的教师作业自动批改系统：手机拍照上传 → AI 逐题批改 → 红笔标注回显 → 学情分析看板。

> 📥 **下载安装包（Windows 免安装单文件）**：[GitHub Releases](https://github.com/JunyuZhan/red-pen/releases/latest)
> 手机连电脑同一 WiFi，扫设置页二维码即可使用，无需在手机装任何 App。

## 功能特性
- ✅ 手机拍照上传作业（拍照预览倒计时、质量提示）
- ✅ 云端大模型逐题批改：判定对错 + 错因分析 + 置信度
- ✅ 红笔标注回显：原图圈画对错标记 + 逐题卡片
- ✅ 教师看板：班级/学生统计、错误类型分布、学情分析
- ✅ 批改引擎教师自选：DeepSeek / 通义千问 / 智谱 / 自定义 OpenAI 兼容接口
- ✅ 主备引擎自动切换、失败重试与队列持久化
- ✅ 答案模板复用：录入参考答案，批改时自动注入
- ✅ 成本估算与控制：token 用量统计、单价可配
- ✅ 数据备份/恢复、PIN 登录、数据合规提示

## Windows 快速开始（推荐）

1. 从 [Releases](https://github.com/JunyuZhan/red-pen/releases/latest) 下载 `RedPen.exe`，放到任意文件夹（建议新建一个专门文件夹，数据会存在同级的 `data` 目录）
2. **双击运行**：
   - 首次运行 Windows 会弹"Windows 已保护你的电脑"——这是未签名 exe 的正常提示。点 **"更多信息" → "仍要运行"** 即可
   - 如果杀毒软件（360/火绒等）误报，请选择"信任/添加白名单"。本软件从 GitHub 官方 Releases 下载，请在发布页核对下载来源
3. 浏览器自动打开工作台，首次登录 PIN 为 **123456**，请立即在设置页修改
4. 在「设置」页填入你的模型 API Key（如 DeepSeek、通义、智谱任选其一）
5. 手机连接电脑同一 WiFi，在设置页"手机连接引导"扫二维码，即可拍照批改

## 从源码运行（开发者）

```bash
pip install -r requirements.txt
python -m uvicorn backend.app:app --port 8080
# 访问 http://localhost:8080，默认 PIN 123456
```

## 项目结构
```
red-pen/
├── backend/                 # 后端服务（FastAPI）
├── frontend/               # 前端界面
├── models/                # AI模型适配器
├── database/              # 数据库相关
├── config/               # 配置文件
└── docs/                 # 文档
```

## 联系与社区
- 开发者：**JunyuZhan**
- 邮箱：**397791043@qq.com**
- 提 bug / 功能建议：[开 Issue](https://github.com/JunyuZhan/red-pen/issues)
- 用户交流群：微信群（见 Release 页说明 / 联系邮箱拉群）

## 许可证
本项目采用 [PolyForm Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0) 协议：
**个人学习与教育机构（学校/教师）使用完全免费；未经作者授权，禁止任何商业用途。**
