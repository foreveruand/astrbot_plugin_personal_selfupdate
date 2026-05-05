import json
from pathlib import Path

from astrbot.api import AstrBotConfig, ToolSet, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse
from astrbot.api.star import Context, Star, register

from .core.tools import GetPersonaDetailTool, UpdatePersonaDetailsTool

SYSTEM_PROMPT_TEMPLATE = """你是人格配置专家，负责根据用户要求更新 AI 人格设定。
可用工具：
- get_persona_detail(persona_id): 获取人格当前设定 - 必须先调用
- update_persona_details(persona_id, system_prompt?, begin_dialogs?, tools?, skills?, custom_error_message?): 更新人格设定,begin_dialogs为偶数个字符串，每个字符串代表一个对话，用户和助手轮流对话

任务：更新人格 '{persona_id}'，要求：{update_requirement}

重要：你必须严格按以下步骤执行：
1. 调用 get_persona_detail 获取当前人格信息
2. 根据要求分析需要修改的内容
3. 调用 update_persona_details 应用修改
4. 简洁总结修改内容

请严格按照上述流程执行。特别注意：
- begin_dialogs 必须包含偶数条对话，且需按照“用户、助手”轮流排列。
- 除非用户明确要求，否则不要修改无关字段；应尽量保留现有人格结构。
- 只有在完成分析并确定改动后，才调用一次 update_persona_details 应用修改。

完成所有步骤后，请以 '{completion_sentinel}' 开头提供最终总结，简要说明修改内容及影响。

请立即开始执行，先调用 get_persona_detail 工具。"""

DEFAULT_USER_PROMPT = "开始执行。"
TOOL_CALL_PLACEHOLDER_PROMPT = " "
MAX_AGENT_ITERATIONS = 10
COMPLETION_SENTINEL = "[AGENT_DONE]"  # Agent completion marker


class ProviderResolutionError(Exception):
    """Raised when an active provider cannot be resolved."""


class AgentExecutionError(Exception):
    """Raised when the agent loop cannot complete successfully."""


