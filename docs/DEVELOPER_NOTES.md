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
