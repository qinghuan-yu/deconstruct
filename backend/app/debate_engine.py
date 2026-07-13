from dataclasses import dataclass


FINAL_DECISION_MARKER = "<!-- FINAL_DECISION -->"


@dataclass(frozen=True)
class AuditStep:
    key: str
    title: str
    mode: str
    prompt: str


AUDIT_STEPS = [
    AuditStep(
        key="problem_analyst",
        title="方案分析员",
        mode="debate.problem_analyst",
        prompt=(
            "你是方案分析员。任务不是下结论，而是把用户方案拆成可审计结构：目标、任务链、"
            "关键依赖、资源约束、时间节点、待验证前提。必须区分【事实】【推断】【建议】。"
            "如果用户上传了文档，只提取与决策有关的假设和执行要求，不要复述全文。"
        ),
    ),
    AuditStep(
        key="red_team",
        title="红队质询员",
        mode="debate.red_team",
        prompt=(
            "你是红队质询员。只找隐含假设、失败模式、反例和自欺风险。"
            "不得给宏大建议，重点输出可验证反证。"
        ),
    ),
    AuditStep(
        key="execution_evaluator",
        title="执行评估员",
        mode="debate.execution_evaluator",
        prompt=(
            "你是执行评估员。只评估时间、能力、协作、资源、验收方式。"
            "必须把试运行方案与试运行通过后的全量方案分开，不得混成一个计划。"
            "对止损条件使用“预警 → 诊断 → 调整 → 退出”四级机制。"
        ),
    ),
    AuditStep(
        key="evidence_auditor",
        title="证据审计员",
        mode="debate.evidence_auditor",
        prompt=(
            "你是证据审计员。专门抓无来源数字、伪精确概率、跳步推理、矛盾结论、"
            "剪枝缺乏依据和时间节点含糊。任何概率、比例、分数、阈值若没有来源，"
            "必须标注为“暂定阈值”，并说明它只是决策门槛而非测量事实。"
        ),
    ),
]


