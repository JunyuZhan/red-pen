# 开发者备忘（cron 每轮必读）

## 已核实的外部事实（不要凭旧知识断言，一律搜索/实测）
- DeepSeek 官方 API 的 `deepseek-flash` 模型**已支持图片输入**（OpenAI 兼容，base_url `https://api.deepseek.com`）；DeepSeek-OCR 是其开源 OCR 模型，对手写/文档识别有优化。**禁止**硬编码"某厂商不支持视觉"的判断——一律运行时实测（测试连接按钮：内置测试图 → 实际调用 → 根据真实响应判断是否支持视觉/Key 是否有效/耗时多少）。
- 任何厂商能力、价格、接口变化，先 web_search 核实再写代码，不确定就实测。

## 待办顺序
a) Windows PyInstaller 打包 + CI windows-latest 构建 job 上传 exe artifact + 中文字体兼容（C:/Windows/Fonts/msyh.ttc 等）
b) 启动体验：启动后自动打开浏览器进工作台，控制台显示局域网地址
c) 模型连接测试按钮（运行时实测视觉支持，见上）
d) 图像质量门禁：模糊（拉普拉斯方差）/亮度/尺寸过小 → quality_flags 标红提示重拍
e) 并发批改：队列 N 路并发（可配 2-4），工作台显示队列状态，记录单份耗时
f) 低置信度二次复核：confidence<0.7 自动用备用 provider（或同模型重采样）交叉验证，不一致标黄进复核
g) CV 切题 + 裁切放大：EXIF 校正/透视纠偏/版面分析 → 逐题裁切送审（与整页模式可切换）
h) 规则交叉校验：数值等价/选项合法性/空答案 → 命中降置信度进复核
i) 知识点体系：LLM 输出加 knowledge_point 字段，学情分析加知识点统计
j) 月度预算护栏 + 模型价格预设表（含 DeepSeek/Qwen/GLM/豆包，注明以官网为准可编辑）

全部完成后输出"待办全部完成"。

## 增补待办（用户 2026-11-11 提出）
k) AI学情报告：把学生历史错题/错误类型/正确率序列交给LLM，生成自然语言学情分析（按学生/按班级），存表并在学情页展示，可重新生成
l) 学情可视化：看板与学情页加图表（纯前端SVG或引入轻量图表库本地打包），错误类型分布饼图、学生正确率趋势折线图、班级对比条形图
注意：备份/恢复(/api/backup)已实现数据迁移；k/l 是在其之上的新功能。

m) 官网(GitHub Pages)：新建 gh-pages 分支放静态落地页，启用 Pages 自动部署
   - 设计语言：从 styles.refero.design 选现成 DESIGN.md 风格（教师工具适合 Notion「warm paper」或 OpenAI「research lab」类温暖干净风格），禁止自创样式
   - 内容必需：产品介绍、Windows exe 下载按钮(链 GitHub Releases/latest)、开发者署名与联系、开源协议说明、二维码赞赏入口(donate_qr.png 上传后加)

## PRD 对照审计（2026-11-11 主对话完成，缺口按优先级排队）
已落地：质量门禁d、切题、规则校验h、teacher_override、预算护栏j、知识点i、备份恢复、测试连接c、启动体验b、低置信度复核f、科目可选。
缺口（PRD 有、代码无），按此顺序开发：
n) 作业等级换算：作业维度正确率→等级(默认 A≥90/B≥75/C≥60/D，阈值可在设置页配)，可选每题分值加权总分；工作台作业卡片+学情页显示
o) AI 一致率统计：teacher_override 与原始判定对账，学情页显示"AI判定与教师确认一致率%"（真实准确率指标）
p) 首次启动三步引导：学段/学科/班级选择页+localStorage默认记住+工作台顶部上下文切换器（PRD二章）
q) 手机端5秒倒计时预览+撤销重拍+顶部队列计数(已传N/批改M/完成K)（PRD五章）✅
r) 图像预处理：EXIF校正+透视纠偏+CLAHE（PRD六章，切题前置）✅（透视纠偏v0.4.5已做：Otsu四角检测+QUAD拉正；CLAHE未做，低对比度走autocontrast替代）
s) 多次采样一致性：已由低置信度二次复核 review.py mode=resample 覆盖 ✅
t) LLM教学建议：✅ backend/models/teaching_advice.py + /api/analytics/teaching-advice(/generate) + 学情页"AI教学建议"卡片（prompt锁定只引用输入数据，纯文本低成本调用，存teaching_advice表，可重新生成）
u) 学段/学科配置表驱动 subject_config ✅（setup向导/引导页已表驱动；grading_engine默认值"数学"仅作回退，保留）
纪律：每轮一个待办，开发→测试→CI绿→提交。官网bug修复穿插进行。

## 版本纪律（用户 2026-11-11 指定）
每完成一个待办(n-u 或后续新功能)：本地测试 + CI 绿 → 立即升一个小版本(v0.4.1→v0.4.2→...)并打 tag，release.yml 自动出 exe。不等攒一批。bug 修复不升版本，随下一个功能版本带上。打 tag 前检查：CHANGELOG.md 加条目、版本号同步(CHANGELOG/version 引用处)。
