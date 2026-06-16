from dataclasses import dataclass


@dataclass(frozen=True)
class DebateAgent:
    key: str
    title: str
    mission: str


AGENTS = [
    DebateAgent("supporter", "支持者", "尽力论证方案值得推进，找出收益、机会和正向路径。"),
    DebateAgent("opponent", "反对者", "专门寻找风险、代价、幻觉、自欺和失败条件。"),
    DebateAgent("realist", "现实主义者", "从时间、钱、资源、执行难度和依赖条件判断可行性。"),
    DebateAgent("long_termist", "长期主义者", "评估三个月、一年、三年后的收益、路径依赖和机会成本。"),
    DebateAgent("emotion_observer", "情绪观察者", "识别冲动、逃避、焦虑、成就感和关系压力对判断的污染。"),
    DebateAgent("technical_reviewer", "技术评审", "评估工程复杂度、依赖风险、部署路线和最小实现路径。"),
]


class DebateEngine:
    def __init__(self, store, provider):
        self.store = store
        self.provider = provider

    async def run(self, conversation_id, assistant_message_id, history):
        user_question = self._latest_user_question(history)

        clarification = await self._run_step(
            conversation_id,
            assistant_message_id,
            key="clarifier",
            title="问题澄清",
            mode="debate.clarifier",
            prompt=(
                "把用户的决策问题改写成结构化格式：目标、候选方案、约束、评价维度、"
                "缺失信息。不要直接给最终建议。\n\n"
                f"用户问题：{user_question}"
            ),
        )

        agent_outputs = {}
        await self._append_assistant_delta(conversation_id, assistant_message_id, "\n## 独立 Agent 分析\n")
        for agent in AGENTS:
            output = await self._run_step(
                conversation_id,
                assistant_message_id,
                key=agent.key,
                title=agent.title,
                mode=f"agent.{agent.key}",
                prompt=(
                    f"你是{agent.title}。你的任务：{agent.mission}\n"
                    "你只能基于原始问题和问题澄清独立分析，暂时不要参考其他 agent。\n\n"
                    f"原始问题：{user_question}\n\n"
                    f"问题澄清：{clarification}"
                ),
            )
            agent_outputs[agent.key] = output

        cross_examination = await self._run_step(
            conversation_id,
            assistant_message_id,
            key="cross_examination",
            title="交叉质询",
            mode="debate.cross_examination",
            prompt=(
                "阅读所有 agent 的独立分析，输出交叉质询：每个视角同意哪里、反驳哪里、"
                "忽略了什么、如果自己错了最可能错在哪里。\n\n"
                f"{self._format_agent_outputs(agent_outputs)}"
            ),
        )

        scoring_matrix = await self._run_step(
            conversation_id,
            assistant_message_id,
            key="scoring_matrix",
            title="评分矩阵",
            mode="debate.scoring_matrix",
            prompt=(
                "基于问题澄清、独立分析和交叉质询，输出 Markdown 评分矩阵。"
                "维度至少包括长期收益、短期成本、机会成本、失败风险、可逆性、"
                "主线一致性、情绪污染、信息充分度、MVP 可行性。给出权重、分数和简短理由。\n\n"
                f"问题澄清：{clarification}\n\n"
                f"独立分析：\n{self._format_agent_outputs(agent_outputs)}\n\n"
                f"交叉质询：{cross_examination}"
            ),
        )

        await self._run_step(
            conversation_id,
            assistant_message_id,
            key="judge",
            title="裁判总结",
            mode="debate.judge",
            prompt=(
                "你是裁判 agent。综合前面所有阶段，输出：当前最优建议、置信度、主要理由、"
                "最大风险、最容易自欺的地方、最小可行动作、止损条件、三天后复盘问题。"
                "不要只按多数投票，要说明权衡。\n\n"
                f"问题澄清：{clarification}\n\n"
                f"独立分析：\n{self._format_agent_outputs(agent_outputs)}\n\n"
                f"交叉质询：{cross_examination}\n\n"
                f"评分矩阵：{scoring_matrix}"
            ),
        )

        await self.store.update_message_status(assistant_message_id, "complete")
        await self.store.add_event(
            conversation_id,
            "assistant_message_completed",
            {"message_id": assistant_message_id, "mode": "expert"},
            message_id=assistant_message_id,
        )

    async def _run_step(self, conversation_id, assistant_message_id, key, title, mode, prompt):
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

    async def _append_assistant_delta(self, conversation_id, assistant_message_id, content):
        await self.store.append_message(assistant_message_id, content)
        await self.store.add_event(
            conversation_id,
            "assistant_delta",
            {"delta": content},
            message_id=assistant_message_id,
        )

    def _section_for(self, key, title, content):
        if key in {agent.key for agent in AGENTS}:
            return f"\n### {title}\n\n{content}\n"
        return f"\n## {title}\n\n{content}\n"

    def _latest_user_question(self, history):
        for message in reversed(history):
            if message["role"] == "user":
                return message["content"]
        return ""

    def _format_agent_outputs(self, outputs):
        sections = []
        for agent in AGENTS:
            sections.append(f"### {agent.title}\n{outputs.get(agent.key, '')}")
        return "\n\n".join(sections)
