import os
import sys
import time
import json
import re
import keyboard
import pyautogui
import pyperclip 
import base64
from playwright.sync_api import sync_playwright
from volcenginesdkarkruntime import Ark
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                           QHBoxLayout, QTableWidget, QTableWidgetItem, 
                           QPushButton, QHeaderView, QMessageBox, QTextEdit, QComboBox)
from PyQt5.QtCore import Qt, QThread, pyqtSignal

# ================= 配置区 =================
ARK_API_KEY = "ba9478f8-c76c-4942-b2a8-562d90df9a79" 
MODEL_NAME = "deepseek-v3-2-251201" 

class UniversalAIAtuomation:
    def __init__(self):
        # 初始化火山引擎客户端
        self.client = Ark(base_url='https://ark.cn-beijing.volces.com/api/v3', api_key=ARK_API_KEY)
        
        self.playwright = sync_playwright().start()
        try:
            # 连接到 9222 端口的 Edge
            self.browser = self.playwright.chromium.connect_over_cdp("http://127.0.0.1:9222")
            self.context = self.browser.contexts[0]
            self.page = self.context.pages[0]
            self.last_url = ""  # 跟踪上一次扫描的页面URL
            self.last_page_count = 0  # 跟踪上一次扫描时的页面数量
            print(f"✅ DeepSeek 眼睛已睁开，正在注视: {self.page.title()}")
        except Exception as e:
            print("❌ 无法连接到浏览器，请确保 Edge 启动参数包含 --remote-debugging-port=9222")
            os._exit(1)
    
    def get_active_page(self):
        """获取最后打开的页面，确保解析最新的页面"""
        try:
            # 获取所有页面
            pages = self.context.pages
            current_page_count = len(pages)
            
            # 更新页面数量
            if current_page_count != self.last_page_count:
                self.last_page_count = current_page_count
                print(f"🔔 页面数量变化: {self.last_page_count} 个页面")
            
            # 打印所有页面信息，方便调试
            print("📋 所有页面信息:")
            valid_pages = []
            for i, page in enumerate(pages):
                try:
                    title = page.title()
                    url = page.url
                    print(f"  {i+1}. {title} ({url})")
                    valid_pages.append(page)
                except Exception as page_error:
                    print(f"  {i+1}. 页面信息获取失败: {page_error}")
            
            # 优先选择最后打开的有效页面（通常是最新的）
            if valid_pages:
                last_page = valid_pages[-1]
                try:
                    title = last_page.title()
                    url = last_page.url
                    # 检查URL是否变化
                    if url != self.last_url:
                        print(f"🔔 检测到URL变化！从 {self.last_url} 变为 {url}")
                        self.last_url = url
                    print(f"🎯 选择最后打开的页面: {title} ({url})")
                    self.page = last_page
                    print(f"🔄 切换到最后打开的页面: {title} ({url})")
                    return last_page
                except Exception as e:
                    print(f"⚠️  选择最后打开的页面失败: {e}")
            
            # 如果没有有效页面，返回当前页面
            return self.page
        except Exception as e:
            print(f"⚠️  获取活动页面失败: {e}")
            return self.page

    def get_raw_view(self):
        """深度扫描页面：穿透 Shadow DOM 和 iframe，获取完整的页面信息"""
        # 先获取最后打开的页面，确保解析最新的页面
        self.get_active_page()
        script = """
        () => {
            window._ai_elements = []; 
            const snippets = [];
            let i = 0;
            
            // 获取页面基本信息
            const pageInfo = {
                title: document.title,
                url: window.location.href,
                viewport: {
                    width: window.innerWidth,
                    height: window.innerHeight
                }
            };
            
            function collect(root, depth = 0, parentInfo = null) {
                // 扩大选择器范围，包括更多元素类型
                const selectors = 'input, button, textarea, a, [role="button"], .el-button, .el-input__inner, span, div, li, h1, h2, h3, h4, h5, h6, p, label, form, select, option, img, iframe, section, article, nav, header, footer';
                const nodes = root.querySelectorAll(selectors);
                
                nodes.forEach(el => {
                    const rect = el.getBoundingClientRect();
                    // 过滤掉不可见或过小的干扰元素
                    if (rect.width > 1 && rect.height > 1 && rect.top < window.innerHeight + 1000) {
                        const text = (el.innerText || el.value || el.placeholder || el.title || "").trim().substring(0, 100);
                        const value = el.value || "";
                        const placeholder = el.placeholder || "";
                        const title = el.title || "";
                        const className = el.className || "";
                        const id = el.id || "";
                        const name = el.name || "";
                        const href = el.href || "";
                        const src = el.src || "";
                        const type = el.type || "";
                        const role = el.getAttribute('role') || "";
                        
                        // 获取父元素信息
                        let parentText = "";
                        if (el.parentElement) {
                            parentText = (el.parentElement.innerText || "").trim().substring(0, 50);
                        }
                        
                        // 构建元素信息
                        const elementInfo = {
                            tag: el.tagName,
                            text: text,
                            value: value,
                            placeholder: placeholder,
                            title: title,
                            className: className,
                            id: id,
                            name: name,
                            href: href,
                            src: src,
                            type: type,
                            role: role,
                            position: {
                                x: rect.left,
                                y: rect.top,
                                width: rect.width,
                                height: rect.height
                            },
                            depth: depth,
                            parentText: parentText,
                            ai_id: i
                        };
                        
                        // 记录所有元素，不仅仅是有文本的元素
                        window._ai_elements.push(el);
                        snippets.push(elementInfo);
                        i++;
                    }
                });
                
                // 递归穿透 Shadow DOM (兼容现代前端框架)
                root.querySelectorAll('*').forEach(node => {
                    if (node.shadowRoot) {
                        collect(node.shadowRoot, depth + 1, {
                            tag: node.tagName,
                            text: (node.innerText || "").trim().substring(0, 50)
                        });
                    }
                });
                
                // 处理 iframe
                root.querySelectorAll('iframe').forEach(iframe => {
                    try {
                        const iframeDocument = iframe.contentDocument || iframe.contentWindow.document;
                        if (iframeDocument) {
                            // 记录 iframe 本身
                            const rect = iframe.getBoundingClientRect();
                            const iframeInfo = {
                                tag: 'IFRAME',
                                text: 'IFRAME: ' + (iframe.src || 'unknown'),
                                src: iframe.src,
                                position: {
                                    x: rect.left,
                                    y: rect.top,
                                    width: rect.width,
                                    height: rect.height
                                },
                                depth: depth,
                                parentText: parentInfo ? parentInfo.text : '',
                                ai_id: i
                            };
                            window._ai_elements.push(iframe);
                            snippets.push(iframeInfo);
                            i++;
                            
                            // 递归收集 iframe 内部的元素
                            collect(iframeDocument, depth + 1, {
                                tag: 'IFRAME',
                                text: 'IFRAME: ' + (iframe.src || 'unknown')
                            });
                        }
                    } catch (e) {
                        // 跨域 iframe 无法访问，记录错误信息
                        const rect = iframe.getBoundingClientRect();
                        const iframeInfo = {
                            tag: 'IFRAME',
                            text: 'IFRAME (cross-origin): ' + (iframe.src || 'unknown'),
                            src: iframe.src,
                            position: {
                                x: rect.left,
                                y: rect.top,
                                width: rect.width,
                                height: rect.height
                            },
                            depth: depth,
                            parentText: parentInfo ? parentInfo.text : '',
                            ai_id: i,
                            error: 'Cross-origin iframe'
                        };
                        window._ai_elements.push(iframe);
                        snippets.push(iframeInfo);
                        i++;
                    }
                });
            }
            
            collect(document);
            
            // 返回页面信息和元素信息
            return JSON.stringify({
                pageInfo: pageInfo,
                elements: snippets
            }); 
        }
        """
        return self.page.evaluate(script)

    def think_and_decide(self, user_command):
        """使用 DeepSeek-V3 决策：解析 API 返回的内容节点"""
        page_data = self.get_raw_view()
        
        prompt = f"""你是一个网页自动化专家。
【页面数据】: {page_data}
【用户指令】: "{user_command}"

请分析页面数据，找到最匹配用户指令的元素。

分析步骤：
1. 首先查看页面基本信息（title, url）了解当前页面
2. 然后分析所有元素，根据元素的文本、属性、位置等信息
3. 考虑元素的类型、可见性和交互性
4. 选择最符合用户指令的元素

返回严格的 JSON:
{{
  "action": "click" | "fill" | "move" | "none",
  "ai_id": 数字,
  "reason": "详细说明为什么选择这个元素"
}}

注意：
- 当用户指令要求移动鼠标到某个元素时，返回 "action": "move"
- 当用户指令要求点击某个元素时，返回 "action": "click"
- 当用户指令要求填写某个元素时，返回 "action": "fill"
- 当没有找到匹配的元素时，返回 "action": "none"
"""

        try:
            response = self.client.responses.create(
                model=MODEL_NAME,
                input=[{"role": "user", "content": prompt}]
            )
            
            # 解析 DeepSeek 返回的内容，过滤掉推理节点
            res_text = ""
            for item in response.output:
                if hasattr(item, 'content'):
                    for part in item.content:
                        if hasattr(part, 'text'):
                            res_text += part.text
            
            print(f"🤖 DeepSeek 决策: {res_text}")
            
            # 正则提取 JSON
            match = re.search(r'\{.*\}', res_text, re.DOTALL)
            if match:
                # 容错处理：将单引号替换为双引号，并尝试解析
                clean_json = match.group().replace("'", '"')
                try:
                    return json.loads(clean_json)
                except:
                    import ast
                    return ast.literal_eval(match.group())
            return None
        except Exception as e:
            print(f"❌ 决策失败: {e}")
            return None

    def identify_element_position(self, user_command, log_signal=None):
        """使用豆包大模型识别元素位置"""
        try:
            # 截图当前页面
            screenshot_path = "temp_screenshot.png"
            self.page.screenshot(path=screenshot_path)
            
            # 调用豆包大模型进行视觉识别
            prompt = f"""请分析以下截图，找到与用户指令相关的元素位置：

用户指令：{user_command}

请返回该元素在屏幕上的坐标位置（x, y），格式为JSON：
{{
  "x": 数字,
  "y": 数字,
  "reason": "简短原因"
}}"""
            
            # 使用豆包模型
            response = self.client.responses.create(
                model="doubao-seed-1-6-vision-250815",
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_image",
                                "image_url": f"file://{os.path.abspath(screenshot_path)}"
                            },
                            {
                                "type": "input_text",
                                "text": prompt
                            }
                        ]
                    }
                ]
            )
            
            # 解析返回内容
            res_text = ""
            for item in response.output:
                if hasattr(item, 'content'):
                    for part in item.content:
                        if hasattr(part, 'text'):
                            res_text += part.text
            
            print(f"🤖 豆包位置识别: {res_text}")
            
            # 正则提取 JSON
            match = re.search(r'\{.*\}', res_text, re.DOTALL)
            if match:
                # 容错处理：将单引号替换为双引号，并尝试解析
                clean_json = match.group().replace("'", '"')
                try:
                    return json.loads(clean_json)
                except:
                    import ast
                    return ast.literal_eval(match.group())
            return None
        except Exception as e:
            log_msg = "❌ 位置识别失败: " + str(e)
            print(log_msg)
            if log_signal:
                try:
                    log_signal.emit(log_msg)
                except Exception as e:
                    print("⚠️  日志信号发送失败: " + str(e))
            return None
        finally:
            # 清理临时截图
            if os.path.exists("temp_screenshot.png"):
                try:
                    os.remove("temp_screenshot.png")
                except:
                    pass

    def execute(self, decision, val=None, log_signal=None):
        """三层暴力执行引擎：PointerEvents + MouseEvents + 强制跳转"""
        if not decision:
            return True
        
        action = decision.get('action')
        reason = decision.get('reason', '未知原因')
        
        if action == "none":
            log_msg = "⏭️  跳过操作：" + str(reason)
            print(log_msg)
            if log_signal:
                try:
                    log_signal.emit(log_msg)
                except Exception as e:
                    print("⚠️  日志信号发送失败: " + str(e))
            
            # 如果原因是找不到元素，尝试使用豆包大模型进行位置识别
            if "未包含明确的" in reason or "无对应组件" in reason or "找不到" in reason:
                log_msg = "尝试使用豆包大模型进行位置识别"
                print(log_msg)
                if log_signal:
                    try:
                        log_signal.emit(log_msg)
                    except Exception as e:
                        print("⚠️  日志信号发送失败: " + str(e))
                
                # 使用豆包大模型识别元素位置
                position = self.identify_element_position(reason, log_signal)
                if position and "x" in position and "y" in position:
                    log_msg = f"🎯 豆包大模型识别到元素位置: ({position['x']}, {position['y']})"
                    print(log_msg)
                    if log_signal:
                        try:
                            log_signal.emit(log_msg)
                        except Exception as e:
                            print("⚠️  日志信号发送失败: " + str(e))
                    
                    # 使用 pyautogui 执行操作
                    try:
                        pyautogui.moveTo(position['x'], position['y'])
                        # 默认执行点击操作
                        pyautogui.click()
                        log_msg = "🎯 成功点击元素（基于豆包位置识别）"
                        print(log_msg)
                        if log_signal:
                            try:
                                log_signal.emit(log_msg)
                            except Exception as e:
                                print("⚠️  日志信号发送失败: " + str(e))
                        return True
                    except Exception as e:
                        log_msg = "❌ 基于豆包位置识别的操作失败: " + str(e)
                        print(log_msg)
                        if log_signal:
                            try:
                                log_signal.emit(log_msg)
                            except Exception as e:
                                print("⚠️  日志信号发送失败: " + str(e))
                        return False
                else:
                    log_msg = "❌ 豆包大模型未能识别到元素位置"
                    print(log_msg)
                    if log_signal:
                        try:
                            log_signal.emit(log_msg)
                        except Exception as e:
                            print("⚠️  日志信号发送失败: " + str(e))
                    return False
            
            return True
        
        target_id = decision.get('ai_id')
        safe_val = str(val).replace('"', '\\"') if val else ""
        
        js_code = f"""
        (val) => {{
            const el = window._ai_elements[{target_id}];
            if (!el) return "ELEMENT_LOST";
            
            // 1. 自动居中滚动
            el.scrollIntoView({{block: 'center', behavior: 'instant'}});
            
            if ("{action}" === "click") {{
                // 2. 模拟全套指针事件链，确保点击被正确触发
                const opts = {{ bubbles: true, cancelable: true, view: window, buttons: 1 }};
                
                // 点击事件链
                el.dispatchEvent(new PointerEvent('pointerdown', opts));
                el.dispatchEvent(new MouseEvent('mousedown', opts));
                el.focus();
                el.dispatchEvent(new MouseEvent('mouseup', opts));
                el.dispatchEvent(new PointerEvent('pointerup', opts));
                el.click();
                
                // 3. 针对 <a> 标签的强制跳转保底
                const link = el.tagName === 'A' ? el : el.closest('a');
                if (link && link.href && link.href.startsWith('http')) {{
                    setTimeout(() => {{ window.location.href = link.href; }}, 150);
                }}
                
                // 强制返回OK，表示点击已执行
                return "OK";
            }} 
            else if ("{action}" === "fill") {{
                el.focus();
                el.value = val;
                // 4. 触发框架监听（Vue/React 必备）
                ['input', 'change', 'blur'].forEach(ev => {{
                    el.dispatchEvent(new Event(ev, {{ bubbles: true }}));
                }});
            }}
            else if ("{action}" === "move") {{
                // 5. 鼠标移动到元素位置
                const rect = el.getBoundingClientRect();
                const x = rect.left + rect.width / 2;
                const y = rect.top + rect.height / 2;
                // 返回位置信息
                return JSON.stringify({{ x: x, y: y }});
            }}
            return "OK";
        }}
        """
        try:
            result = self.page.evaluate(js_code, safe_val)
            if result == "OK":
                if action == "click":
                    log_msg = "🎯 成功点击 ID: " + str(target_id) + " (" + str(reason) + ")"
                elif action == "fill":
                    log_msg = "✍️  已填入: " + str(val)
                elif action == "move":
                    log_msg = "🖱️  鼠标已移动到 ID: " + str(target_id) + " (" + str(reason) + ")"
                else:
                    log_msg = "✅ 执行成功 ID: " + str(target_id)
                print(log_msg)
                if log_signal:
                    try:
                        log_signal.emit(log_msg)
                    except Exception as e:
                        print("⚠️  日志信号发送失败: " + str(e))
                return True
            elif action == "move" and result and result != "OK" and result != "ELEMENT_LOST":
                # 处理鼠标移动操作
                try:
                    pos = json.loads(result)
                    pyautogui.moveTo(pos['x'], pos['y'])
                    log_msg = "🖱️  鼠标已移动到坐标: (" + str(pos['x']) + ", " + str(pos['y']) + ") (" + str(reason) + ")"
                    print(log_msg)
                    if log_signal:
                        try:
                            log_signal.emit(log_msg)
                        except Exception as e:
                            print("⚠️  日志信号发送失败: " + str(e))
                    return True
                except Exception as move_error:
                    log_msg = "⚠️  移动操作失败: " + str(move_error)
                    print(log_msg)
                    if log_signal:
                        try:
                            log_signal.emit(log_msg)
                        except Exception as e:
                            print("⚠️  日志信号发送失败: " + str(e))
                    return False
            elif result == "ELEMENT_LOST" or result != "OK":
                log_msg = f"❌ 元素ID {target_id} 在浏览器内存中不存在，尝试使用豆包大模型进行位置识别"
                print(log_msg)
                if log_signal:
                    try:
                        log_signal.emit(log_msg)
                    except Exception as e:
                        print("⚠️  日志信号发送失败: " + str(e))
                
                # 使用豆包大模型识别元素位置
                position = self.identify_element_position(reason, log_signal)
                if position and "x" in position and "y" in position:
                    log_msg = f"🎯 豆包大模型识别到元素位置: ({position['x']}, {position['y']})"
                    print(log_msg)
                    if log_signal:
                        try:
                            log_signal.emit(log_msg)
                        except Exception as e:
                            print("⚠️  日志信号发送失败: " + str(e))
                    
                    # 使用 pyautogui 执行操作
                    try:
                        pyautogui.moveTo(position['x'], position['y'])
                        if action == "click":
                            pyautogui.click()
                            log_msg = "🎯 成功点击元素（基于豆包位置识别）"
                        elif action == "fill":
                            pyautogui.click()
                            pyautogui.typewrite(str(val))
                            log_msg = "✍️  已填入内容（基于豆包位置识别）"
                        print(log_msg)
                        if log_signal:
                            try:
                                log_signal.emit(log_msg)
                            except Exception as e:
                                print("⚠️  日志信号发送失败: " + str(e))
                        return True
                    except Exception as e:
                        log_msg = "❌ 基于豆包位置识别的操作失败: " + str(e)
                        print(log_msg)
                        if log_signal:
                            try:
                                log_signal.emit(log_msg)
                            except Exception as e:
                                print("⚠️  日志信号发送失败: " + str(e))
                        return False
                else:
                    log_msg = "❌ 豆包大模型未能识别到元素位置"
                    print(log_msg)
                    if log_signal:
                        try:
                            log_signal.emit(log_msg)
                        except Exception as e:
                            print("⚠️  日志信号发送失败: " + str(e))
                    return False
        except Exception as e:
            log_msg = "❌ 执行失败 (ID: " + str(target_id) + "): " + str(e)
            print(log_msg)
            if log_signal:
                try:
                    log_signal.emit(log_msg)
                except Exception as e:
                    print("⚠️  日志信号发送失败: " + str(e))
            
            # 尝试使用豆包大模型识别元素位置
            log_msg = "尝试使用豆包大模型进行位置识别"
            print(log_msg)
            if log_signal:
                try:
                    log_signal.emit(log_msg)
                except Exception as e:
                    print("⚠️  日志信号发送失败: " + str(e))
            
            position = self.identify_element_position(reason, log_signal)
            if position and "x" in position and "y" in position:
                log_msg = f"🎯 豆包大模型识别到元素位置: ({position['x']}, {position['y']})"
                print(log_msg)
                if log_signal:
                    try:
                        log_signal.emit(log_msg)
                    except Exception as e:
                        print("⚠️  日志信号发送失败: " + str(e))
                
                # 使用 pyautogui 执行操作
                try:
                    pyautogui.moveTo(position['x'], position['y'])
                    if action == "click":
                        pyautogui.click()
                        log_msg = "🎯 成功点击元素（基于豆包位置识别）"
                    elif action == "fill":
                        pyautogui.click()
                        pyautogui.typewrite(str(val))
                        log_msg = "✍️  已填入内容（基于豆包位置识别）"
                    print(log_msg)
                    if log_signal:
                        try:
                            log_signal.emit(log_msg)
                        except Exception as e:
                            print("⚠️  日志信号发送失败: " + str(e))
                    return True
                except Exception as e:
                    log_msg = "❌ 基于豆包位置识别的操作失败: " + str(e)
                    print(log_msg)
                    if log_signal:
                        try:
                            log_signal.emit(log_msg)
                        except Exception as e:
                            print("⚠️  日志信号发送失败: " + str(e))
                    return False
            else:
                log_msg = "❌ 豆包大模型未能识别到元素位置"
                print(log_msg)
                if log_signal:
                    try:
                        log_signal.emit(log_msg)
                    except Exception as e:
                        print("⚠️  日志信号发送失败: " + str(e))
                return False

