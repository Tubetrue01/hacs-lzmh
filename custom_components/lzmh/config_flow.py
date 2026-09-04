import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import GateController
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = f"{DOMAIN}_session"
STORAGE_VERSION = 1


class GateConfigFlow(
    config_entries.ConfigFlow,
    domain=DOMAIN,
):
    """联掌门户配置流程。"""

    VERSION = 1

    async def async_step_user(
            self,
            user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """处理用户配置"""

        errors: dict[str, str] = {}

        # ------------------------------
        # 单用户模式：已经配置过就不允许再次添加
        # ------------------------------
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        # ------------------------------
        # 用户提交账号密码
        # ------------------------------
        if user_input is not None:
            phone = user_input["phone"].strip()
            password = user_input["password"]

            if not phone:
                errors["phone"] = "invalid_phone"
            elif not password:
                errors["password"] = "invalid_password"
            else:
                session = async_get_clientsession(self.hass)

                store = Store(self.hass, STORAGE_VERSION, STORAGE_KEY)

                controller = GateController(
                    phone=phone,
                    password=password,
                    session=session,
                    store=store,
                )

                success = await controller.async_force_refresh_token()

                if success:
                    _LOGGER.info("联掌门户账号验证成功")

                    # 设置唯一 ID，防止重复配置同一个手机号
                    await self.async_set_unique_id(f"gate_{phone}")
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title=f"联掌门户 ({phone})",
                        data={
                            "phone": phone,
                            "password": password,
                        },
                    )

                _LOGGER.warning("联掌门户账号验证失败")
                errors["base"] = "invalid_auth"

        # ------------------------------
        # 显示配置页面
        # ------------------------------
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("phone"): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.TEL,
                        )
                    ),
                    vol.Required("password"): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD,
                        )
                    ),
                }
            ),
            errors=errors,
        )