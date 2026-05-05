import json
from typing import Any

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.api import FunctionTool, logger


@dataclass
class GetPersonaDetailTool(FunctionTool):
    main_plugin: Any = Field(repr=False)
    name: str = "get_persona_detail"
    description: str = "获取指定ID的人格的当前 system_prompt。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "persona_id": {
                    "type": "string",
                    "description": "要查询的人格的ID。",
                }
            },
            "required": ["persona_id"],
        }
    )

    async def call(self, context, **kwargs):
        persona_id = kwargs.get("persona_id")
        logger.info(f"[Tool] GetPersonaDetailTool: 查询人格 '{persona_id}' 的提示词")

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


@dataclass
class UpdatePersonaDetailsTool(FunctionTool):
    main_plugin: Any = Field(repr=False)
    name: str = "update_persona_details"
    description: str = "仅更新指定ID人格的 system_prompt。"
    parameters: dict = Field(
        default_factory=lambda: {
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
            },
            "required": ["persona_id", "system_prompt"],
        }
    )

    async def call(self, context, **kwargs):
        persona_id = kwargs.get("persona_id")
        system_prompt = kwargs.get("system_prompt")

        logger.info(
            "[Tool] UpdatePersonaDetailsTool: 更新人格 '%s' - system_prompt: %s",
            persona_id,
            "system_prompt" in kwargs,
        )

        if not isinstance(system_prompt, str) or not system_prompt.strip():
            error_msg = "system_prompt 必须是非空字符串"
            logger.warning(f"[Tool] UpdatePersonaDetailsTool: {error_msg}")
            return json.dumps({"ok": False, "error": error_msg}, ensure_ascii=False)

        system_prompt = system_prompt.strip()

        try:
            persona = await self.main_plugin.context.persona_manager.update_persona(
                persona_id,
                system_prompt=system_prompt,
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
