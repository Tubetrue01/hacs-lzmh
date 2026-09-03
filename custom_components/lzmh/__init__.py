import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import GateController
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """初始化集成。"""

    phone = entry.data.get("phone")
    password = entry.data.get("password")

    if not phone or not password:
        _LOGGER.error("缺少手机号或密码配置")
        return False

    # 单用户模式：整个集成共用一个 session 文件。
    cache_file = hass.config.path(
        ".storage",
        f"{DOMAIN}_session.json",
    )

    controller = GateController(
        phone=phone,
        password=password,
        cache_file=cache_file,
    )

    try:
        doors = await hass.async_add_executor_job(
            controller.fetch_and_clean_doors
        )
    except Exception:
        _LOGGER.exception("初始化联���门户失败")
        return False

    if not doors:
        _LOGGER.warning("没有获取到任何门禁设备")

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "controller": controller,
        "doors": doors,
    }

    await hass.config_entries.async_forward_entry_setups(
        entry,
        ["button"],
    )

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """卸载集成。"""

    unload_ok = await hass.config_entries.async_unload_platforms(
        entry,
        ["button"],
    )

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok