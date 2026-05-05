import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, ToolSet, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .core.tools import GetPersonaDetailTool, UpdatePersonaDetailsTool

SYSTEM_PROMPT_TEMPLATE = """你是人格配置专家，负责根据用户要求更新 AI 人格设定中的 system_prompt。
可用工具：
- get_persona_detail(persona_id): 获取人格当前的 system_prompt - 必须先调用
- update_persona_details(persona_id, system_prompt): 仅更新人格的 system_prompt

任务：更新人格 '{persona_id}'，要求：{update_requirement}

重要：你必须严格按以下步骤执行：
1. 调用 get_persona_detail 获取当前人格信息
2. 根据要求分析需要修改的内容
3. 仅调用 update_persona_details 更新 system_prompt
4. 简洁总结修改内容

请严格按照上述流程执行。特别注意：
- 你只能修改 system_prompt，不允许修改 begin_dialogs、tools、skills、custom_error_message 等其他字段。
- 除非用户明确要求，否则不要改写无关设定；应尽量保留现有人格结构和原意。
- 只有在完成分析并确定改动后，才调用一次 update_persona_details 应用修改。

完成所有步骤后，请以 '{completion_sentinel}' 开头提供最终总结，简要说明修改内容及影响。

请立即开始执行，先调用 get_persona_detail 工具。"""

DEFAULT_USER_PROMPT = "开始执行。"
MAX_AGENT_ITERATIONS = 10
COMPLETION_SENTINEL = "[AGENT_DONE]"  # Agent completion marker
DEFAULT_HISTORY_LIMIT = 5
HISTORY_FILE_NAME = "persona_history.json"


class ProviderResolutionError(Exception):
    """Raised when an active provider cannot be resolved."""


class AgentExecutionError(Exception):
    """Raised when the agent loop cannot complete successfully."""