class ExecutionThread(QThread):
    # 定义信号
    finished = pyqtSignal()
    error = pyqtSignal(str)
    log = pyqtSignal(str)
    
    def __init__(self, commands):
        super().__init__()
        self.commands = commands
    
    def run(self):
        try:
            import keyboard
            import pyautogui
            import re
            keyboard.add_hotkey('space', lambda: os._exit(0))
            agent = UniversalAIAtuomation()
            
            for item in self.commands:
                if item["cmd"].strip():
                    log_msg = "\n📢 指令: " + item['cmd']
                    print(log_msg)
                    self.log.emit(log_msg)
                    
                    # 等待1秒，确保页面加载完全
                    log_msg = "⏳ 等待页面加载..."
                    print(log_msg)
                    self.log.emit(log_msg)
                    time.sleep(3)
                    
                    # 强制检查页面变化，确保切换到最新页面
                    log_msg = "🔄 检查页面变化..."
                    print(log_msg)
                    self.log.emit(log_msg)
                    agent.get_active_page()
                    
                    # 检查识别方式
                    recognition_type = item.get("recognition_type", "code")
                    
                    if recognition_type == "vision":
                        # 使用视觉识别
                        log_msg = "🔍 使用视觉识别方式..."
                        print(log_msg)
                        self.log.emit(log_msg)
                        
                        # 截图当前屏幕
                        screenshot_path = "temp_screenshot.png"
                        pyautogui.screenshot(screenshot_path)
                        
                        # 构建提示词
                        prompt = f"""请分析以下截图，找到与用户指令相关的元素位置：

用户指令：{item['cmd']}

请返回该元素在屏幕上的坐标位置（x, y），格式为JSON：
{{
  "x": 数字,
  "y": 数字,
  "reason": "简短原因"
}}"""
                        
                        # 调用豆包大模型进行视觉识别
                        log_msg = "🤖 正在调用豆包大模型进行视觉识别..."
                        print(log_msg)
                        self.log.emit(log_msg)
                        
                        try:
                            response = agent.client.responses.create(
                                model="doubao-seed-1-6-vision-250815",
                                input=[
                                    {
                                        "role": "user",
                                        "content": [
                                            {
                                                "type": "input_image",
                                                "image_url": "data:image/png;base64," + base64.b64encode(open(screenshot_path, 'rb').read()).decode('utf-8')
                                            },
                                            {
                                                "type": "input_text",
                                                "text": prompt
                                            }
                                        ]
                                    }
                                ]
                            )
                            
                            # 解析返回内容
                            res_text = ""
                            for resp_item in response.output:
                                if hasattr(resp_item, 'content'):
                                    for part in resp_item.content:
                                        if hasattr(part, 'text'):
                                            res_text += part.text
                            
                            log_msg = f"🤖 豆包位置识别: {res_text}"
                            print(log_msg)
                            self.log.emit(log_msg)
                            
                            # 正则提取 JSON
                            match = re.search(r'\{.*\}', res_text, re.DOTALL)
                            if match:
                                # 容错处理：将单引号替换为双引号，并尝试解析
                                clean_json = match.group().replace("'", '"')
                                try:
                                    position = json.loads(clean_json)
                                except:
                                    import ast
                                    position = ast.literal_eval(match.group())
                                
                                if position and "x" in position and "y" in position:
                                    # 对坐标进行处理：除以1000再乘以1920
                                    x = position['x']
                                    y = position['y']
                                    processed_x = (x / 1000) * 1920
                                    processed_y = (y / 1000) * 1080
                                    
                                    log_msg = f"🎯 豆包大模型识别到元素位置: ({x}, {y})，处理后: ({processed_x:.2f}, {processed_y:.2f})"
                                    print(log_msg)
                                    self.log.emit(log_msg)
                                    
                                    # 使用 pyautogui 执行操作
                                    try:
                                        pyautogui.moveTo(processed_x, processed_y)
                                        # 执行点击操作
                                        pyautogui.click()
                                        log_msg = "🎯 成功点击元素（基于豆包位置识别）"
                                        print(log_msg)
                                        self.log.emit(log_msg)
                                        
                                        # 如果有数据，执行填写操作
                                        if item.get('data'):
                                            pyautogui.typewrite(str(item['data']))
                                            log_msg = f"✍️  已填入: {item['data']}（基于豆包位置识别）"
                                            print(log_msg)
                                            self.log.emit(log_msg)
                                    except Exception as e:
                                        log_msg = f"❌ 基于豆包位置识别的操作失败: {str(e)}"
                                        print(log_msg)
                                        self.log.emit(log_msg)
                                else:
                                    log_msg = "❌ 豆包大模型未能识别到元素位置"
                                    print(log_msg)
                                    self.log.emit(log_msg)
                            else:
                                log_msg = "❌ 无法解析豆包模型返回的结果"
                                print(log_msg)
                                self.log.emit(log_msg)
                        except Exception as e:
                            log_msg = f"❌ 视觉识别失败: {str(e)}"
                            print(log_msg)
                            self.log.emit(log_msg)
                        finally:
                            # 清理临时截图
                            if os.path.exists(screenshot_path):
                                try:
                                    os.remove(screenshot_path)
                                except:
                                    pass
                            
                            # 视觉识别操作后，检查页面是否发生变化
                            log_msg = "🔄 视觉识别后检查页面变化..."
                            print(log_msg)
                            self.log.emit(log_msg)
                            agent.get_active_page()
                    else:
                        # 使用读码识别
                        log_msg = "🔍 使用读码识别方式..."
                        print(log_msg)
                        self.log.emit(log_msg)
                        
                        # 重新获取页面视图，确保检测到页面变化
                        log_msg = "🔄 重新扫描页面..."
                        print(log_msg)
                        self.log.emit(log_msg)
                        
                        decision = agent.think_and_decide(item['cmd'])
                        if decision and "ai_id" in decision:
                            agent.execute(decision, val=item.get('data'), log_signal=self.log)
                            # 执行操作后，检查页面是否发生变化
                            log_msg = "🔄 执行操作后检查页面变化..."
                            print(log_msg)
                            self.log.emit(log_msg)
                            agent.get_active_page()
                        else:
                            log_msg = "🛑 无法匹配目标元素，请确保页面已加载或指令正确。"
                            print(log_msg)
                            self.log.emit(log_msg)
                    
                    time.sleep(2) 
            
            log_msg = "\n✅ 队列执行完毕。"
            print(log_msg)
            self.log.emit(log_msg)
            self.finished.emit()
        except Exception as e:
            log_msg = "❌ 执行线程错误: " + str(e)
            print(log_msg)
            self.log.emit(log_msg)
            self.error.emit(str(e))

