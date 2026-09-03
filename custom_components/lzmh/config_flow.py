import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .api import GateController
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class GateConfigFlow(
    config_entries.ConfigFlow,
    domain=DOMAIN,
):
    """联掌门户配置流程。"""

    VERSION = 1

    async def async_step_user(
        self,
        user_input=None,
    ):
        """处理用户配置"""

        errors = {}

        # ------------------------------
        # 单用户模式：
        # 已经配置过就不允许再次添加。
        # ------------------------------

        if self._async_current_entries():
            return self.async_abort(
                reason="already_configured"
            )

        # ------------------------------
        # 用户提交账号密码
        # ------------------------------

        if user_input is not None:
            phone = user_input["phone"].strip()
            password = user_input["password"]

            # ------------------------------
            # 基础校验
            # ------------------------------

            if not phone:
                errors["phone"] = "invalid_phone"

            elif not password:
                errors["password"] = "invalid_password"

            else:
                # ------------------------------
                # 使用固定 session 文件
                # ------------------------------

                cache_file = self.hass.config.path(
                    ".storage",
                    f"{DOMAIN}_session.json",
                )

                controller = GateController(
                    phone=phone,
                    password=password,
                    cache_file=cache_file,
                )

                # ------------------------------
                # 登录验证
                #
                # force_refresh_token() 内部会：
                #
                # login()
                #   ↓
                # 获取 token/openid
                #   ↓
                # save_session()
                #
                # requests 是同步的，所以放 executor。
                # ------------------------------

                success = await self.hass.async_add_executor_job(
                    controller.force_refresh_token
                )

                if success:
                    _LOGGER.info(
                        "联掌门户账号验证成功"
                    )

                    return self.async_create_entry(
                        title=f"联掌门户 ({phone})",
                        data={
                            "phone": phone,
                            "password": password,
                        },
                    )

                _LOGGER.warning(
                    "联掌门户账号验证失败"
                )

                errors["base"] = "invalid_auth"

        # ------------------------------
        # 显示配置页面
        # ------------------------------

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "phone"
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type="tel",
                        )
                    ),
                    vol.Required(
                        "password"
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type="password",
                        )
                    ),
                }
            ),
            errors=errors,
        )