@register(
    "personal_selfupdate",
    "kterna",
    "通过与LLM对话来更新人格",
    "0.3.1",
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
        self._plugin_data_dir = Path(get_astrbot_plugin_data_path()) / self.name
        self._plugin_data_dir.mkdir(parents=True, exist_ok=True)
        self._history_file = self._plugin_data_dir / HISTORY_FILE_NAME

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

        try:
            original_persona = await self.context.persona_manager.get_persona(
                persona_id
            )
        except Exception as error:
            yield event.plain_result(f"获取人格失败: {error}")
            return

        original_snapshot = self._persona_to_snapshot(original_persona)
        self._reset_persona_cache()

        logger.info(
            f"收到人格更新命令. ID: '{persona_id}', 要求: '{update_requirement}'"
        )

        tool_set = self._build_tool_set(event)

        try:
            provider_id = self._resolve_provider_id(event)
        except ProviderResolutionError as error:
            yield event.plain_result(f"获取服务提供商失败: {error}")
            return

        system_prompt = self._build_system_prompt(persona_id, update_requirement)
        user_prompt = self._initial_user_prompt()

        logger.info("开始调用 LLM Agent 进行人格更新")
        yield event.plain_result("🔄 分析中...")

        try:
            final_text = await self._run_agent_conversation(
                event=event,
                provider_id=provider_id,
                tool_set=tool_set,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            updated_persona = await self.context.persona_manager.get_persona(persona_id)
            updated_snapshot = self._persona_to_snapshot(updated_persona)
            if not self._snapshots_equal(original_snapshot, updated_snapshot):
                self._record_persona_history(
                    persona_id=persona_id,
                    snapshot=original_snapshot,
                    source_action="update",
                    update_requirement=update_requirement,
                    summary=final_text,
                )
            yield event.plain_result(f"✅ 更新完成\n{final_text}")
        except AgentExecutionError as error:
            logger.error(f"执行人格更新 Agent 流程时出错: {error}", exc_info=True)
            yield event.plain_result(f"❌ 更新失败: {error}")
        except Exception as error:
            logger.error(f"执行人格更新 Agent 流程时出错: {error}", exc_info=True)
            yield event.plain_result(f"❌ 更新失败: {error}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("人格历史", "persona history")
    async def persona_history(self, event: AstrMessageEvent):
        try:
            persona_id = self._parse_history_request(event)
        except ValueError as error:
            yield event.plain_result(str(error))
            return

        history = self._get_persona_history(persona_id)
        if not history:
            yield event.plain_result(f"人格 '{persona_id}' 暂无历史记录。")
            return

        lines = [f"人格 '{persona_id}' 的最近 {len(history)} 条历史记录："]
        for index, entry in enumerate(history, start=1):
            snapshot = entry.get("snapshot", {})
            prompt_preview = self._truncate_text(
                str(snapshot.get("system_prompt", "") or ""),
                80,
            )
            reason = self._truncate_text(
                str(
                    entry.get("update_requirement")
                    or entry.get("summary")
                    or entry.get("source_action")
                    or ""
                ),
                60,
            )
            lines.append(
                f"{index}. {entry.get('recorded_at', 'unknown')} [{entry.get('source_action', 'unknown')}]"
            )
            lines.append(f"prompt: {prompt_preview}")
            if reason:
                lines.append(f"说明: {reason}")

        lines.append(f"使用 /人格回滚 {persona_id} <序号> 预览并选择是否回滚。")
        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("人格回滚", "persona rollback")
    async def persona_rollback(self, event: AstrMessageEvent):
        try:
            persona_id, history_index, confirmed = self._parse_rollback_request(event)
        except ValueError as error:
            yield event.plain_result(str(error))
            return

        history = self._get_persona_history(persona_id)
        if not history:
            yield event.plain_result(f"人格 '{persona_id}' 暂无历史记录。")
            return

        if history_index < 1 or history_index > len(history):
            yield event.plain_result(
                f"历史序号超出范围。当前共有 {len(history)} 条历史记录。"
            )
            return

        entry = history[history_index - 1]
        snapshot = entry.get("snapshot")
        if not isinstance(snapshot, dict):
            yield event.plain_result("历史记录损坏：缺少可回滚的 snapshot。")
            return

        if not confirmed:
            yield event.plain_result(
                self._render_history_preview(persona_id, history_index, entry)
            )
            return

        try:
            current_persona = await self.context.persona_manager.get_persona(persona_id)
        except Exception as error:
            yield event.plain_result(f"获取当前人格失败: {error}")
            return

        current_snapshot = self._persona_to_snapshot(current_persona)
        if self._snapshots_equal(current_snapshot, snapshot):
            yield event.plain_result("当前人格已经与目标历史记录一致，无需回滚。")
            return

        self._record_persona_history(
            persona_id=persona_id,
            snapshot=current_snapshot,
            source_action="rollback",
            update_requirement=f"rollback to history #{history_index}",
            summary=(
                f"Rollback to history #{history_index} "
                f"recorded at {entry.get('recorded_at', 'unknown')}"
            ),
        )

        try:
            await self.context.persona_manager.update_persona(
                persona_id,
                system_prompt=str(snapshot.get("system_prompt", "") or ""),
                begin_dialogs=self._normalize_dialogs(snapshot.get("begin_dialogs")),
                tools=self._normalize_optional_string_list(snapshot.get("tools")),
                skills=self._normalize_optional_string_list(snapshot.get("skills")),
                custom_error_message=self._normalize_optional_string(
                    snapshot.get("custom_error_message")
                ),
            )
            self._reset_persona_cache()
        except Exception as error:
            logger.error(f"执行人格回滚失败: {error}", exc_info=True)
            yield event.plain_result(f"执行人格回滚失败: {error}")
            return

        yield event.plain_result(
            f"✅ 已将人格 '{persona_id}' 回滚到历史记录 #{history_index}。\n"
            f"如需查看当前历史，可执行 /人格历史 {persona_id}"
        )

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

    def _parse_history_request(self, event: AstrMessageEvent) -> str:
        raw_message = event.message_str.strip()
        parts = raw_message.split(None, 1) if raw_message else []

        if len(parts) < 2:
            raise ValueError("参数不足，请提供人格ID。")

        persona_id = parts[1].strip()
        if not persona_id:
            raise ValueError("人格ID 不能为空，请重新输入。")

        return persona_id

    def _parse_rollback_request(self, event: AstrMessageEvent) -> tuple[str, int, bool]:
        raw_message = event.message_str.strip()
        parts = raw_message.split() if raw_message else []

        if len(parts) < 3:
            raise ValueError("参数不足。用法: /人格回滚 <人格ID> <历史序号> [确认]")

        persona_id = parts[1].strip()
        if not persona_id:
            raise ValueError("人格ID 不能为空，请重新输入。")

        try:
            history_index = int(parts[2])
        except ValueError as error:
            raise ValueError("历史序号必须是整数。") from error

        confirmed = len(parts) >= 4 and parts[3].strip() in {"确认", "confirm", "yes"}
        return persona_id, history_index, confirmed

    def _build_tool_set(self, event: AstrMessageEvent) -> ToolSet:
        return ToolSet(
            [
                GetPersonaDetailTool(main_plugin=self),
                UpdatePersonaDetailsTool(main_plugin=self),
            ]
        )

    def _resolve_provider_id(self, event: AstrMessageEvent) -> str:
        provider_id = str(self.config.get("provider", "") or "").strip()

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

        return provider_instance.meta().id

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
        event: AstrMessageEvent,
        provider_id: str,
        tool_set: ToolSet,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        logger.info("开始 LLM Agent 工具调用...")

        try:
            response: LLMResponse = await self.context.tool_loop_agent(
                event=event,
                chat_provider_id=provider_id,
                prompt=user_prompt,
                tools=tool_set,
                system_prompt=system_prompt,
                max_steps=MAX_AGENT_ITERATIONS,
            )
        except Exception as error:
            raise AgentExecutionError(str(error)) from error

        final_text = response.completion_text or ""
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

    def _get_history_limit(self) -> int:
        value = self.config.get("history_limit", DEFAULT_HISTORY_LIMIT)
        try:
            limit = int(value)
        except (TypeError, ValueError):
            logger.warning(
                "history_limit 配置非法，回退到默认值 %s", DEFAULT_HISTORY_LIMIT
            )
            return DEFAULT_HISTORY_LIMIT
        return max(limit, 0)

    def _load_history_store(self) -> dict[str, list[dict[str, Any]]]:
        if not self._history_file.exists():
            return {}

        try:
            raw = json.loads(self._history_file.read_text(encoding="utf-8"))
        except Exception as error:
            logger.error(f"读取人格历史文件失败: {error}", exc_info=True)
            return {}

        if not isinstance(raw, dict):
            logger.warning("人格历史文件格式非法，已忽略。")
            return {}

        history_store: dict[str, list[dict[str, Any]]] = {}
        for persona_id, entries in raw.items():
            if isinstance(persona_id, str) and isinstance(entries, list):
                history_store[persona_id] = [
                    entry for entry in entries if isinstance(entry, dict)
                ]
        return history_store

    def _save_history_store(self, store: dict[str, list[dict[str, Any]]]) -> None:
        self._plugin_data_dir.mkdir(parents=True, exist_ok=True)
        self._history_file.write_text(
            json.dumps(store, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _get_persona_history(self, persona_id: str) -> list[dict[str, Any]]:
        store = self._load_history_store()
        entries = store.get(persona_id, [])
        return entries if isinstance(entries, list) else []

    def _record_persona_history(
        self,
        persona_id: str,
        snapshot: dict[str, Any],
        source_action: str,
        update_requirement: str,
        summary: str,
    ) -> None:
        limit = self._get_history_limit()
        if limit <= 0:
            return

        store = self._load_history_store()
        entries = store.setdefault(persona_id, [])
        entries.insert(
            0,
            {
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "source_action": source_action,
                "update_requirement": update_requirement,
                "summary": summary,
                "snapshot": snapshot,
            },
        )
        store[persona_id] = entries[:limit]
        self._save_history_store(store)

    def _persona_to_snapshot(self, persona: object) -> dict[str, Any]:
        return {
            "persona_id": getattr(persona, "persona_id", ""),
            "system_prompt": getattr(persona, "system_prompt", "") or "",
            "begin_dialogs": self._normalize_dialogs(
                getattr(persona, "begin_dialogs", None)
            ),
            "tools": self._normalize_optional_string_list(
                getattr(persona, "tools", None)
            ),
            "skills": self._normalize_optional_string_list(
                getattr(persona, "skills", None)
            ),
            "custom_error_message": self._normalize_optional_string(
                getattr(persona, "custom_error_message", None)
            ),
        }

    def _normalize_dialogs(self, dialogs: object) -> list[str] | None:
        if dialogs is None:
            return None
        if not isinstance(dialogs, list):
            return None
        return [str(item) for item in dialogs if isinstance(item, str)]

    def _normalize_optional_string_list(self, value: object) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            return None
        return [str(item) for item in value if isinstance(item, str)]

    def _normalize_optional_string(self, value: object) -> str | None:
        if value is None:
            return None
        return str(value).strip() or None

    def _snapshots_equal(self, left: dict[str, Any], right: dict[str, Any]) -> bool:
        return json.dumps(left, sort_keys=True, ensure_ascii=False) == json.dumps(
            right,
            sort_keys=True,
            ensure_ascii=False,
        )

    def _truncate_text(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return f"{text[: limit - 3]}..."

    def _render_history_preview(
        self, persona_id: str, history_index: int, entry: dict[str, Any]
    ) -> str:
        snapshot = entry.get("snapshot", {})
        lines = [
            f"人格 '{persona_id}' 历史记录 #{history_index}",
            f"记录时间: {entry.get('recorded_at', 'unknown')}",
            f"来源操作: {entry.get('source_action', 'unknown')}",
        ]

        requirement = str(entry.get("update_requirement", "") or "").strip()
        if requirement:
            lines.append(f"变更说明: {requirement}")

        summary = str(entry.get("summary", "") or "").strip()
        if summary:
            lines.append(f"摘要: {summary}")

        lines.extend(
            [
                "",
                "历史 system_prompt:",
                str(snapshot.get("system_prompt", "") or ""),
                "",
                f"begin_dialogs 条数: {len(snapshot.get('begin_dialogs') or [])}",
                f"tools: {json.dumps(snapshot.get('tools'), ensure_ascii=False)}",
                f"skills: {json.dumps(snapshot.get('skills'), ensure_ascii=False)}",
                "custom_error_message: "
                f"{snapshot.get('custom_error_message') or '(empty)'}",
                "",
                f"确认回滚请执行: /人格回滚 {persona_id} {history_index} 确认",
            ]
        )
        return "\n".join(lines)
