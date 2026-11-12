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
k) AI学情报告：✅ 已落地（98d974d, v0.4.6，study_report.py + 学情页AI报告入口，7项测试）。教训：测试原生化后 release.yml 的测试步骤仍是旧脚本循环（smoke_*.py 已删导致 glob 失败）——改工作流时两份 workflow 要同步检查。
l) 学情可视化：✅ 已落地（d672901, v0.4.7）：学情页学生正确率趋势折线图+班级平均正确率对比条形图，/api/analytics/analytics/trends，3项测试。注：路由前缀是 /api/analytics，路径要写 /analytics/trends，最终 URL 是 /api/analytics/analytics/trends。错误类型分布已有条形图，饼图未做（现有环形图展示判定分布）
注意：备份/恢复(/api/backup)已实现数据迁移；k/l 是在其之上的新功能。

m) 官网(GitHub Pages)：新建 gh-pages 分支放静态落地页，启用 Pages 自动部署
   - 设计语言：从 styles.refero.design 选现成 DESIGN.md 风格（教师工具适合 Notion「warm paper」或 OpenAI「research lab」类温暖干净风格），禁止自创样式
   - 内容必需：产品介绍、Windows exe 下载按钮(链 GitHub Releases/latest)、开发者署名与联系、开源协议说明、二维码赞赏入口(donate_qr.png 上传后加)

## PRD 对照审计（2026-11-11 主对话完成，缺口按优先级排队）
已落地：质量门禁d、切题、规则校验h、teacher_override、预算护栏j、知识点i、备份恢复、测试连接c、启动体验b、低置信度复核f、科目可选。
缺口（PRD 有、代码无），按此顺序开发：
n) ✅ 作业等级换算：已落地（e9abbd3，grade_scale.py+设置页阈值+工作台/学情页/手机端等级，23项测试）
o) ✅ AI 一致率统计：已落地（8b4b1f0，14项测试）
p) ✅ 首次启动三步引导：已落地（9e2aa81，32项测试）
q) 手机端5秒倒计时预览+撤销重拍+顶部队列计数(已传N/批改M/完成K)（PRD五章）✅
r) 图像预处理：EXIF校正+透视纠偏+CLAHE（PRD六章，切题前置）✅（透视纠偏v0.4.5已做：Otsu四角检测+QUAD拉正；CLAHE未做，低对比度走autocontrast替代）
s) 多次采样一致性：已由低置信度二次复核 review.py mode=resample 覆盖 ✅
t) LLM教学建议：✅ backend/models/teaching_advice.py + /api/analytics/teaching-advice(/generate) + 学情页"AI教学建议"卡片（prompt锁定只引用输入数据，纯文本低成本调用，存teaching_advice表，可重新生成）
u) 学段/学科配置表驱动 subject_config ✅（setup向导/引导页已表驱动；grading_engine默认值"数学"仅作回退，保留）

## PRD 全文对照审计（2026-11-12 第二轮，grep 代码逐章验证）
确认已落地：引导/记住/切换器、IP+二维码引导、适配层+主备自动切换、key掩码存储、测试连接、质量门禁、切题+预处理、规则校验、低置信度复核、知识点、预算护栏、价格表、AI学情报告+教学建议、等级换算、AI一致率、队列持久化重启恢复、备份恢复、错误类型、手机端倒计时/撤销/队列计数、答案模板、合规提示文字。
剩余缺口（按优先级）：
v) 批改结果缓存：✅ 已落地（58c81c5, v0.4.8：GradingCache表+设置页开关，6项测试）
w) PRD 4.3 快路径：选择题/单一数值填空先规则精确匹配命中不调 API——受限于学生答案需先由视觉模型读出，整页模式下无法跳过调用；可考虑对选择题仅让模型读答案不做判题（省输出token），或本地OCR机器印刷选项。低优先，待真实Key验证后评估
x) PRD 4.3 单次请求 token 上限开关：✅ 已落地（v0.4.9：get_max_tokens/save_max_tokens + grade 请求体携带 max_tokens + 设置页可调 + /api/settings/max-tokens，6项测试共149 passed）
y) 错误类型饼图：现有环形图展示判定分布+条形图错误类型，PRD 七章"错误类型分布"已覆盖，饼图可选
z) CLAHE：PRD 六章提及，现用 autocontrast 替代低对比度增强，实测如不足再上（依赖真实Key验证）
aa) 敏感信息自动遮盖（PRD 标 P1）：✅ 已落地（v0.4.10：privacy.py 顶部涂黑+设置页开关/比例，送模型副本遮盖原图不动，9项测试共157 passed）
bb) ✅ 置信度融合第四因子：已落地（v0.4.11，confidence_fusion.py 知识点历史准确率调整置信度+路由接入二次复核之前，11项测试共168 passed）
## PRD 第三轮逐章审计（2026-11-13）
逐章 grep 代码复核：一二章引导/配置表/切换器、三章IP+二维码、四章适配层/key/成本护栏/价格表/max_tokens/缓存/失败处理、五章手机端倒计时+队列计数+质量预检、六章流水线/规则校验/置信度融合四因子、七章三层看板/教学建议/错误类型分布、八章数据模型含 uploader_user_id/teacher_override、九章备份恢复——全部落地。仅剩两项可选：y)错误类型饼图（已有条形图+环形图覆盖）、z)CLAHE（autocontrast 替代，待真实Key验证）。**待办全部完成。** 后续转入：真实API Key验证（需用户配合）与官网/反馈驱动迭代。