class CommandManager(QMainWindow):
    # 定义信号
    log_signal = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI RPA 指令管理器")
        self.setGeometry(100, 100, 800, 600) 
        self.commands = [
            {"cmd": "点击所有站点相关的按钮", "data": None},
            {"cmd": "点击Ozon站点相关选项的按钮", "data": None},
            {"cmd": "点击确定或者我已知晓的按钮", "data": None},
            {"cmd": "点击”批量上架”按钮", "data": None},
            {"cmd": "点击'请选择要上品的店铺(单选)'下拉框", "data": None},
            {"cmd": "点击包含'佳翰'的选项", "data": None},
            {"cmd": "点击是否携带品牌的状态变成不复制，如果为是打开状态则点击，如果为关闭状态则返回none", "data": None},
            {"cmd": "点击智能重写标题/描述，如果为是则返回none，如果为否则点击", "data": None},
            {"cmd": "点击AI改图按钮，不要点击成AI改图模板按钮，如果为是关闭状态则点击，如果为打开状态则返回none", "data": None},
            {"cmd": "点击'请选择AI改图模板'下拉框", "data": None},
            {"cmd": "点击包含'佳翰'文字的选项", "data": None},
            {"cmd": "点击图片上传模式的随机打乱", "data": None},
            {"cmd": "点击是否清空页面内容状态为否，如果为是打开状态则点击，如果为关闭状态则返回none", "data": None},
            #{"cmd": "再百度文库搜索框填写", "data": "openclaw"},
            # {"cmd": "在收件人填写框位置填写1144327872@qq.com", "data": "1144327872@qq.com"},
            # {"cmd": "点击发送", "data": None},
        ]
        self.execution_thread = None
        self.vision_threads = []  # 存储视觉识别线程
        # 连接信号，使用QueuedConnection确保在主线程中执行
        from PyQt5.QtCore import Qt, QThread
        self.log_signal.connect(self.add_log, Qt.QueuedConnection)
        self.init_ui()
    
    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        
        # 表格设置
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["cmd", "data", "识别方式", "视觉识别", "操作"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Fixed)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 80)
        self.table.setColumnWidth(4, 60)
        self.update_table()
        layout.addWidget(self.table)
        
        # 日志显示区域
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setPlaceholderText("执行日志将显示在这里...")
        layout.addWidget(self.log_text)
        
        # 按钮布局
        button_layout = QHBoxLayout()
        add_button = QPushButton("+ 添加指令")
        add_button.clicked.connect(self.add_command)
        run_button = QPushButton("执行指令")
        run_button.clicked.connect(self.run_commands)
        clear_log_button = QPushButton("清空日志")
        clear_log_button.clicked.connect(self.clear_log)
        button_layout.addWidget(add_button)
        button_layout.addWidget(run_button)
        button_layout.addWidget(clear_log_button)
        layout.addLayout(button_layout)
    
    def update_table(self):
        self.table.setRowCount(len(self.commands))
        for row, item in enumerate(self.commands):
            self.table.setItem(row, 0, QTableWidgetItem(item["cmd"]))
            data_str = str(item["data"]) if item["data"] is not None else "None"
            self.table.setItem(row, 1, QTableWidgetItem(data_str))
            
            # 添加识别方式选择下拉框
            recognition_combo = QComboBox()
            recognition_combo.addItems(["读码识别", "视觉识别"])
            # 设置默认值
            if item.get("recognition_type") == "vision":
                recognition_combo.setCurrentIndex(1)
            else:
                recognition_combo.setCurrentIndex(0)
            # 连接信号
            recognition_combo.currentIndexChanged.connect(lambda index, r=row: self.update_recognition_type(r, index))
            self.table.setCellWidget(row, 2, recognition_combo)
            
            # 添加视觉识别按钮
            vision_button = QPushButton("视觉识别")
            vision_button.setFixedSize(70, 30)
            vision_button.clicked.connect(lambda _, r=row: self.run_vision_recognition(r))
            self.table.setCellWidget(row, 3, vision_button)
            
            # 添加删除按钮
            delete_button = QPushButton("-")
            delete_button.setFixedSize(30, 30)
            delete_button.clicked.connect(lambda _, r=row: self.delete_command(r))
            self.table.setCellWidget(row, 4, delete_button)
    
    def add_command(self):
        # 保存表格中的现有数据
        for row in range(self.table.rowCount()):
            cmd_item = self.table.item(row, 0)
            data_item = self.table.item(row, 1)
            if cmd_item:
                self.commands[row]["cmd"] = cmd_item.text()
            if data_item:
                data_text = data_item.text()
                self.commands[row]["data"] = None if data_text == "None" else data_text
        
        # 添加新行
        self.commands.append({"cmd": "", "data": None})
        self.update_table()
        # 滚动到最后一行并编辑
        last_row = len(self.commands) - 1
        self.table.scrollToItem(self.table.item(last_row, 0))
        self.table.editItem(self.table.item(last_row, 0))
    
    def delete_command(self, row):
        # 保存表格中的现有数据
        for r in range(self.table.rowCount()):
            cmd_item = self.table.item(r, 0)
            data_item = self.table.item(r, 1)
            if cmd_item:
                self.commands[r]["cmd"] = cmd_item.text()
            if data_item:
                data_text = data_item.text()
                self.commands[r]["data"] = None if data_text == "None" else data_text
        
        # 删除指定行的指令
        if 0 <= row < len(self.commands):
            self.commands.pop(row)
            self.update_table()
    
    def update_recognition_type(self, row, index):
        # 更新指令的识别方式
        if 0 <= row < len(self.commands):
            if index == 1:
                self.commands[row]["recognition_type"] = "vision"
            else:
                self.commands[row]["recognition_type"] = "code"
    
    def run_vision_recognition(self, row):
        # 保存表格中的现有数据
        for r in range(self.table.rowCount()):
            cmd_item = self.table.item(r, 0)
            data_item = self.table.item(r, 1)
            if cmd_item:
                self.commands[r]["cmd"] = cmd_item.text()
            if data_item:
                data_text = data_item.text()
                self.commands[r]["data"] = None if data_text == "None" else data_text
        
        # 获取当前指令
        if 0 <= row < len(self.commands):
            command = self.commands[row]
            cmd = command["cmd"]
            data = command["data"]
            
            if not cmd:
                QMessageBox.warning(self, "警告", "请先填写指令内容")
                return
            
            # 显示正在执行视觉识别的提示
            self.add_log("\n🔍 正在执行视觉识别...")
            
            # 创建一个临时线程来执行视觉识别，避免阻塞主线程
            class VisionThread(QThread):
                finished = pyqtSignal(dict)
                error = pyqtSignal(str)
                log = pyqtSignal(str)
                
                def __init__(self, cmd, data):
                    super().__init__()
                    self.cmd = cmd
                    self.data = data
                
                def run(self):
                    try:
                        import os
                        import json
                        import re
                        import pyautogui
                        from volcenginesdkarkruntime import Ark
                        
                        # 使用全局配置的 API KEY
                        api_key = ARK_API_KEY
                        if not api_key:
                            # 如果全局配置没有，尝试从环境变量获取
                            api_key = os.getenv('ARK_API_KEY')
                            if not api_key:
                                self.error.emit("请先设置 ARK_API_KEY 环境变量或在代码中配置")
                                return
                        
                        # 初始化 Ark 客户端
                        client = Ark(
                            base_url='https://ark.cn-beijing.volces.com/api/v3',
                            api_key=api_key,
                        )
                        
                        # 截图当前屏幕
                        screenshot_path = "temp_screenshot.png"
                        pyautogui.screenshot(screenshot_path)
                        
                        # 构建提示词
                        prompt = f"""请分析以下截图，找到与用户指令相关的元素位置：

用户指令：{self.cmd}

请返回该元素在屏幕上的坐标位置（x, y），格式为JSON：
{{
  "x": 数字,
  "y": 数字,
  "reason": "简短原因"
}}"""
                        
                        # 调用豆包大模型进行视觉识别
                        self.log.emit("🤖 正在调用豆包大模型进行视觉识别...")
                        response = client.responses.create(
                            model="doubao-seed-1-6-vision-250815",
                            input=[
                                {
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "input_image",
                                            "image_url": "data:image/png;base64," + base64.b64encode(open(screenshot_path, 'rb').read()).decode('utf-8')
                                        },
                                        {
                                            "type": "input_text",
                                            "text": prompt
                                        }
                                    ]
                                }
                            ],
                            max_tokens=256,  # 限制生成长度
                            temperature=0.3,  # 降低温度
                            stream=True  # 使用流式 API
                        )
                        
                        # 解析返回内容
                        res_text = ""
                        for item in response.output:
                            if hasattr(item, 'content'):
                                for part in item.content:
                                    if hasattr(part, 'text'):
                                        res_text += part.text
                        
                        self.log.emit(f"🤖 豆包位置识别: {res_text}")
                        
                        # 正则提取 JSON
                        match = re.search(r'\{.*\}', res_text, re.DOTALL)
                        if match:
                            # 容错处理：将单引号替换为双引号，并尝试解析
                            clean_json = match.group().replace("'", '"')
                            try:
                                position = json.loads(clean_json)
                            except:
                                import ast
                                position = ast.literal_eval(match.group())
                            
                            # 清理临时截图
                            if os.path.exists(screenshot_path):
                                try:
                                    os.remove(screenshot_path)
                                except:
                                    pass
                            
                            self.finished.emit(position)
                        else:
                            self.error.emit("无法解析豆包模型返回的结果")
                    except Exception as e:
                        self.error.emit(f"视觉识别失败: {str(e)}")
                    finally:
                        # 清理临时截图
                        if os.path.exists("temp_screenshot.png"):
                            try:
                                os.remove("temp_screenshot.png")
                            except:
                                pass
            
            # 创建并启动视觉识别线程
            vision_thread = VisionThread(cmd, data)
            self.vision_threads.append(vision_thread)  # 添加到线程列表
            
            # 连接信号
            def on_vision_finished(position):
                if position and "x" in position and "y" in position:
                    # 对坐标进行处理：除以1000再乘以1920
                    x = position['x']
                    y = position['y']
                    processed_x = (x / 1000) * 1920
                    processed_y = (y / 1000) * 1080
                    
                    self.add_log(f"🎯 豆包大模型识别到元素位置: ({x}, {y})，处理后: ({processed_x:.2f}, {processed_y:.2f})")
                    
                    # 使用 pyautogui 执行操作
                    try:
                        pyautogui.moveTo(processed_x, processed_y)
                        # 默认执行点击操作
                        pyautogui.click()
                        self.add_log("🎯 成功点击元素（基于豆包位置识别）")
                        
                        # 如果有数据，执行填写操作
                        if data:
                            pyautogui.typewrite(str(data))
                            self.add_log(f"✍️  已填入: {data}（基于豆包位置识别 ）")
                    except Exception as e:
                        self.add_log(f"❌ 基于豆包位置识别的操作失败: {str(e)}")
                else:
                    self.add_log("❌ 豆包大模型未能识别到元素位置")
                
                # 线程完成后从列表中移除
                if vision_thread in self.vision_threads:
                    self.vision_threads.remove(vision_thread)
            
            def on_vision_error(error_msg):
                self.add_log(f"❌ 视觉识别错误: {error_msg}")
                QMessageBox.critical(self, "视觉识别错误", error_msg)
                
                # 线程完成后从列表中移除
                if vision_thread in self.vision_threads:
                    self.vision_threads.remove(vision_thread)
            
            def on_vision_log(log_msg):
                self.add_log(log_msg)
            
            vision_thread.finished.connect(on_vision_finished)
            vision_thread.error.connect(on_vision_error)
            vision_thread.log.connect(on_vision_log)
            vision_thread.start()
    
    def run_commands(self):
        # 保存表格中的数据
        for row in range(self.table.rowCount()):
            cmd_item = self.table.item(row, 0)
            data_item = self.table.item(row, 1)
            if cmd_item:
                self.commands[row]["cmd"] = cmd_item.text()
            if data_item:
                data_text = data_item.text()
                self.commands[row]["data"] = None if data_text == "None" else data_text
        
        # 清空日志
        self.log_text.clear()
        # 创建并启动执行线程
        self.execution_thread = ExecutionThread(self.commands)
        # 使用QueuedConnection确保在主线程中执行
        from PyQt5.QtCore import Qt
        self.execution_thread.finished.connect(self.on_execution_finished, Qt.QueuedConnection)
        self.execution_thread.error.connect(self.on_execution_error, Qt.QueuedConnection)
        # 连接log信号到CommandManager的log_signal
        self.execution_thread.log.connect(self.log_signal.emit, Qt.QueuedConnection)
        self.execution_thread.start()
    
    def on_execution_finished(self):
        QMessageBox.information(self, "执行完成", "所有指令已执行完毕！")
    
    def add_log(self, log_message):
        self.log_text.append(log_message)
        # 自动滚动到底部
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())
    
    def clear_log(self):
        self.log_text.clear()
    
    def on_execution_error(self, error_message):
        self.add_log("❌ 执行错误: " + error_message)
        QMessageBox.critical(self, "执行错误", f"执行过程中出现错误: {error_message}")

# --- 主程序 ---
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = CommandManager()
    window.show()
    sys.exit(app.exec_())
