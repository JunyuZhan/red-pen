# PRD 复审报告（2026-11-14，对照 docs/prd.md V0.4）

逐节核对 PRD 与当前实现（v0.5.21，338 passed，main CI 绿）。结论：**PRD 核心需求全部有实现落地，无阻塞性缺口**。以下为逐项证据，均可在代码中定位验证。

## 一、架构与扩展性
- 云端算力 / 手机仅内网：后端本地调 LLM，手机端只上传（`routes/upload.py`、`routes/mobile.py`）。
- 学段/学科/班级配置表驱动：`stage_config` / `subject_config` 表（`database.py`）。
- 二维码 + IP 引导：`routes/network.py`（qrcode 内存生成 data URL）。

## 二、模型接入（PRD 第四节）
- 适配层 + 主备切换：`models/grading_engine.py`。
- 测试连接 + 视觉模型支持检测 + 推荐模型清单：设置页（待办 c）。
- 成本估算 + 价格预设表 + 月度预算护栏：`models/budget.py` + `routes/settings.py /cost`（待办 j）。
- 选择题快路径（只读不判，省 token）：`grading_engine.py:137-211`。
- 批改缓存（图片 sha256+答案+model 为键，重复提交不重复计费）：`grading_engine.py:518+`。
- 失败处理：重试（`MAX_RETRY=3`）、429 限流识别、非 JSON 重试后标黄、主备切换均有实现。
- 队列持久化重启可续：`app.py:128-140`（重启把卡在 processing 的任务转 failed 可重试）。
- 隐私遮盖（P1）：`models/privacy.py`，送模型前对副本打码，原图不动，设置页可开。

## 三、手机端与质量（PRD 第五节）
- 连续拍照、5 秒预览撤销、按学生组织会话：`routes/mobile.py`。
- 质量门禁（模糊/亮度/过小，标红可强行上传）：`models/quality_checker.py`（待办 d）。

## 四、批改流水线（PRD 第六节）
- 图像预处理（EXIF/透视/CLAHE）+ CV 切题裁切放大：`image_preprocess.py`、`question_splitter.py`（待办 g）。
- JSON schema 强约束输出；规则交叉校验（数值等价/选项/空答案）：`rule_validator.py`（待办 h）。
- 低置信度二次复核（<0.7 交叉验证，不一致标黄）：`models/review.py`（待办 f）。
- 置信度融合（含知识点历史准确率）：`confidence_fusion.py`。
- 教师复核队列 + `teacher_override` 独立存储：`routes/review` 相关路由。
- 答案模板复用：`routes/grading.py:1116+`。

## 五、看板（PRD 第七节）
- 三层看板：作业概览 / 知识点掌握（`analytics.py`，含错误四分类）/ 高频错题 + LLM 教学建议（`teaching_advice.py`，锁定只引用输入数据）。

## 六、数据与备份（PRD 八、九节）
- 数据模型含全部扩展预留字段（uploader_user_id、teacher_override、llm_raw_output、bbox 等）。
- 一键备份/恢复 + 安全加固（损坏校验、zip-slip 防护、SQLite 在线快照、先校验后替换）：v0.5.20–v0.5.21 完成。

## 遗留与建议（非阻塞）
1. **准确率验证仍未做**：无真实 API Key / 学生作业素材，"识别与判题准确性"只能靠单测模拟。这是 PRD 核心诉求，等素材随时切入。
2. 真实 Windows 环境（exe 安装、防火墙弹窗、手机扫码连接）只做过 CI 级验证，缺一次真人装机走查。
3. PRD 4.5 提到的"学校与厂商签约企业版"属于后续商业动作，不在代码范围。
