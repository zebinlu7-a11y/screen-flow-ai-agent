# -*- coding: utf-8 -*-
"""
图片处理工具：QImage ↔ PIL 互转、压缩、Base64 编码
"""
import io
import base64
from PIL import Image
from PyQt6.QtGui import QImage

from config import MAX_IMAGE_WIDTH, MAX_IMAGE_HEIGHT, JPEG_QUALITY


def qimage_to_pil(qimage: QImage) -> Image.Image:
    """
    将 PyQt6 的 QImage 转换为 PIL Image。
    使用 QImage 的原始像素数据避免颜色通道问题。
    """
    # 确保是 32-bit ARGB 格式
    qimage = qimage.convertToFormat(QImage.Format.Format_ARGB32)
    width = qimage.width()
    height = qimage.height()

    # 获取原始像素字节
    ptr = qimage.bits()
    ptr.setsize(height * width * 4)  # 4 bytes per pixel (ARGB)
    arr = bytes(ptr)

    # 从 ARGB 字节数组创建 PIL Image
    pil_image = Image.frombytes("RGBA", (width, height), arr)

    return pil_image


def pil_to_base64(image: Image.Image, quality: int = JPEG_QUALITY) -> str:
    """
    PIL Image → JPEG 压缩 → Base64 编码字符串（不含 data URI 前缀）。
    """
    # 如果是 RGBA 模式，转为 RGB 再压缩
    if image.mode == "RGBA":
        # 创建白色背景
        bg = Image.new("RGB", image.size, (255, 255, 255))
        bg.paste(image, mask=image.split()[3])  # alpha channel as mask
        image = bg
    elif image.mode != "RGB":
        image = image.convert("RGB")

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    b64_str = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return b64_str


def compress_image(image: Image.Image, max_width: int = MAX_IMAGE_WIDTH,
                   max_height: int = MAX_IMAGE_HEIGHT) -> Image.Image:
    """
    等比缩放图像，确保不超过最大宽高限制。
    """
    width, height = image.size
    if width <= max_width and height <= max_height:
        return image.copy()

    ratio = min(max_width / width, max_height / height)
    new_width = int(width * ratio)
    new_height = int(height * ratio)
    return image.resize((new_width, new_height), Image.Resampling.LANCZOS)


def pil_to_base64_data_uri(image: Image.Image, quality: int = JPEG_QUALITY) -> str:
    """
    返回完整的 data URI 格式：data:image/jpeg;base64,...
    """
    b64 = pil_to_base64(image, quality)
    return f"data:image/jpeg;base64,{b64}"


def save_screenshot(image: Image.Image, filepath: str) -> str:
    """
    保存截图到文件（用于调试）。
    """
    image.save(filepath, format="JPEG", quality=JPEG_QUALITY)
    return filepath
