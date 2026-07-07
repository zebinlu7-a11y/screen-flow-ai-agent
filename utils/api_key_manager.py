# -*- coding: utf-8 -*-
"""
API Key 本地持久化管理。

配置文件存储在 exe 所在目录（或当前工作目录）下的 airag_config.json。
格式: {"api_key": "xxx", "model": "doubao-seed-2-0-lite-260428"}
"""
import json
import os
import sys


def get_config_dir() -> str:
    """获取配置文件所在目录（exe 目录或当前工作目录）。"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后的 exe 路径
        return os.path.dirname(sys.executable)
    else:
        # 开发环境中用项目根目录
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


CONFIG_DIR = get_config_dir()
CONFIG_FILE = os.path.join(CONFIG_DIR, "airag_config.json")

DEFAULT_MODEL = "doubao-seed-2-0-mini-260428"


def load_config() -> dict:
    """加载配置文件，返回 dict。文件不存在返回空 dict。"""
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, IOError):
        return {}


def save_config(data: dict) -> None:
    """保存配置到文件。"""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_api_key() -> str:
    """获取保存的 API Key，没有则返回空字符串。"""
    config = load_config()
    return config.get("api_key", "")


def set_api_key(key: str) -> None:
    """保存 API Key。"""
    config = load_config()
    config["api_key"] = key
    save_config(config)


def get_model() -> str:
    """获取保存的模型名，没有则返回默认。"""
    config = load_config()
    return config.get("model", DEFAULT_MODEL)


def set_model(model: str) -> None:
    """保存模型名。"""
    config = load_config()
    config["model"] = model
    save_config(config)


def get_proxy() -> str:
    """获取保存的代理地址，没有则返回空字符串。"""
    config = load_config()
    return config.get("proxy", "")


def set_proxy(proxy: str) -> None:
    """保存代理地址。"""
    config = load_config()
    config["proxy"] = proxy
    save_config(config)


def get_tencent_secret_id() -> str:
    """获取腾讯云 SecretId。"""
    return load_config().get("tencent_secret_id", "")


def set_tencent_secret_id(sid: str) -> None:
    """保存腾讯云 SecretId。"""
    config = load_config()
    config["tencent_secret_id"] = sid
    save_config(config)


def get_tencent_secret_key() -> str:
    """获取腾讯云 SecretKey。"""
    return load_config().get("tencent_secret_key", "")


def set_tencent_secret_key(skey: str) -> None:
    """保存腾讯云 SecretKey。"""
    config = load_config()
    config["tencent_secret_key"] = skey
    save_config(config)
