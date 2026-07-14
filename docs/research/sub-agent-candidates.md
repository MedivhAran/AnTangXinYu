# Sub-agent 候选专项调研（第一轮）

## 调研目标

判断主动陪伴、记忆管理、健康管理推理和 FoH 评估与干预四项能力是否适合成为独立 Sub-agent。调研关注公开实现、专业方法、验证方式、风险边界和可复用程度。

## 当前结论

四项能力都具备独立 Sub-agent 的合理性，正式数量仍需根据工作流设计和评测结果确定。

首版主要服务糖尿病患者，核心目标人群是 1 型糖尿病患者。FoH 评估、干预、健康建议和评测案例优先使用 1 型糖尿病研究与指南作为依据。

| 候选 | 公开实现成熟度 | 专业方法成熟度 | 当前判断 |
| --- | --- | --- | --- |
| 主动陪伴 | 中 | 中高 | Sub-agent 候选 |
| 记忆管理 | 高 | 中 | Sub-agent 候选 |
| 健康管理推理 | 中 | 高 | Sub-agent 候选 |
| FoH 评估与干预 | 低 | 中高 | Sub-agent 候选，需要专业合作与临床验证 |

## 1. 主动陪伴

### 官方设计与公开实现

MarIA 是一个面向 2 型糖尿病用户的纵向对话实验系统。它设置了两个主动 Agent：Aurora 在用户一段时间没有互动后发起联系，Eduarda 定期发起糖尿病健康教育对话。论文报告了 35 名参与者、3 个月纵向使用的用户体验结果。

- 论文：https://pmc.ncbi.nlm.nih.gov/articles/PMC12872950/
- 源码：https://github.com/rafaellpontes/maria_paper

MarIA 源码展示了真实调度和发送路径，工程实现属于研究原型：活动主题和沉默天数直接写在脚本中，生成后直接发送消息，异常处理只输出简单错误文本。其职责划分和用户研究具有参考价值。

移动健康领域已经形成 Just-in-Time Adaptive Intervention（JITAI）方法。它将主动干预拆为长期目标、短期目标、调整变量、决策时点、干预选项和决策规则。DIAMANTE 随机试验在糖尿病与抑郁症状人群中验证了自适应消息对活动量的影响。

- JITAI 设计原则：https://pmc.ncbi.nlm.nih.gov/articles/PMC5364076/
- DIAMANTE 随机试验：https://pmc.ncbi.nlm.nih.gov/articles/PMC11496924/

### 基于证据的推论

主动陪伴 Agent 适合判断当前是否存在联系价值，选择陪伴目标并起草消息。调度、免打扰、频率上限、用户同意和正式发送应由应用程序执行。

### 待验证问题

- 哪些事件实际提升陪伴价值。
- 用户可接受的联系频率、时间和语气。
- 发送、不发送和延后联系三种决策的评价方法。
- 短期互动率与长期 FoH 改善之间的区别。

## 2. 记忆管理

### 官方设计与公开实现

Hindsight 已实现 retain、recall 和 reflect，并在后台将新事实整理成带来源和时间线的长期观察。它的冲突合并会区分重复信息、直接矛盾和真实状态变化，并保留变更历史与源记忆。

- 源码与架构：https://github.com/vectorize-io/hindsight
- 冲突处理：https://hindsight.vectorize.io/blog/2026/02/09/resolving-memory-conflicts

Letta 用可持久的 memory blocks 维护用户、Agent 人格等高价值上下文，支持 Agent 自主编辑、只读、多 Agent 共享。Mem0 提供记忆的新增、搜索、更新、删除和变更历史 API；其 2026 年新算法转向追加式事实提取。

- Letta memory blocks：https://docs.letta.com/guides/core-concepts/memory/memory-blocks
- Mem0：https://github.com/mem0ai/mem0

MedMemoryBench 提供面向个性化医疗对话的开源记忆评测框架，包含中文数据配置、多会话医患对话和 14 种记忆方法基线。

- https://github.com/AQ-MedAI/MedMemoryBench

### 基于证据的推论

Hindsight 可以承担陪伴记忆的自动提取、时间整合和召回。记忆管理 Agent 的核心工作可以集中在健康档案变更提案、信息来源分类、跨来源冲突与用户确认。

### 待验证问题

- Hindsight 在中文 FoH 长对话中的提取、时间定位、冲突处理和召回准确率。
- 医疗事实、用户自述、行为观察和 Agent 推论的可见性与权重。
- 用户纠正、删除和撤回后的传播范围。
- 陪伴记忆与正式健康档案之间的单向提案边界。

## 3. 健康管理推理

### 官方设计与公开实现

Google AMIE 的长期疾病管理研究将系统拆成实时共情对话 Agent 和深度管理推理 Agent。推理 Agent 结合多次就诊记录、临床指南和药物手册。研究用 100 个多次就诊场景与 21 位基层医生进行随机盲法模拟评估。公开内容包含论文和评测方法，完整运行时与模型代码尚未公开。

- Google 官方介绍：https://blog.google/innovation-and-ai/models-and-research/google-research/amie-for-disease-management-in-nature/
- 论文页：https://research.google/pubs/towards-conversational-ai-for-disease-management/

openCHA 开源了中央 Orchestrator、Planner、Executor 和外部任务框架。其糖尿病案例将 ADA 饮食指南、Nutritionix 数据和营养分析工具加入对话推理。

