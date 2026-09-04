import asyncio
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import GateController
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
        hass: HomeAssistant,
        entry: ConfigEntry,
        async_add_entities: AddEntitiesCallback,
) -> None:
    """根据 API 返回的门禁动态创建 Button Entity"""

    data = hass.data[DOMAIN][entry.entry_id]

    controller: GateController = data["controller"]
    doors: list[dict[str, Any]] = data["doors"]

    entities = [
        HA_DynamicGateButton(
            controller=controller,
            info=door,
        )
        for door in doors
    ]

    async_add_entities(entities)


class HA_DynamicGateButton(ButtonEntity):
    """动态门禁按钮"""

    _attr_has_entity_name = True

    def __init__(
            self,
            controller: GateController,
            info: dict[str, Any],
    ) -> None:
        """初始化按钮"""

        self._controller = controller
        self._info = info

        # ------------------------------
        # Home Assistant Entity 基本信息
        # ------------------------------

        self._attr_name = info["name"]
        self._attr_unique_id = f"{DOMAIN}_{info['unique_id']}"

        # 图标
        if info["action_type"] == "pwd":
            self._attr_icon = "mdi:key-text"
        else:
            self._attr_icon = "mdi:door-closed"

        # 动态状态（初始值，对应 default_text）
        self._dynamic_status = info["default_text"]
        self._reset_task: asyncio.Task | None = None

    # ------------------------------
    # Entity 属性 (供前端卡片读取)
    # ------------------------------

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """返回额外属性"""

        return {
            "dynamic_status": self._dynamic_status,
            "gate_type": self._info["action_type"],
            "sort_order": 1 if self._info["action_type"] == "pwd" else 0,
        }

    # ------------------------------
    # 点击按钮
    # ------------------------------

    async def async_press(self) -> None:
        """处理按钮点击"""

        # 1. 防重复点击
        if self._dynamic_status == "正在操作...":
            _LOGGER.debug(
                "门禁正在操作中，忽略重复点击: %s",
                self._info["name"],
            )
            return

        # 2. 状态切换为“正在操作...”，驱动前端图标旋转
        self._dynamic_status = "正在操作..."
        self.async_write_ha_state()

        action_type = self._info.get("action_type")
        target = self._info.get("target", "")

        try:
            # 3.1 一键开门
            if action_type == "open":
                eq_model = self._info.get("eq_model", 6)
                success = await self._controller.async_open_door(
                    door_sn=target,
                    door_eqmodel=eq_model,
                )

                if success:
                    self._dynamic_status = "开门成功"
                else:
                    self._dynamic_status = "开门失败"

            # 3.2 获取临时密码（直接写入 dynamic_status，驱动前端卡片显示密码和绿底）
            elif action_type == "pwd":
                pwd = await self._controller.async_get_open_door_pwd(
                    door_bt_mac=target
                )

                if pwd:
                    self._dynamic_status = f"密码: {pwd}"
                else:
                    self._dynamic_status = "获取失败"

            else:
                self._dynamic_status = "未知类型"

        except Exception:
            _LOGGER.exception(
                "门禁操作发生异常: %s",
                self._info["name"],
            )
            self._dynamic_status = "网络异常"

        # 4. 更新状态，触发 button-card 重绘
        self.async_write_ha_state()

        # 5. 启动复位定时任务（例如 2s 或 5s 后恢复为初始文字）
        if self._reset_task is not None:
            self._reset_task.cancel()

        self._reset_task = self.hass.async_create_background_task(
            self._reset_status(),
            name=f"gate_reset_status_{self._info['unique_id']}",
        )

    # ------------------------------
    # 恢复按钮默认文字
    # ------------------------------

    async def _reset_status(self) -> None:
        """延迟恢复按钮默认状态"""

        delay = self._info.get("delay", 2)

        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        self._dynamic_status = self._info["default_text"]
        self.async_write_ha_state()

    # ------------------------------
    # Entity 移除
    # ------------------------------

    async def async_will_remove_from_hass(self) -> None:
        """Entity 从 HA 移除时清理 Task"""

        if self._reset_task is not None:
            self._reset_task.cancel()
            self._reset_task = None

        await super().async_will_remove_from_hass()