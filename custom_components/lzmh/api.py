import functools
import hashlib
import logging
import time
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSession
from homeassistant.helpers.storage import Store

from .const import API_SUCCESS_CODE, DEV_NAME, OLD_BASE

_LOGGER = logging.getLogger(__name__)

USER_AGENT = "okhttp/3.12.0"

HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "User-Agent": USER_AGENT,
}

# 开门 API 使用的固定 secret
OPEN_DOOR_SECRET = "lzmh@openDoor#v2"


def auto_relogin(func):
    """自动登录与 Token 失效重试装饰器"""

    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        if not self.token or not self.openid:
            _LOGGER.info("内存缺少 Token，自动触发网络登录...")
            if not await self.async_force_refresh_token():
                return {}

        res_json = await func(self, *args, **kwargs)

        if not res_json or res_json.get("code") != API_SUCCESS_CODE:
            _LOGGER.warning("Token 可能失效或响应异常，尝试重新登录重试...")
            if await self.async_force_refresh_token():
                res_json = await func(self, *args, **kwargs)

        return res_json or {}

    return wrapper


class GateController:
    """联掌门户 API 控制器"""

    def __init__(
            self,
            phone: str,
            password: str,
            session: ClientSession,
            store: Store,
    ) -> None:
        self.phone = phone
        self.password = password

        self.store = store
        self.session = session

        self.token: str | None = None
        self.openid: str | None = None

    # -----------------
    # 基础工具
    # -----------------

    @staticmethod
    def _calc_md5(value: str) -> str:
        """计算 MD5"""
        return hashlib.md5(value.encode("utf-8")).hexdigest()

    async def _async_login(self) -> dict[str, Any]:
        """登录 API，获取原始响应数据"""
        login_path = "/api/v2/login/account"
        login_t = int(time.time())

        login_sign = self._calc_md5(f"{login_path}{login_t}")
        pwd_md5 = self._calc_md5(self.password)

        params = {
            "t": login_t,
            "sign": login_sign,
        }

        login_body = {
            "header": {
                "imei": "865123001234567",
                "andModel": "Redmi Note 12 Pro",
                "appVersion": "5.13.0",
                "appCode": 5130,
                "sdk": "13",
                "type": 1,
                "apkName": "com.fxicrazy.sjml",
                "netWorkType": "WIFI",
                "operator": "中国移动",
                "imsi": "460001234567890",
                "phoneMac": "",
            },
            "body": {
                "login_name": self.phone,
                "password": pwd_md5,
                "reg_id": "",
                "auth_code": "",
                "bind_info": "",
                "third_code": "",
            },
        }

        _LOGGER.info("正在登录联掌门户...")

        return await self._post(
            path=login_path,
            params=params,
            body=login_body,
        )

    async def async_force_refresh_token(self) -> bool:
        """强制重新登录并刷新/保存 Session（异步）"""
        try:
            res_json = await self._async_login()

            if res_json.get("code") != API_SUCCESS_CODE:
                _LOGGER.error(
                    "登录失败: code=%s, response=%s",
                    res_json.get("code"),
                    res_json,
                )
                return False

            value = res_json.get("value") or {}
            token = value.get("token")
            openid = value.get("openid")

            if not token or not openid:
                _LOGGER.error("登录成功但响应中缺少 token/openid: %s", res_json)
                return False

            self.token = token
            self.openid = openid

            await self.async_save_session()

            _LOGGER.info("联掌门户登录成功，Session 已持久化保存")
            return True

        except TimeoutError:
            _LOGGER.error("登录请求超时，请检查网络连接或服务器地址")
        except ClientError as err:
            _LOGGER.error("登录服务器连接失败: %s", err)
        except Exception as err:
            _LOGGER.exception("登录过程发生未预期异常: %s", err)

        return False

    # -----------------
    # Session
    # -----------------

    async def async_load_session(self) -> None:
        """加载保存的 token/openid (异步)"""
        try:
            data = await self.store.async_load()

            if not data:
                _LOGGER.debug("Session 文件不存在或为空")
                return

            token = data.get("token")
            openid = data.get("openid")

            if token and openid:
                self.token = token
                self.openid = openid
                _LOGGER.debug("已成功加载联掌门户 Session")
            else:
                _LOGGER.warning("Session 文件存在，但缺少 token/openid")

        except Exception as err:
            _LOGGER.warning("读取 Session 失败: %s", err)

    async def async_save_session(self) -> None:
        """保存 token/openid (异步)"""
        if not self.token or not self.openid:
            _LOGGER.warning("没有 token/openid，不保存 Session")
            return

        data = {
            "token": self.token,
            "openid": self.openid,
        }

        try:
            await self.store.async_save(data)
            _LOGGER.debug("Session 已保存")

        except Exception as err:
            _LOGGER.error("保存 Session 失败: %s", err)

    # -----------------
    # HTTP
    # -----------------

    async def _post(
            self,
            path: str,
            params: dict[str, Any] | None = None,
            body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """底层的异步 POST 请求封装"""
        url = f"{OLD_BASE}{DEV_NAME}{path}"

        try:
            async with self.session.post(
                    url,
                    params=params,
                    headers=HEADERS,
                    json=body,
                    timeout=15,
            ) as response:
                response.raise_for_status()
                data = await response.json()
                return data

        except ClientResponseError as err:
            _LOGGER.error("HTTP 错误 (%s): %s", err.status, err.message)
            raise
        except ClientError as err:
            _LOGGER.error("网络连接或请求失败: %s", err)
            raise
        except TimeoutError:
            _LOGGER.error("请求超时: %s", url)
            raise

    # -----------------
    # 小区列表 (底层返回 Raw Dict)
    # -----------------

    @auto_relogin
    async def async_get_my_community_list(self) -> dict[str, Any]:
        """获取我的小区及门禁列表"""
        path = "/api/v1/community/getMyCommunityList"
        timestamp = int(time.time())

        sign = self._calc_md5(f"{path}{timestamp}{self.token}")

        params = {
            "timestamp": timestamp,
            "openid": self.openid,
            "sign": sign,
        }

        body = {
            "header": {
                "imei": "865123001234567",
                "andModel": "Redmi Note 12 Pro",
                "appVersion": "5.13.0",
                "appCode": 5130,
                "sdk": "13",
                "type": 1,
                "apkName": "com.fxicrazy.sjml",
                "netWorkType": "WIFI",
                "operator": "中国移动",
                "imsi": "460001234567890",
                "phoneMac": "",
            },
            "body": {},
        }

        return await self._post(
            path=path,
            params=params,
            body=body,
        )

    # -----------------
    # 开门 (底层 Raw 请求 -> 业务层 bool 封装)
    # -----------------

    @auto_relogin
    async def _async_raw_open_door(
            self,
            door_sn: str,
            door_eqmodel: int = 6,
    ) -> dict[str, Any]:
        """开门底层裸接口（供装饰器捕获 Code 使用）"""
        if door_eqmodel == 8:
            path = "/api/v1/opendoor/openDoorControlByOrion"
        else:
            path = "/api/v1/opendoor/openDoorControlV2"

        timestamp = int(time.time())
        msg_id = str(timestamp)

        sign = self._calc_md5(
            f"{path}"
            f"{door_sn}"
            f"{msg_id}"
            f"{timestamp}"
            f"{self.token}"
            f"{OPEN_DOOR_SECRET}"
        )

        params = {
            "timestamp": timestamp,
            "openid": self.openid,
            "sign": sign,
        }

        body = {
            "header": {
                "imei": "865123001234567",
                "andModel": "Redmi Note 12 Pro",
                "appVersion": "5.13.0",
                "appCode": 5130,
                "sdk": "13",
                "type": 1,
                "apkName": "com.fxicrazy.sjml",
                "netWorkType": "WIFI",
                "operator": "中国移动",
                "imsi": "460001234567890",
                "phoneMac": "",
            },
            "body": {
                "ser_num": door_sn,
                "msg_id": msg_id,
            },
        }

        return await self._post(
            path=path,
            params=params,
            body=body,
        )

    async def async_open_door(
            self,
            door_sn: str,
            door_eqmodel: int = 6,
    ) -> bool:
        """执行开门操作（面向 Button 实体的上层接口）"""
        res_data = await self._async_raw_open_door(
            door_sn=door_sn, door_eqmodel=door_eqmodel
        )

        code = res_data.get("code")
        if code == API_SUCCESS_CODE:
            _LOGGER.info("设备 [%s] 开门成功", door_sn)
            return True

        _LOGGER.error("设备 [%s] 开门失败，响应信息: %s", door_sn, res_data)
        return False

    # -----------------
    # 临时密码 (底层 Raw 请求 -> 业务层 str 封装)
    # -----------------

    @auto_relogin
    async def _async_raw_get_open_door_pwd(
            self, door_bt_mac: str
    ) -> dict[str, Any]:
        """获取临时密码底层裸接口"""
        path = "/api/v1/opendoor/getOpenDoorPwd"
        timestamp = int(time.time())

        sign = self._calc_md5(f"{path}{timestamp}{self.token}")

        params = {
            "timestamp": timestamp,
            "openid": self.openid,
            "sign": sign,
        }

        body = {
            "header": {
                "imei": "865123001234567",
                "andModel": "Redmi Note 12 Pro",
                "appVersion": "5.13.0",
                "appCode": 5130,
                "sdk": "13",
                "type": 1,
                "apkName": "com.fxicrazy.sjml",
                "netWorkType": "WIFI",
                "operator": "中国移动",
                "imsi": "460001234567890",
                "phoneMac": "",
            },
            "body": {
                "bt_mac": door_bt_mac,
            },
        }

        return await self._post(
            path=path,
            params=params,
            body=body,
        )

    async def async_get_open_door_pwd(
            self,
            door_bt_mac: str,
    ) -> str | None:
        """获取门禁临时密码（面向 Button 实体的上层接口）"""
        res_data = await self._async_raw_get_open_door_pwd(
            door_bt_mac=door_bt_mac
        )

        if res_data.get("code") == API_SUCCESS_CODE:
            pwd = res_data.get("value")
            if pwd:
                _LOGGER.info("成功获取设备 [%s] 的开门密码: %s", door_bt_mac, pwd)
                return str(pwd)

        _LOGGER.error("获取设备 [%s] 开门密码失败: %s", door_bt_mac, res_data)
        return None

    # -----------------
    # 获取动态门禁
    # -----------------

    async def async_fetch_and_clean_doors(self) -> list[dict[str, Any]]:
        """获取并整理门禁设备（异步极简版）"""
        res_json = await self.async_get_my_community_list()

        if not res_json or res_json.get("code") != API_SUCCESS_CODE:
            _LOGGER.error("获取门禁列表失败或数据格式异常: %s", res_json)
            return []

        value = res_json.get("value") or {}
        com_list = value.get("comList") or []
        doors_pool: list[dict[str, Any]] = []

        for community in com_list:
            # 1. 普通门禁 (一键开门)
            for equ in community.get("equ_list") or []:
                ser_num = equ.get("ser_num", "")
                if not ser_num:
                    _LOGGER.warning("跳过普通门禁，缺少 ser_num: %s", equ)
                    continue

                try:
                    eq_model = int(equ.get("eq_model", 6))
                except (TypeError, ValueError):
                    eq_model = 6

                unique_id = equ.get("id") or ser_num

                doors_pool.append({
                    "name": equ.get("door_no") or equ.get("name") or "常规门禁",
                    "action_type": "open",
                    "target": ser_num,
                    "eq_model": eq_model,
                    "delay": 2,
                    "default_text": "点击开门",
                    "unique_id": f"gate_equ_{unique_id}",
                })

            # 2. 独立门禁 / 临时密码
            for alone in community.get("alone_list") or []:
                bt_mac = alone.get("bt_mac", "")
                if not bt_mac:
                    _LOGGER.warning("跳过独立门禁，缺少 bt_mac: %s", alone)
                    continue

                unique_id = alone.get("id") or bt_mac

                doors_pool.append({
                    "name": alone.get("pos_name") or "独立门禁",
                    "action_type": "pwd",
                    "target": bt_mac,
                    "delay": 5,
                    "default_text": "获取临时密码",
                    "unique_id": f"gate_alone_{unique_id}",
                })

        _LOGGER.info("成功解析到 %d 个门禁设备", len(doors_pool))
        return doors_pool