class DebateEngine:
    def __init__(self, store, provider):
        self.store = store
        self.provider = provider

    async def run(self, conversation_id, assistant_message_id, history):
        conversation_context = self._format_conversation_context(history)

        step_outputs = {}
        for step in AUDIT_STEPS:
            output = await self._run_step(
                conversation_id,
                assistant_message_id,
                key=step.key,
                title=step.title,
                mode=step.mode,
                prompt=(
                    f"{step.prompt}\n\n"
                    "输出格式：\n"
                    "1. 关键观察：用【事实】【推断】【建议】标注。\n"
                    "2. 证据或质询来源：说明来自用户材料、对话上下文或前序步骤的哪一点。\n"
                    "3. 决策影响：\n"
                    "- 若该观察会改变当前行动、试运行范围或决策门槛，写明影响；\n"
                    "- 若只属于背景信息或暂无法影响决策，明确写“暂无直接决策影响”。\n\n"
                    f"对话上下文：\n{conversation_context}\n\n"
                    f"前序审计结果：\n{self._format_step_outputs(step_outputs)}"
                ),
            )
            step_outputs[step.key] = output

        ledger = await self._run_step(
            conversation_id,
            assistant_message_id,
            key="decision_ledger",
            title="决策证据链",
            mode="debate.decision_ledger",
            prompt=(
                "你是决策台账整理员。不要做最终裁决。请把前序审计压缩成一张“争议—证据—裁决影响”表。"
                "每行必须包含：议题、初始方案假设、质询后的反证、当前证据状态、对计划的影响。"
                "仅当证据台账中存在“已被提出、尚无充分证据解决、且会改变当前决策”的异议时，"
                "才列入“未解决异议”。若不存在，明确写“本轮无重大未解决异议”。"
                "禁止为了满足格式虚构反对意见、风险、保留理由或机制。\n\n"
                f"对话上下文：\n{conversation_context}\n\n"
                f"前序审计结果：\n{self._format_step_outputs(step_outputs)}"
            ),
        )
        step_outputs["decision_ledger"] = ledger

        judge = await self._run_step(
            conversation_id,
            assistant_message_id,
            key="judge",
            title="裁判台账",
            mode="debate.judge",
            prompt=(
                "你是决策裁判。你的输出不给最终用户直接阅读，而是交给最终编辑器。\n\n"
                "只输出结构化决策台账，不写散文，不写“保留理由”等孤立标题。\n\n"
                "每条内容必须包含：\n"
                "- id\n"
                "- 类型：事实 / 推断 / 建议 / 未解决异议\n"
                "- 内容\n"
                "- 来源：用户材料、对话记录或哪个审计步骤\n"
                "- 是否改变当前决策：是 / 否\n"
                "- 处理方式：采纳 / 待验证 / 驳回\n"
                "- 理由\n\n"
                "规则：\n"
                "1. 禁止引入来源中不存在的新机制、新角色、新风险。\n"
                "2. 没有来源的数字、比例、等级只能标记为“暂定决策门槛”，不能伪装成事实。\n"
                "3. 未解决异议只保留会改变当前行动的内容。\n"
                "4. 仅当证据台账中存在“已被提出、尚无充分证据解决、且会改变当前决策”的异议时，"
                "才列入“未解决异议”。若不存在，明确写“本轮无重大未解决异议”。\n"
                "5. 禁止为了满足格式虚构反对意见、风险、保留理由或机制。\n"
                "6. 输出必须覆盖：当前裁决、试运行、继续条件、调整条件、终止条件。\n\n"
                f"对话上下文：\n{conversation_context}\n\n"
                f"审计证据：\n{self._format_step_outputs(step_outputs)}"
            ),
        )
        step_outputs["judge"] = judge

        await self._publish_thinking_summary(conversation_id, assistant_message_id, step_outputs)

        await self._run_step(
            conversation_id,
            assistant_message_id,
            key="final_writer",
            title="最终决策书",
            mode="debate.final_writer",
            prompt=self._final_writer_prompt(conversation_context, step_outputs),
            publish=True,
        )

        await self.store.update_message_status(assistant_message_id, "complete")
        await self.store.add_event(
            conversation_id,
            "assistant_message_completed",
            {"message_id": assistant_message_id, "mode": "expert"},
            message_id=assistant_message_id,
        )

    async def _run_step(self, conversation_id, assistant_message_id, key, title, mode, prompt, publish=False):
        await self.store.add_event(
            conversation_id,
            "debate_step_started",
            {"key": key, "title": title, "mode": mode},
            message_id=assistant_message_id,
        )
        chunks = []
        async for chunk in self.provider.stream_reply([{"role": "user", "content": prompt}], mode):
            chunks.append(chunk)
        content = "".join(chunks).strip()

        if publish:
            section = self._section_for(key, title, content)
            await self.store.append_message(assistant_message_id, section)
            await self.store.add_event(
                conversation_id,
                "assistant_delta",
                {"delta": section},
                message_id=assistant_message_id,
            )

        await self.store.add_event(
            conversation_id,
            "debate_step_completed",
            {"key": key, "title": title, "mode": mode, "content": content},
            message_id=assistant_message_id,
        )
        return content

    async def _publish_thinking_summary(self, conversation_id, assistant_message_id, step_outputs):
        section_titles = {
            "problem_analyst": "方案分析员",
            "red_team": "红队质询员",
            "execution_evaluator": "执行评估员",
            "evidence_auditor": "证据审计员",
            "decision_ledger": "决策证据链",
            "judge": "裁判台账",
        }
        sections = [
            self._section_for(key, section_titles[key], content)
            for key, content in step_outputs.items()
            if key in section_titles and content
        ]
        if not sections:
            return
        content = "".join(sections)
        await self.store.append_message(assistant_message_id, content)
        await self.store.add_event(
            conversation_id,
            "assistant_delta",
            {"delta": content},
            message_id=assistant_message_id,
        )

    def _section_for(self, key, title, content):
        if key == "final_writer":
            final_content = content if content.startswith("#") else f"# 决策书\n\n{content}"
            return f"\n\n{FINAL_DECISION_MARKER}\n\n{final_content}"
        return f"\n### {title}\n\n{content}\n"

    def _format_conversation_context(self, history):
        lines = []
        for message in history:
            role = "User" if message["role"] == "user" else "Assistant"
            content = message["content"].strip()
            if content:
                lines.append(f"{role}: {content}")
        return "\n\n".join(lines) or "No prior context."

    def _format_step_outputs(self, outputs):
        if not outputs:
            return "No prior audit output."
        return "\n\n".join(f"### {key}\n{value}" for key, value in outputs.items())

    def _final_writer_prompt(self, conversation_context, step_outputs):
        return (
            "你是 Decision Arena 的行动型决策书编辑器。把内部台账转化为清楚、克制、可立即执行的方案。\n\n"
            "你现在输出的内容将直接展示给决策者。\n\n"
            "你不是辩论记录员，不展示 Agent 过程，不解释“谁说了什么”。"
            "证据只约束判断，不占据正文；不得展示证据编号、Agent 名称、交叉验证过程、"
            "可追溯性清单、内部异议台账或自检记录。\n\n"
            "先直接回答用户真正的问题，再给行动方案。语气应像可靠的决策顾问，而不是审批者或审判者。"
            "不要为了显得严谨而重复证明结论，不要把同一风险写进多个章节。\n\n"
            "判断规则：\n"
            "1. 只有信息不足确实阻止下一步行动时才使用“暂缓”。"
            "如果仍可采取低风险、可逆行动，应明确写“可以先做什么，同时补充什么信息”。\n"
            "2. 不得把局部信息缺失夸大为方案完全不可执行；区分“无法判断最终成功率”和“无法开始行动”。\n"
            "3. 保留用户方案中合理的部分，明确指出哪些保留、哪些调整、为什么调整。\n"
            "4. 计划强度应从用户已知时间和能力推导。没有依据时给建议区间或试行值，不得强制每日时长。\n"
            "5. 禁止擅自引入方案自动关闭、书面确认风险、第三方监督、目标分数上调、采购限制，"
            "以及“致命”“必然失败”等绝对化机制或措辞。\n"
            "6. 数字、期限和门槛仅在帮助执行时出现；无来源时说明是建议起点，并允许根据复盘调整。\n\n"
            "严格按以下结构输出，不能省略任何一级标题：\n\n"
            "# 决策书：<主题>\n\n"
            "## 结论\n"
            "用 2 至 4 句话直接回答：是否可行、可行到什么程度、当前最重要的调整是什么。"
            "若无法判断最终成功率，也要区分哪些行动现在可以开始。\n\n"
            "## 方案调整\n"
            "优先用简洁表格写：事项｜当前做法｜建议调整｜调整原因。"
            "只写会改变用户行动的内容，并明确保留哪些原计划。\n\n"
            "## 近期行动\n"
            "给出从明天或本周即可开始的任务。根据场景选择清单或表格，写清任务、投入、完成标准。"
            "默认给 7 天以内的可执行安排；只有用户明确需要时才扩展为长期计划。\n\n"
            "## 复盘与调整\n"
            "写清何时复盘、看哪些可观察结果，以及结果良好、部分受阻、明显不适配时分别怎么调整。"
            "优先调整任务量、方法和节奏，不轻易把正常困难升级为终止。\n\n"
            "## 待补信息\n"
            "只列会显著改变建议的信息，并说明补充后将影响哪项判断。"
            "如果没有必须补充的信息，写“当前信息足以开始执行”。\n\n"
            "正文应短于内部台账，避免审计腔、管理咨询套话和重复结论。"
            "不要输出证据来源、风险来源、未解决异议、可追溯性或自检确认章节。\n\n"
            f"对话上下文：\n{conversation_context}\n\n"
            f"内部决策台账：\n{self._format_step_outputs(step_outputs)}"
        )