@register(
    "personal_selfupdate",
    "kterna",
    "通过与LLM对话来更新人格",
    "0.2.0",
    "https://github.com/kterna/astrbot_plugin_personal_selfupdate",
)
class Main(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        """
        插件初始化
        """
        super().__init__(context)
        self.config = config
        self._persona_cache = {}

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("人格更新", "persona update")
    async def persona_self_update(self, event: AstrMessageEvent):
        """
        通过独立的Agent流程，让LLM自我更新人格。
        用法: /人格更新 [人格ID] [更新要求]
        例如: /人格更新 伯特 让他说话更专业一些
        """
        try:
            persona_id, update_requirement = self._parse_update_request(event)
        except ValueError as error:
            yield event.plain_result(str(error))
            return

        self._reset_persona_cache()

        logger.info(
            f"收到人格更新命令. ID: '{persona_id}', 要求: '{update_requirement}'"
        )

        tool_set = self._build_tool_set(event)

        try:
            provider, model_name = self._resolve_provider(event)
        except ProviderResolutionError as error:
            yield event.plain_result(f"获取服务提供商失败: {error}")
            return

        system_prompt = self._build_system_prompt(persona_id, update_requirement)
        user_prompt = self._initial_user_prompt()

        logger.info("开始调用 LLM Agent 进行人格更新")
        yield event.plain_result("🔄 分析中...")

        try:
            final_text = await self._run_agent_conversation(
                provider=provider,
                model_name=model_name,
                tool_set=tool_set,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            yield event.plain_result(f"✅ 更新完成\n{final_text}")
        except AgentExecutionError as error:
            logger.error(f"执行人格更新 Agent 流程时出错: {error}", exc_info=True)
            yield event.plain_result(f"❌ 更新失败: {error}")
        except Exception as error:
            logger.error(f"执行人格更新 Agent 流程时出错: {error}", exc_info=True)
            yield event.plain_result(f"❌ 更新失败: {error}")

    def _parse_update_request(self, event: AstrMessageEvent) -> tuple[str, str]:
        raw_message = event.message_str.strip()
        parts = raw_message.split(None, 2) if raw_message else []

        if len(parts) < 3:
            raise ValueError("参数不足，请提供人格ID和更新要求。")

        _, persona_id, update_requirement = parts
        persona_id = persona_id.strip()
        update_requirement = update_requirement.strip()

        if not persona_id:
            raise ValueError("人格ID 不能为空，请重新输入。")

        if not update_requirement:
            raise ValueError("更新要求不能为空，请提供具体说明。")

        return persona_id, update_requirement

    def _build_tool_set(self, event: AstrMessageEvent) -> ToolSet:
        return ToolSet(
            [
                GetPersonaDetailTool(main_plugin=self, event=event),
                UpdatePersonaDetailsTool(main_plugin=self, event=event),
            ]
        )

    def _resolve_provider(self, event: AstrMessageEvent) -> tuple[object, str | None]:
        provider_id = str(self.config.get("provider", "") or "").strip()
        model_name = self.config.get("model", "")
        model_name = str(model_name) if model_name and model_name != "" else None

        try:
            provider_instance = None

            if provider_id:
                provider_instance = self.context.get_provider_by_id(
                    provider_id=provider_id
                )
                if not provider_instance:
                    logger.warning(
                        f"指定的 Provider '{provider_id}' 不存在或未启用，使用默认 provider"
                    )
            else:
                target_config = self._resolve_target_astrbot_config(event)
                target_provider_id = (
                    target_config.get("provider_settings", {}).get(
                        "default_provider_id", ""
                    )
                    if target_config
                    else ""
                )
                target_provider_id = str(target_provider_id or "").strip()

                if target_provider_id:
                    provider_instance = self.context.get_provider_by_id(
                        provider_id=target_provider_id
                    )
                    if not provider_instance:
                        logger.warning(
                            "指定的 AstrBot 配置默认 Provider '%s' 不存在或未启用，使用当前会话 Provider",
                            target_provider_id,
                        )

            if not provider_instance:
                provider_instance = self.context.get_using_provider(
                    umo=event.unified_msg_origin
                )

        except Exception as error:
            logger.error(f"获取服务提供商失败: {error}", exc_info=True)
            raise ProviderResolutionError(str(error)) from error

        if not provider_instance:
            message = "无法获取有效的服务提供商。请检查是否有启用的 Provider。"
            logger.error(f"获取服务提供商失败: {message}")
            raise ProviderResolutionError(message)

        return provider_instance, model_name

    def _resolve_target_astrbot_config(
        self, event: AstrMessageEvent
    ) -> AstrBotConfig | None:
        config_name = str(self.config.get("astrbot_config", "") or "").strip()
        if not config_name:
            return self.context.get_config(event.unified_msg_origin)

        config_mgr = getattr(self.context, "astrbot_config_mgr", None)
        if not config_mgr:
            logger.warning("AstrBotConfigManager 不可用，回退到当前会话配置")
            return self.context.get_config(event.unified_msg_origin)

        confs = getattr(config_mgr, "confs", {}) or {}
        target_name = Path(config_name).name

        for conf_info in config_mgr.get_conf_list():
            conf_id = conf_info.get("id")
            if conf_id not in confs:
                continue

            display_name = str(conf_info.get("name", "") or "").strip()
            path_name = Path(str(conf_info.get("path", "") or "")).name
            candidates = {conf_id, display_name, path_name}

            if config_name in candidates or target_name in candidates:
                return confs[conf_id]

        logger.warning(
            "未找到 AstrBot 配置 '%s'，回退到当前会话配置",
            config_name,
        )
        return self.context.get_config(event.unified_msg_origin)

    def _build_system_prompt(self, persona_id: str, update_requirement: str) -> str:
        return SYSTEM_PROMPT_TEMPLATE.format(
            persona_id=persona_id,
            update_requirement=update_requirement,
            completion_sentinel=COMPLETION_SENTINEL,
        )

    def _initial_user_prompt(self) -> str:
        return DEFAULT_USER_PROMPT

    async def _run_agent_conversation(
        self,
        provider,
        model_name: str | None,
        tool_set: ToolSet,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        logger.info("开始 LLM Agent 工具调用...")

        max_iterations = MAX_AGENT_ITERATIONS
        messages: list[dict] = []
        current_prompt = user_prompt
        final_text = ""

        try:
            for _ in range(max_iterations):
                response: LLMResponse = await provider.text_chat(
                    prompt=current_prompt,
                    system_prompt=system_prompt,
                    model=model_name or None,
                    func_tool=tool_set,
                    session_id=None,
                    contexts=messages,
                    image_urls=[],
                )

                messages.append({"role": "user", "content": current_prompt})

                if (
                    hasattr(response, "tools_call_name")
                    and hasattr(response, "tools_call_args")
                    and response.tools_call_name
                    and response.tools_call_args
                ):
                    tool_results = []
                    tool_call_ids = getattr(
                        response,
                        "tools_call_ids",
                        [
                            f"{name}:{i}"
                            for i, name in enumerate(response.tools_call_name)
                        ],
                    )

                    for tool_name, tool_args, tool_id in zip(
                        response.tools_call_name,
                        response.tools_call_args,
                        tool_call_ids,
                    ):
                        tool = tool_set.get_tool(tool_name)
                        if tool and tool.handler:
                            try:
                                result = await tool.handler(**tool_args)
                                tool_results.append(
                                    {
                                        "tool_call_id": tool_id,
                                        "role": "tool",
                                        "content": str(result),
                                    }
                                )
                            except Exception as error:
                                logger.error(f"工具 {tool_name} 执行失败: {error}")
                                tool_results.append(
                                    {
                                        "tool_call_id": tool_id,
                                        "role": "tool",
                                        "content": f"工具执行失败: {error}",
                                    }
                                )
                        else:
                            logger.error(f"未找到工具: {tool_name}")
                            tool_results.append(
                                {
                                    "tool_call_id": tool_id,
                                    "role": "tool",
                                    "content": f"未找到工具: {tool_name}",
                                }
                            )

                    assistant_content = "调用工具"
                    if response.result_chain and response.result_chain.chain:
                        text = response.result_chain.chain[0].text
                        if text and text.strip():
                            assistant_content = text

                    messages.append(
                        {
                            "role": "assistant",
                            "content": assistant_content,
                            "tool_calls": [
                                {
                                    "id": tool_id,
                                    "type": "function",
                                    "function": {
                                        "name": tool_name,
                                        "arguments": json.dumps(tool_args)
                                        if isinstance(tool_args, dict)
                                        else str(tool_args),
                                    },
                                }
                                for tool_name, tool_args, tool_id in zip(
                                    response.tools_call_name,
                                    response.tools_call_args,
                                    tool_call_ids,
                                )
                            ],
                        }
                    )

                    messages.extend(tool_results)
                    current_prompt = TOOL_CALL_PLACEHOLDER_PROMPT
                    continue

                final_text = (
                    response.completion_text
                    if hasattr(response, "completion_text")
                    else ""
                )
                if response.result_chain and response.result_chain.chain:
                    final_text = response.result_chain.chain[0].text

                break
            else:
                final_text = "工具调用超过最大次数限制"
                logger.warning("工具调用循环达到最大次数限制")
        except Exception as error:
            raise AgentExecutionError(str(error)) from error

        return self._extract_completion_text(final_text)

    def _reset_persona_cache(self) -> None:
        """Clear per-command persona cache so each invocation starts fresh."""
        self._persona_cache.clear()

    def get_cached_persona_detail(self, persona_id: str) -> dict | None:
        return self._persona_cache.get(persona_id)

    def cache_persona_detail(self, persona_id: str, detail: dict) -> None:
        self._persona_cache[persona_id] = detail

    def _extract_completion_text(self, raw_text: str) -> str:
        if not raw_text:
            return raw_text

        text = raw_text.strip()

        if COMPLETION_SENTINEL in text:
            _, remainder = text.split(COMPLETION_SENTINEL, 1)
            remainder = remainder.strip()
            return remainder if remainder else COMPLETION_SENTINEL

        return text
