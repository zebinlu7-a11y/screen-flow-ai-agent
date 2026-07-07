# -*- coding: utf-8 -*-
"""
腾讯云 OCR 文字识别工具 — 免费额度 1000 次/月。

使用前需配置 SecretId 和 SecretKey（设置界面或 airag_config.json）。
"""
import base64
import io
from typing import List, Optional
from PIL import Image


def _get_credentials():
    """从配置中读取腾讯云凭证。"""
    from utils.api_key_manager import get_tencent_secret_id, get_tencent_secret_key
    sid = get_tencent_secret_id()
    skey = get_tencent_secret_key()
    return sid, skey


def ocr_recognize(pil_image: Image.Image) -> str:
    """识别单张 PIL 图片，返回文本。"""
    sid, skey = _get_credentials()
    if not sid or not skey:
        return "⚠️ 请先设置腾讯云 OCR 凭证（SecretId / SecretKey）\n右键托盘 → 设置"

    try:
        from tencentcloud.common import credential
        from tencentcloud.ocr.v20181119 import ocr_client, models

        # 图片转 Base64
        buf = io.BytesIO()
        pil_image.convert("RGB").save(buf, format="JPEG", quality=85)
        img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        cred = credential.Credential(sid, skey)
        client = ocr_client.OcrClient(cred, "ap-guangzhou")

        req = models.GeneralBasicOCRRequest()
        req.ImageBase64 = img_b64

        resp = client.GeneralBasicOCR(req)

        lines = []
        for item in resp.TextDetections:
            lines.append(item.DetectedText)

        return "\n".join(lines) if lines else "[OCR] 未识别到文字"

    except Exception as e:
        err = str(e)
        if "AuthFailure" in err or "InvalidParameterValue" in err:
            return f"❌ 腾讯云凭证无效: {err}\n请检查 SecretId/SecretKey 是否正确"
        if "RequestLimitExceeded" in err:
            return "❌ 请求超限，免费额度 1000 次/月已用完"
        return f"❌ OCR 请求失败: {err}"


def ocr_recognize_batch(images: List[Image.Image]) -> str:
    """批量识别多张图片，返回汇总文本。"""
    if not images:
        return ""

    results = []
    for i, img in enumerate(images, 1):
        text = ocr_recognize(img)
        if len(images) > 1:
            results.append(f"## 截图 {i}\n{text}")
        else:
            results.append(text)

    return "\n\n".join(results)
