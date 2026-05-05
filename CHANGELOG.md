# Changelog

## 0.3.1

- Fixed AstrBot tool invocation compatibility by switching plugin tools to the standard `FunctionTool.call(...)` path.
- Restricted persona updates to `system_prompt` only, so the plugin no longer modifies `begin_dialogs`, `tools`, `skills`, or `custom_error_message`.
- Updated README and plugin metadata to match the narrowed update scope.

## 0.3.0

- Added persistent persona history storage under `data/plugin_data/astrbot_plugin_personal_selfupdate/persona_history.json`.
- Added `history_limit` config to control how many recent snapshots are retained per persona.
- Added `/人格历史` to list recent snapshots and `/人格回滚` to preview a stored prompt before confirming rollback.

## 0.2.0

- Updated persona read/write support to include `skills` and `custom_error_message`.
- Updated provider resolution to support direct provider selection and fallback to the default provider from a specified AstrBot config.
- Updated `_conf_schema.json` to use `_special: select_provider` and added `astrbot_config`.
- Updated README and plugin metadata for the current AstrBot interface.
