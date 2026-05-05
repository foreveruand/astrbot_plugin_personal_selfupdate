import json

from astrbot.api import FunctionTool, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.sentinels import NOT_GIVEN

# Forward declaration for type hinting
if False:
    from ..main import Main


class GetPersonaDetailTool(FunctionTool):
    def __init__(self, main_plugin: "Main", event: "AstrMessageEvent"):
        self.main_plugin = main_plugin
        self.event = event

        super().__init__(
            name="get_persona_detail",
            description="获取指定ID的人格的详细信息。",
            parameters={
                "type": "object",
                "properties": {
                    "persona_id": {
                        "type": "string",
                        "description": "要查询的人格的ID。",
                    }
                },
                "required": ["persona_id"],
            },
            handler=self._run_handler,
            handler_module_path=__name__,
        )

    async def _run_handler(self, **kwargs):
        persona_id = kwargs.get("persona_id")
        logger.info(f"[Tool] GetPersonaDetailTool: 查询人格 '{persona_id}' 的详细信息")

        cached_persona = self.main_plugin.get_cached_persona_detail(persona_id)
        if cached_persona is not None:
            logger.info(
                f"[Tool] GetPersonaDetailTool: 使用缓存的人格 '{persona_id}' 信息"
            )
            return json.dumps(
                {"ok": True, "persona": cached_persona}, ensure_ascii=False
            )

        try:
            persona = await self.main_plugin.context.persona_manager.get_persona(
                persona_id
            )
            if not persona:
                raise ValueError("未找到指定人格")
            logger.info(
                f"[Tool] GetPersonaDetailTool: 成功获取人格 '{persona_id}' 信息"
            )
            result = {
                "persona_id": persona_id,
                "system_prompt": getattr(persona, "system_prompt", ""),
                "begin_dialogs": getattr(persona, "begin_dialogs", []),
                "tools": getattr(persona, "tools", None),
                "skills": getattr(persona, "skills", None),
                "custom_error_message": getattr(persona, "custom_error_message", None),
            }
            self.main_plugin.cache_persona_detail(persona_id, result)
            return json.dumps({"ok": True, "persona": result}, ensure_ascii=False)
        except Exception as e:
            logger.error(
                f"[Tool] GetPersonaDetailTool: 获取人格 '{persona_id}' 失败: {e}"
            )
            return json.dumps(
                {"ok": False, "error": str(e), "persona_id": persona_id},
                ensure_ascii=False,
            )