纪律：每轮一个待办，开发→测试→CI绿→提交。官网bug修复穿插进行。

## 版本纪律（用户 2026-11-11 指定）
每完成一个待办(n-u 或后续新功能)：本地测试 + CI 绿 → 立即升一个小版本(v0.4.1→v0.4.2→...)并打 tag，release.yml 自动出 exe。不等攒一批。bug 修复不升版本，随下一个功能版本带上。打 tag 前检查：CHANGELOG.md 加条目、版本号同步(CHANGELOG/version 引用处)。

## 红笔标注精准覆盖：GitHub 方案调研（2026-11-12，用户指定优先级）
用户决策：姓名等隐私不敏感，隐私遮盖默认关闭（已改）。第一优先=题目识别准确性，第二=红笔标注精准覆盖原题。
调研结论（已核实）：
- PaddleOCR **PP-DocLayout_plus**（RT-DETR，官方自建数据集含试卷/exam，mAP 83.2，L/M/S 三档）是版面检测最优开源方案，能检测 text/title/table/formula 等 23 类区域 → 用它替代自研行投影切题。体积考量：exe 打包用 S/M 档 + onnxruntime 导出推理（避免拖 paddle 全家桶）。
- Lazily01/homework-grader：用有道智云 API 返回每题精确 bbox（付费第三方、数据出本机，不符合我们"数据不出教师电脑"原则，仅借鉴其"切题坐标→裁剪→逐题识别"流程）。
- leipengo/homework-grader：与我们架构相同（扫描→视觉模型预批→老师复核→红笔批注 PDF），批注层思路一致。
- OpenCV-YOLO 作业批改：YOLO 检测答案区域再标注——需要自建标注数据训练，长期方案。
实施顺序（排入队列 g 后半场）：
g1) 引入 PP-DocLayout_plus-S/M（onnxruntime）检测试卷版面 → 题目块坐标替代行投影
g2) 每题块裁剪送真实模型识别题干+手写答案（题块与批改结果构造性一一对应，红笔精准压题）
g3) 标注渲染升级：参考 AI-Marker/阅卷平台样式，红笔批注画在作答区旁（错号+短错因），不遮挡原字
注意：真实模型接入后 LLM 返回题号与题块顺序对齐（题块自上而下=题号顺序）。

## 红笔标注调研 V2（2026-11-12 用户指出关键词不对，重搜后修正）
正确技术拆解：①版面分析/试卷切题 ②手写内容检测与分割(核心,上版漏搜) ③题目↔答案空间对齐(Spatial Grounding)。
新发现（已核实）：
- **jz315/chinese-exam-question-regions**：中文试卷切题 YOLO 数据集 2360页/12431题框/9科，含题号+续题关系 → 训 YOLO(onnx) 替换行投影
- **Chehrehmi/vedaai-assessment-extraction**：手写块检测→归一化bbox→题号确定性映射→语义兜底→原卷高亮。核心原则：红笔压学生手写块，不是题干
- **boxfish-ai/BAGS**：DeepLabv3+ 分割答案区下划线定位手写，91%；小学作业横线作答区=天然锚点
- 2026论文：VLM整页+结构化prompt 98.4% > YOLO 88% → 真实模型prompt设计"返回题号+学生答案+错因+位置线索"
修订路线：
g1) ✅ 页眉剔除+题号OCR锚点已落地（v0.4.12, ac432a5）：RapidOCR(onnxruntime,已入requirements+spec打包)识别题号锚点，≥2锚点重建切题区域(页眉天然剔除)，1锚点剔页眉，无OCR回退行投影；真实模拟页4锚点全中1.3s；19项测试共187 passed；exe已发v0.4.12 Release
g2) 手写墨迹块聚类bbox(复用现有二值化) → 每题带内手写块=红笔目标
g3) chinese-exam-question-regions 训 YOLO 切题(exe用onnx)
g4) 真实模型结构化prompt: 一次返回题号/学生答案/判分/错因，定位判定一体