- 源码：https://github.com/Institute4FutureHealth/CHA
- 糖尿病案例：https://arxiv.org/abs/2402.10153

TIDE 是一个开源 CGM 分析工具，用共识指标筛选适合异步联系的用户，展示了确定性糖数据分析与专业审核结合的另一种路径。

- https://pmc.ncbi.nlm.nih.gov/articles/PMC9210201/

### 基于证据的推论

健康管理推理 Agent 适合负责跨时间整合档案、血糖规律、用户目标和专业知识，输出带证据、不确定性和适用条件的管理建议草案。Core Agent 负责用统一陪伴人格组织最终表达。血糖统计、指南检索和药物信息查询可以作为独立工具。

### 待验证问题

- 健康教育、生活方式建议、就医准备和个体化临床建议的产品边界。
- 中国临床指南、公众健康教育材料和药物数据的版本与授权。
- 建议与来源片段、用户数据证据之间的可追溯关系。
- 低置信度、高风险建议和信息冲突时的正式放弃回答与专业人员路径。

## 4. FoH 评估与干预

### 专业方法

HFS-II 是常用的 FoH 量表，经典结构包含担忧和行为两个分量表。后续研究将行为进一步探索为寻求安全、限制活动和刻意维持高血糖等因素。中国大陆已有中文版本的文化调适与心理测量学验证研究。HFS-II 由 HFS-Global 负责授权和分发，产品使用需要单独确认许可。

- HFS-II 验证：https://pmc.ncbi.nlm.nih.gov/articles/PMC3064031/
- FoH 类型研究：https://pmc.ncbi.nlm.nih.gov/articles/PMC8918257/
- 中文版研究：https://pmc.ncbi.nlm.nih.gov/articles/PMC7096186/
- 量表授权：https://www.hfs-global.com/

FREE 是针对年轻 1 型糖尿病用户 FoH 的 8 周认知行为干预。其核心包括错误信念识别、认知重构、放松训练、恐惧层级、渐进暴露、CGM 反馈、恐惧日记和维持计划。试验由持证临床心理学家按手册执行，并通过录音抽查评估忠实度。

- 干预结果：https://pmc.ncbi.nlm.nih.gov/articles/PMC11162312/
- 详细协议：https://pmc.ncbi.nlm.nih.gov/articles/PMC6938021/

HARPdoc 面向经过优化照护后仍有低血糖感知受损和反复严重低血糖的 1 型糖尿病成人。课程结合动机访谈、认知行为理论和“思维陷阱”练习，由受训的糖尿病教育者执行并接受临床心理学家监督。

- https://pmc.ncbi.nlm.nih.gov/articles/PMC9050729/

ADA 2026 建议使用年龄适配、标准化且经过验证的工具筛查糖尿病痛苦、抑郁、焦虑、FoH 和进食行为，并在有需要时转介具备相关经验的行为健康专业人员。

- https://diabetesjournals.org/care/article/49/Supplement_1/S89/163932/5-Facilitating-Positive-Health-Behaviors-and-Well

### 公开实现现状

本轮调研找到了公开试验协议、课程结构、数字化可行性研究和新的 HypoPals 数字行为干预计划，尚未找到经过同等验证且可直接复用的开源 FoH Agent。

- HypoPals：https://ohsu.elsevierpure.com/en/publications/a-pilot-trial-of-hypopals-assessing-trial-procedures-feasibility-/

### 基于证据的推论

FoH Agent 可以管理筛查时机、量表对话、认知和行为模式的纵向记录、干预活动和进度复盘。用户近期语言、饮食和活动变化可以作为关心与筛查邀请的触发线索。标准量表的阳性结果需要进入进一步评估或转介路径。

### 待验证问题

- 首版目标人群的糖尿病类型、年龄和低血糖经历范围。
- HFS-II、FoH 短筛查工具及其中文版本的授权与适用性。
- FREE、HARPdoc 等人工干预方法转化为数字对话时的内容忠实度与临床安全。
- 专业人员参与、危机识别、转介资源和不良事件处理。
- 个人化干预的效果指标，包括 FoH、自我管理、血糖指标、依赖风险和转介准确性。

## 综合判断

### 作为 Sub-agent 继续评估

1. 主动陪伴 Agent：具有独立触发时机、联系目标和发送审批边界。
2. 记忆管理 Agent：具有独立的后台运行周期、档案变更提案和用户确认流程。
3. 健康管理推理 Agent：具有独立专业上下文、跨时间推理、指南证据和高风险审核要求。
4. FoH 评估与干预 Agent：具有独立的专业方法、纵向状态、人机关系和安全边界。

### 作为工具或固定流程继续评估

- 血糖指标与趋势计算。
- 标准量表计分。
- 指南、药物和知识库检索。
- 图片、报告和药品识别。
- 调度、免打扰、频率控制和消息发送。
- 档案读取、变更提案、用户确认和正式写入。
- 安全规则、危机路由和正式放弃回答。

## 下一轮调研入口

1. 继续明确 1 型糖尿病核心人群的年龄、治疗方式和风险范围。
2. 与糖尿病医疗、糖尿病教育和临床心理专业人员核对健康建议与 FoH 干预边界。
3. 建立真实多轮案例集，分别测试四个候选 Sub-agent 相比 Core Agent 单独处理的收益。
4. 测试 Hindsight 和 PostgreSQL 档案在冲突、纠正、删除和权限隔离上的实际表现。