class UpdatePersonaDetailsTool(FunctionTool):
    def __init__(self, main_plugin: "Main", event: "AstrMessageEvent"):
        self.main_plugin = main_plugin
        self.event = event

        super().__init__(
            name="update_persona_details",
            description="更新指定ID的人格信息。只有提供的参数才会被更新。",
            parameters={
                "type": "object",
                "properties": {
                    "persona_id": {
                        "type": "string",
                        "description": "要更新的人格的ID。",
                    },
                    "system_prompt": {
                        "type": "string",
                        "description": "新的系统提示。",
                    },
                    "begin_dialogs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "新的开场白列表。",
                    },
                    "tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "新的工具列表。None表示默认全部，空列表表示无。",
                    },
                    "skills": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "新的技能列表。None表示默认全部，空列表表示无。",
                    },
                    "custom_error_message": {
                        "type": ["string", "null"],
                        "description": "新的自定义报错回复。传 null 可清除。",
                    },
                },
                "required": ["persona_id"],
            },
            handler=self._run_handler,
            handler_module_path=__name__,
        )

    async def _run_handler(self, **kwargs):
        persona_id = kwargs.get("persona_id")
        system_prompt = kwargs.get("system_prompt")
        begin_dialogs = kwargs.get("begin_dialogs")
        tools = kwargs["tools"] if "tools" in kwargs else NOT_GIVEN
        skills = kwargs["skills"] if "skills" in kwargs else NOT_GIVEN
        custom_error_message = (
            kwargs["custom_error_message"]
            if "custom_error_message" in kwargs
            else NOT_GIVEN
        )

        logger.info(
            "[Tool] UpdatePersonaDetailsTool: 更新人格 '%s' - system_prompt: %s, begin_dialogs: %s, tools: %s, skills: %s, custom_error_message: %s",
            persona_id,
            "system_prompt" in kwargs,
            "begin_dialogs" in kwargs,
            "tools" in kwargs,
            "skills" in kwargs,
            "custom_error_message" in kwargs,
        )

        if begin_dialogs is not None:
            if not isinstance(begin_dialogs, list) or any(
                not isinstance(item, str) for item in begin_dialogs
            ):
                error_msg = "begin_dialogs 必须是字符串列表"
                logger.warning(f"[Tool] UpdatePersonaDetailsTool: {error_msg}")
                return json.dumps({"ok": False, "error": error_msg}, ensure_ascii=False)

            if len(begin_dialogs) % 2 != 0:
                error_msg = "begin_dialogs 条目数量必须为偶数，且应按用户/助手顺序排列"
                logger.warning(f"[Tool] UpdatePersonaDetailsTool: {error_msg}")
                return json.dumps({"ok": False, "error": error_msg}, ensure_ascii=False)

        if tools is not NOT_GIVEN and tools is not None:
            if not isinstance(tools, list) or any(
                not isinstance(item, str) for item in tools
            ):
                error_msg = "tools 必须是字符串列表或 null"
                logger.warning(f"[Tool] UpdatePersonaDetailsTool: {error_msg}")
                return json.dumps({"ok": False, "error": error_msg}, ensure_ascii=False)

        if skills is not NOT_GIVEN and skills is not None:
            if not isinstance(skills, list) or any(
                not isinstance(item, str) for item in skills
            ):
                error_msg = "skills 必须是字符串列表或 null"
                logger.warning(f"[Tool] UpdatePersonaDetailsTool: {error_msg}")
                return json.dumps({"ok": False, "error": error_msg}, ensure_ascii=False)

        if custom_error_message is not NOT_GIVEN:
            if custom_error_message is not None and not isinstance(
                custom_error_message, str
            ):
                error_msg = "custom_error_message 必须是字符串或 null"
                logger.warning(f"[Tool] UpdatePersonaDetailsTool: {error_msg}")
                return json.dumps({"ok": False, "error": error_msg}, ensure_ascii=False)
            if isinstance(custom_error_message, str):
                custom_error_message = custom_error_message.strip() or None

        try:
            persona = await self.main_plugin.context.persona_manager.update_persona(
                persona_id,
                system_prompt=system_prompt,
                begin_dialogs=begin_dialogs,
                tools=tools,
                skills=skills,
                custom_error_message=custom_error_message,
            )
            logger.info(f"[Tool] UpdatePersonaDetailsTool: 成功更新人格 '{persona_id}'")
        except Exception as e:
            logger.error(
                f"[Tool] UpdatePersonaDetailsTool: 更新人格 '{persona_id}' 失败: {e}"
            )
            return json.dumps(
                {"ok": False, "error": f"更新失败：{e}"}, ensure_ascii=False
            )

        try:
            persona = (
                persona
                or await self.main_plugin.context.persona_manager.get_persona(
                    persona_id
                )
            )
            if not persona:
                raise ValueError("更新后无法获取人格详情")
            result = {
                "persona_id": persona_id,
                "system_prompt": getattr(persona, "system_prompt", ""),
                "begin_dialogs": getattr(persona, "begin_dialogs", []),
                "tools": getattr(persona, "tools", None),
                "skills": getattr(persona, "skills", None),
                "custom_error_message": getattr(persona, "custom_error_message", None),
            }
            logger.info("[Tool] UpdatePersonaDetailsTool: 返回更新后的人格信息")
            self.main_plugin.cache_persona_detail(persona_id, result)
            return json.dumps({"ok": True, "persona": result}, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[Tool] UpdatePersonaDetailsTool: 获取更新后信息失败: {e}")
            return json.dumps(
                {"ok": False, "error": f"更新成功但获取更新后信息失败：{e}"},
                ensure_ascii=False,
            )
