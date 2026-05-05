# AstrBot 人格自更新插件

该插件通过函数调用驱动的 Agent 流程，让 LLM 先读取再更新 AstrBot 中的指定人格，适用于持续迭代 persona 配置的场景。

**⚠️ 使用前建议先备份人格数据。**
**本插件面向 AstrBot 4.x 当前接口。**

## 功能

- 提供 `/人格更新 <人格ID> <更新要求>` 命令，让模型自动分析并更新人格。
- 兼容当前 AstrBot persona 字段：`system_prompt`、`begin_dialogs`、`tools`、`skills`、`custom_error_message`。
- 支持在插件配置中直接选择 LLM Provider。
- 支持在 `provider` 留空时，通过指定 AstrBot 配置名/ID/文件名读取该配置的默认对话 Provider。
- 支持为每个人格持久化保留最近几次变更历史，并通过命令预览后回滚。

## 安装与启用

1. 将插件目录 `astrbot_plugin_personal_selfupdate` 放入 `data/plugins/`。
2. 在 AstrBot 后台启用该插件。
3. 确保 AstrBot 中已有可用的聊天 Provider。

## 配置项

- `provider`：插件直接使用的 Provider ID。后台已接入 `_special: select_provider` 选择器。
- `astrbot_config`：可选的 AstrBot 配置名、配置 ID 或配置文件名，例如 `default`、`abconf_xxx.json`。仅在 `provider` 留空时生效，用于读取该配置中的 `provider_settings.default_provider_id`。
- `history_limit`：每个人格保留的最近历史条数，默认 `5`。设为 `0` 可关闭历史记录。

Provider 选择顺序如下：

1. 优先使用插件配置中的 `provider`。
2. 若 `provider` 为空，则尝试使用 `astrbot_config` 对应配置里的默认对话 Provider。
3. 若仍无法确定，则回退到当前消息会话实际使用的 Provider。

## 使用方法

聊天中执行：

```text
/人格更新 <人格ID> <更新要求>
```

示例：

```text
/人格更新 伯特 保留当前设定，把语气改得更专业，补充一条出错时的友好提示
```

查看历史：

```text
/人格历史 伯特
```

预览某次历史并决定是否回滚：

```text
/人格回滚 伯特 1
/人格回滚 伯特 1 确认
```

历史记录会持久化保存到：

```text
data/plugin_data/astrbot_plugin_personal_selfupdate/persona_history.json
```

## 注意事项

- `begin_dialogs` 必须为偶数条，并按“用户 / 助手”交替排列。
- 模型会先调用 `get_persona_detail`，再调用 `update_persona_details`；建议使用函数调用稳定的模型。
- 如果只想改局部内容，请在指令里明确说明“保留其它字段不变”。
- 若指定的 Provider 或 AstrBot 配置不存在，插件会自动回退到当前会话 Provider。
- `/人格回滚` 默认先输出该历史的 `system_prompt` 与主要字段，只有追加 `确认` 才会真正执行回滚。