## 增补记录
- 知识点归一化 ✅（v0.4.16）：knowledge_norm.py canonical_key（去空白/全角→半角/去尾标点+别名表），学情分析、confidence_fusion 历史因子、study_report 三处聚合统一走归一化 key；相近知识点绝不合并（无相似度层，安全底线）。9项新测试共211 passed。CI/Release 绿，exe已发。下轮候选：别名表按真实数据扩充；或待真实API Key。
- g2 ✅ 手写墨迹块聚类 bbox（v0.4.13）：question_splitter.detect_ink_blocks / tighten_band_to_ink；grading.py 三处切带定位统一走 _tighten() 收紧到手写墨迹外接框。坑：墨迹检测的连通块合并 gap 用格子数（BLOCK_MERGE_GAP=6，相对 520 网格），改分辨率要同步调；下轮候选 g3：印刷题干与手写墨迹区分（红笔只压手写答案）或待办队列下一项。
- g3 防重叠收尾 ✅（v0.4.15）：place_chip 增 avoid 参数（其它题框+已放置文字条），候选位右→左→上→下四级，全被占选重叠面积最小；_draw_reason_chip 返回实际矩形供避让。4项新测试共202 passed。坑：原测试断言"左右贴边→下方"，上方候选更优（不压下一题带），断言已同步改为"上或下"。下轮候选：i)知识点体系强化或待真实API Key做准确率度量。
- g3 ✅ 标注渲染升级（v0.4.14）：错题只画贴角小错号（✗/?/部分），短错因（error_type 优先，截6字）白底红字文字条放 bbox 外侧（place_chip：右→左→下），不再整框遮挡原字；对错改贴角小对勾。坑：gh-pages 分支是手动同步（CI 不部署官网），同步时必须 checkout gh-pages 只改 download.html，勿把 site/ 混入；github.io 有 301 需 curl -sL 验证。新增6项测试共199 passed。下轮候选：错因文字条与相邻题带防重叠；或待真实API Key后做准确率度量。
- z) CLAHE ✅（v0.4.17）：image_preprocess._clahe_enhance——低对比度(灰度std<40)时用 CLAHE(LAB L通道, clip2.0, 8x8)，增强后全局std反而降(纯平图clip所致)则回退 autocontrast；cv2 缺失回退不抛异常。新增依赖 opencv-python-headless==4.10.0.84（exe 会增大约 50MB）。2项新测试共213 passed。至此 PRD 可选项 y/z 中 z 完成，y 错误类型饼图判定不做（条形图+环形图已覆盖）。真实 API Key 仍是准确率验证唯一卡点。
- w) 选择题快路径 ✅（v0.4.18）：_is_choice_only（参考答案全为单选项字母 A-H 且≥2题）→ _build_read_prompt 只让模型读 student_choice（不判题、不发送参考答案），_questions_from_choices 本地规则比对判题（correct/wrong 0.95，读不清 unclear 0.5 进复核），_parse_response 识别 "choices" 键返回 fast_path 标记；混合题型自动回退整卷模式。7项新测试共220 passed。教训：g4 结构化 prompt 此前已在 base prompt 落地（题号/答案/错因/知识点/bbox 一次返回），勿重复开发。
- 批改结果导出 CSV ✅（v0.4.19）：/api/grading/export/{id}（UTF-8 BOM，Excel 中文不乱码，文件名 RFC5987 中文编码），详情页「导出 Excel(CSV)」按钮；3项新测试共223 passed。PRD 待办全部完成后的第一个口碑向增量。下轮候选：按作业批量导出全班成绩单（一个 CSV 多列=学生）；或待真实 API Key。
- 知识点掌握列 ✅（v0.5.3）：export-class-all 按学生聚合知识点掌握率（canonical_key 归一化），全班并集薄弱优先追加列（上限12），无知识点时输出与旧版完全一致。2项新测试共234 passed。功能待办已全部完成，后续增量等真实API Key或用户反馈。
- 题干列（v0.5.5）：prompt 加 question_text（整卷+选择题快路径），export-wrong-all 从 QuestionResult.llm_raw_output 提取（llm_raw_output 在 QuestionResult 不在 Submission，勿再踩）。3项新测试共242 passed。
- 错题讲评清单 ✅（v0.5.4）：export-wrong-all 一行一题（wrong/partial/unclear 或 needs_review），按知识点错题次数降序分组（错误最多的考点排前），组内按学生题号排；工作台「错题讲评清单」按钮。5项新测试共239 passed。功能待办仍为空，后续等真实API Key或用户反馈。
- 得分矩阵导出 ✅（v0.5.8）：export-question-matrix/{id}，学生×题号 √/×/△/?/复核，末两行每题正确率/正确数（分母=√+△+×，复核不计）；工作台按钮已更名「成绩单:/得分矩阵:」区分。4项测试共256 passed。坑：CSV 的 Content-Disposition 是 URL 编码后的 filename*，测试别断言中文原文；首个 writer.writerow 是标题行，纯表头需另写一行。
- 学生个人错题本 ✅（v0.5.6）：export-student/{student_id}（days 过滤），按知识点分组（错得最多的在前），题干列复用 _question_text_of；dashboard /summary 新增 students 列表；工作台导出区学生下拉+「学生错题本」按钮。6 项新测试共 248 passed。坑：ORM 对象 db.close() 后访问属性会 DetachedInstanceError，测试里必须在 close 前取 id；Submission 按天过滤用 created_at 不是 assignment.created_at。功能待办仍为空，后续等真实 API Key 或用户反馈。
