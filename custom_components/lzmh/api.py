import hashlib
import json
import logging
import os
import time
from typing import Any

import requests

from .const import API_SUCCESS_CODE, DEV_NAME, OLD_BASE

_LOGGER = logging.getLogger(__name__)

USER_AGENT = "okhttp/3.12.0"

HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "User-Agent": USER_AGENT,
}

# 开门 API 使用的固定 secret
OPEN_DOOR_SECRET = "lzmh@openDoor#v2"


class GateController:
    """联掌门户 API 控制器"""

    def __init__(
            self,
            phone: str,
            password: str,
            cache_file: str,
    ) -> None:
        self.phone = phone
        self.password = password

        self.token: str | None = None
        self.openid: str | None = None

        self.cache_file = cache_file

    # -----------------
    # 基础工具
    # -----------------

    @staticmethod
    def calc_md5(value: str) -> str:
        """计算 MD5"""

        return hashlib.md5(
            value.encode("utf-8")
        ).hexdigest()

    # -----------------
    # 登录
    # -----------------

    def login(self) -> requests.Response:
        """登录并获取 token/openid"""

        login_path = "/api/v2/login/account"

        login_t = int(time.time())

        login_sign = self.calc_md5(
            f"{login_path}{login_t}"
        )

        pwd_md5 = self.calc_md5(
            self.password
        )

        login_url = (
            f"{OLD_BASE}{DEV_NAME}{login_path}"
        )

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

        _LOGGER.info(
            "正在登录联掌门户: %s",
            login_url,
        )

        response = requests.post(
            login_url,
            params=params,
            headers=HEADERS,
            json=login_body,
            timeout=(10, 15),
        )

        _LOGGER.debug(
            "登录 HTTP %s: %s",
            response.status_code,
            response.text[:1000],
        )

        response.raise_for_status()

        return response

    def force_refresh_token(self) -> bool:
        """重新登录并刷新 token"""

        try:
            response = self.login()

            res_json = response.json()

            if res_json.get("code") != API_SUCCESS_CODE:
                _LOGGER.error(
                    "登录失败: code=%s, response=%s",
                    res_json.get("code"),
                    response.text[:1000],
                )
                return False

            value = res_json.get("value") or {}

            token = value.get("token")
            openid = value.get("openid")

            if not token or not openid:
                _LOGGER.error(
                    "登录成功但没有获取到 token/openid: %s",
                    response.text[:1000],
                )
                return False

            self.token = token
            self.openid = openid

            self.save_session()

            _LOGGER.info(
                "联掌门户登录成功，session 已保存"
            )

            return True

        except requests.exceptions.Timeout:
            _LOGGER.error(
                "登录请求超时，请检查服务器地址和网络连接"
            )

        except requests.exceptions.ConnectionError as err:
            _LOGGER.error(
                "登录服务器无法连接: %s",
                err,
            )

        except (
                requests.exceptions.RequestException,
                ValueError,
                OSError,
        ) as err:
            _LOGGER.error(
                "登录失败: %s",
                err,
            )

        return False

    # -----------------
    # Session
    # -----------------

    def load_session(self) -> None:
        """加载保存的 token/openid"""

        if not self.cache_file:
            return

        if not os.path.exists(self.cache_file):
            _LOGGER.debug(
                "session 文件不存在: %s",
                self.cache_file,
            )
            return

        try:
            with open(
                    self.cache_file,
                    "r",
                    encoding="utf-8",
            ) as file:
                data = json.load(file)

            token = data.get("token")
            openid = data.get("openid")

            if token and openid:
                self.token = token
                self.openid = openid

                _LOGGER.debug(
                    "已加载联掌门户 session"
                )
            else:
                _LOGGER.warning(
                    "session 文件存在，但缺少 token/openid"
                )

        except (
                OSError,
                ValueError,
                TypeError,
        ) as err:
            _LOGGER.warning(
                "读取 session 失败: %s",
                err,
            )

    def save_session(self) -> None:
        """保存 token/openid"""

        if not self.cache_file:
            return

        if not self.token or not self.openid:
            _LOGGER.warning(
                "没有 token/openid，不保存 session"
            )
            return

        directory = os.path.dirname(
            self.cache_file
        )

        if directory:
            try:
                os.makedirs(
                    directory,
                    exist_ok=True,
                )
            except OSError as err:
                _LOGGER.error(
                    "创建 session 目录失败: %s",
                    err,
                )
                return

        data = {
            "token": self.token,
            "openid": self.openid,
        }

        temp_file = f"{self.cache_file}.tmp"

        try:
            with open(
                    temp_file,
                    "w",
                    encoding="utf-8",
            ) as file:
                json.dump(
                    data,
                    file,
                    ensure_ascii=False,
                )

            # 原子替换。
            os.replace(
                temp_file,
                self.cache_file,
            )

            _LOGGER.debug(
                "session 已保存"
            )

        except OSError as err:
            _LOGGER.error(
                "保存 session 失败: %s",
                err,
            )

            # 如果临时文件已经创建但 replace 失败，
            # 尝试清理临时文件。
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except OSError:
                pass

    # -----------------
    # HTTP
    # -----------------

    def _post(
            self,
            path: str,
            params: dict[str, Any],
            body: dict[str, Any],
    ) -> requests.Response:

        url = f"{OLD_BASE}{DEV_NAME}{path}"

        response = requests.post(
            url,
            params=params,
            headers=HEADERS,
            json=body,
            timeout=(10, 15),
        )

        response.raise_for_status()

        return response

    # -----------------
    # 小区列表
    # -----------------

    def get_my_community_list(
            self,
    ) -> requests.Response:
        """获取我的小区及门禁列表。"""

        if not self.token or not self.openid:
            raise RuntimeError(
                "缺少 token/openid"
            )

        path = (
            "/api/v1/community/"
            "getMyCommunityList"
        )

        timestamp = int(time.time())

        sign = self.calc_md5(
            f"{path}{timestamp}{self.token}"
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
            "body": {},
        }

        return self._post(
            path=path,
            params=params,
            body=body,
        )

    # -----------------
    # 开门
    # -----------------

    def open_door(
            self,
            door_sn: str,
            door_eqmodel: int = 6,
    ) -> requests.Response:
        """执行开门操作。"""

        if not self.token or not self.openid:
            raise RuntimeError(
                "缺少 token/openid"
            )

        if door_eqmodel == 8:
            path = (
                "/api/v1/opendoor/"
                "openDoorControlByOrion"
            )
        else:
            path = (
                "/api/v1/opendoor/"
                "openDoorControlV2"
            )

        timestamp = int(time.time())

        msg_id = str(timestamp)

        sign = self.calc_md5(
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

        return self._post(
            path=path,
            params=params,
            body=body,
        )

    # -----------------
    # 临时密码
    # -----------------

    def open_door_pwd(
            self,
            door_bt_mac: str,
    ) -> requests.Response:
        """获取门禁临时密码。"""

        if not self.token or not self.openid:
            raise RuntimeError(
                "缺少 token/openid"
            )

        path = (
            "/api/v1/opendoor/"
            "getOpenDoorPwd"
        )

        timestamp = int(time.time())

        sign = self.calc_md5(
            f"{path}{timestamp}{self.token}"
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
                "bt_mac": door_bt_mac,
            },
        }

        return self._post(
            path=path,
            params=params,
            body=body,
        )

    # -----------------
    # 统一门禁操作
    # -----------------

    def execute_door_action(
            self,
            door_info: dict[str, Any],
    ) -> str:
        """执行门禁操作"""

        action_type = door_info.get(
            "action_type"
        )

        # -----------------
        # 第一次请求
        # -----------------

        try:
            response = self._execute_door_action_once(
                door_info
            )

            res_json = response.json()

        except (
                requests.exceptions.RequestException,
        ) as err:
            _LOGGER.error(
                "门禁 API 请求失败: %s",
                err,
            )
            return "网络异常"

        except ValueError:
            _LOGGER.error(
                "门禁 API 返回非 JSON: %s",
                response.text[:1000],
            )
            return "操作失败"

        if res_json.get("code") == API_SUCCESS_CODE:
            return self._parse_action_result(
                action_type,
                res_json,
            )

        # -----------------
        # 第一次失败：
        # 很可能 token 失效，重新登录一次
        # -----------------

        _LOGGER.warning(
            "门禁 API 返回失败 code=%s，"
            "尝试重新登录后重试",
            res_json.get("code"),
        )

        if not self.force_refresh_token():
            return "登录失效"

        # -----------------
        # 第二次请求
        # -----------------

        try:
            response = self._execute_door_action_once(
                door_info
            )

            res_json = response.json()

        except requests.exceptions.RequestException as err:
            _LOGGER.error(
                "刷新 session 后再次请求失败: %s",
                err,
            )
            return "网络异常"

        except ValueError:
            _LOGGER.error(
                "刷新 session 后 API 返回非 JSON: %s",
                response.text[:1000],
            )
            return "操作失败"

        if res_json.get("code") != API_SUCCESS_CODE:
            _LOGGER.error(
                "门禁操作失败: code=%s, message=%s",
                res_json.get("code"),
                res_json.get("message"),
            )
            return "操作失败"

        return self._parse_action_result(
            action_type,
            res_json,
        )

    def _execute_door_action_once(
            self,
            door_info: dict[str, Any],
    ) -> requests.Response:
        """执行一次门禁操作，不处理 token 刷新"""

        action_type = door_info.get(
            "action_type"
        )

        target = door_info.get(
            "target"
        )

        if action_type == "open":
            eq_model = door_info.get(
                "eq_model",
                6,
            )

            try:
                eq_model = int(eq_model)
            except (
                    TypeError,
                    ValueError,
            ):
                eq_model = 6

            return self.open_door(
                door_sn=target,
                door_eqmodel=eq_model,
            )

        if action_type == "pwd":
            return self.open_door_pwd(
                door_bt_mac=target,
            )

        raise ValueError(
            f"未知门禁 action_type: {action_type}"
        )

    @staticmethod
    def _parse_action_result(
            action_type: str | None,
            res_json: dict[str, Any],
    ) -> str:
        """解析门禁 API 成功结果。"""

        if action_type == "pwd":
            value = res_json.get("value")

            if value is None:
                return "获取成功"

            return str(value)

        return "开门成功"

    # -----------------
    # 获取动态门禁
    # -----------------

    def fetch_and_clean_doors(
            self,
    ) -> list[dict[str, Any]]:
        """获取并整理门禁设备"""

        # -----------------
        # 没有 session，先登录
        # -----------------

        if not self.token or not self.openid:
            _LOGGER.info(
                "没有有效 session，开始登录"
            )

            if not self.force_refresh_token():
                _LOGGER.error(
                    "自动登录失败"
                )
                return []

        # -----------------
        # 第一次获取门禁列表
        # -----------------

        try:
            response = self.get_my_community_list()

            res_json = response.json()

        except requests.exceptions.RequestException as err:
            _LOGGER.error(
                "获取门禁列表失败: %s",
                err,
            )
            return []

        except ValueError:
            _LOGGER.error(
                "获取门禁列表返回非 JSON: %s",
                response.text[:1000],
            )
            return []

        # -----------------
        # token 可能已经失效
        # -----------------

        if res_json.get("code") != API_SUCCESS_CODE:
            _LOGGER.warning(
                "获取门禁列表失败: code=%s，"
                "尝试重新登录",
                res_json.get("code"),
            )

            if not self.force_refresh_token():
                return []

            try:
                response = self.get_my_community_list()

                res_json = response.json()

            except requests.exceptions.RequestException as err:
                _LOGGER.error(
                    "重新登录后获取门禁列表失败: %s",
                    err,
                )
                return []

            except ValueError:
                _LOGGER.error(
                    "重新登录后门禁列表返回非 JSON: %s",
                    response.text[:1000],
                )
                return []

            if res_json.get("code") != API_SUCCESS_CODE:
                _LOGGER.error(
                    "重新登录后获取门禁列表仍失败: "
                    "code=%s, message=%s",
                    res_json.get("code"),
                    res_json.get("message"),
                )
                return []

        # -----------------
        # 解析门禁
        # -----------------

        value = res_json.get("value") or {}

        com_list = value.get(
            "comList"
        ) or []

        doors_pool: list[
            dict[str, Any]
        ] = []

        for community in com_list:
            # ==================
            # 普通门禁
            # ==================

            equ_list = community.get(
                "equ_list"
            ) or []

            for equ in equ_list:
                eq_model = equ.get(
                    "eq_model",
                    6,
                )

                try:
                    eq_model = int(eq_model)
                except (
                        TypeError,
                        ValueError,
                ):
                    eq_model = 6

                ser_num = equ.get(
                    "ser_num",
                    "",
                )

                unique_id = (
                        equ.get("id")
                        or ser_num
                )

                if not ser_num:
                    _LOGGER.warning(
                        "跳过普通门禁，"
                        "缺少 ser_num: %s",
                        equ,
                    )
                    continue

                doors_pool.append(
                    {
                        "name": (
                                equ.get("door_no")
                                or equ.get("name")
                                or "常规门禁"
                        ),
                        "action_type": "open",
                        "target": ser_num,
                        "eq_model": eq_model,
                        "delay": 2,
                        "default_text": "点击开门",
                        "unique_id": (
                            f"gate_equ_{unique_id}"
                        ),
                    }
                )

            # ==================
            # 独立门禁 / 临时密码
            # ==================

            alone_list = community.get(
                "alone_list"
            ) or []

            for alone in alone_list:
                bt_mac = alone.get(
                    "bt_mac",
                    "",
                )

                unique_id = (
                        alone.get("id")
                        or bt_mac
                )

                if not bt_mac:
                    _LOGGER.warning(
                        "跳过独立门禁，"
                        "缺少 bt_mac: %s",
                        alone,
                    )
                    continue

                doors_pool.append(
                    {
                        "name": (
                                alone.get(
                                    "pos_name"
                                )
                                or "独立门禁"
                        ),
                        "action_type": "pwd",
                        "target": bt_mac,
                        "delay": 5,
                        "default_text": (
                            "获取临时密码"
                        ),
                        "unique_id": (
                            f"gate_alone_{unique_id}"
                        ),
                    }
                )

        _LOGGER.info(
            "获取到 %d 个门禁设备",
            len(doors_pool),
        )

        return doors_pool