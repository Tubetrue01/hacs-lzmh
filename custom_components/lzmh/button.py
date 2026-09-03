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
    """根据 API 返回的门禁动态创建 Button Entity。"""

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
    """动态门禁按钮。"""

    _attr_has_entity_name = True

    def __init__(
        self,
        controller: GateController,
        info: dict[str, Any],
    ) -> None:
        """初始化按钮。"""

        self._controller = controller
        self._info = info

        # --------------------------------------------------------------
        # Home Assistant Entity 基本信息
        # --------------------------------------------------------------

        self._attr_name = info["name"]

        self._attr_unique_id = (
            f"{DOMAIN}_{info['unique_id']}"
        )

        # --------------------------------------------------------------
        # 图标
        # --------------------------------------------------------------

        if info["action_type"] == "pwd":
            self._attr_icon = "mdi:key-text"
        else:
            self._attr_icon = "mdi:door-closed"

        # --------------------------------------------------------------
        # 动态状态
        #
        # 例如：
        #
        # 点击开门
        #     ↓
        # 正在操作
        #     ↓
        # 开门成功
        #     ↓
        # 点击开门
        # --------------------------------------------------------------

        self._dynamic_status = info[
            "default_text"
        ]

        self._reset_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Entity 属性
    # ------------------------------------------------------------------
    @property
    def extra_state_attributes(
        self,
    ) -> dict[str, Any]:
        """返回额外属性。"""

        return {
            "dynamic_status": self._dynamic_status,
            "gate_type": self._info["action_type"],
            "sort_order": (
                1
                if self._info["action_type"] == "pwd"
                else 0
            ),
        }
    # ------------------------------------------------------------------
    # 点击按钮
    # ------------------------------------------------------------------

    async def async_press(self) -> None:
        """处理按钮点击。"""

        # --------------------------------------------------------------
        # 防止连续点击导致多个请求同时执行。
        # --------------------------------------------------------------

        if self._dynamic_status == "正在操作...":
            _LOGGER.debug(
                "门禁正在操作中，忽略重复点击: %s",
                self._info["name"],
            )
            return

        # --------------------------------------------------------------
        # 显示 loading
        # --------------------------------------------------------------

        self._dynamic_status = "正在操作..."

        self.async_write_ha_state()

        try:
            # ----------------------------------------------------------
            # Controller 内部负责：
            #
            # token
            # openid
            # session
            # 登录
            # token 刷新
            # API 请求
            # API 重试
            #
            # 这里完全不需要知道这些细节。
            # ----------------------------------------------------------

            result = await self.hass.async_add_executor_job(
                self._controller.execute_door_action,
                self._info,
            )

            self._dynamic_status = result

        except Exception:
            _LOGGER.exception(
                "门禁操作异常: %s",
                self._info["name"],
            )

            self._dynamic_status = "网络异常"

        # --------------------------------------------------------------
        # 更新 HA 状态
        # --------------------------------------------------------------

        self.async_write_ha_state()

        # --------------------------------------------------------------
        # 取消之前的 reset task
        # --------------------------------------------------------------

        if self._reset_task is not None:
            self._reset_task.cancel()

        self._reset_task = self.hass.async_create_task(
            self._reset_status()
        )

    # ------------------------------------------------------------------
    # 恢复按钮文字
    # ------------------------------------------------------------------

    async def _reset_status(self) -> None:
        """延迟恢复按钮默认状态。"""

        delay = self._info.get(
            "delay",
            2,
        )

        try:
            await asyncio.sleep(delay)

        except asyncio.CancelledError:
            return

        self._dynamic_status = self._info[
            "default_text"
        ]

        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Entity 移除
    # ------------------------------------------------------------------

    async def async_will_remove_from_hass(
        self,
    ) -> None:
        """Entity 从 HA 移除时清理 task。"""

        if self._reset_task is not None:
            self._reset_task.cancel()
            self._reset_task = None

        await super().async_will_remove_from_hass()