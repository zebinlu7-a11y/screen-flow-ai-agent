# -*- coding: utf-8 -*-
"""
GUI Agent 独立进程入口。
通过命令行参数接收任务，执行完成后写结果文件。
不受 Qt asyncio 限制，可用 Playwright MCP。
"""
import sys
import io
# 强制 UTF-8 + 行缓冲（确保子进程管道实时输出）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)
import json
import os
import time

# 确保能找到项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _write_result(result_path, result):
    if result_path:
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)


def _run_one_task(task, use_mcp, keep_browser, cancel_file, hint_file="",
                  shared_mcp=None,
                  user_id="default", memory_key="desktop",
                  is_continuation=False):
    from agent.gui_agent import run_gui_task

    start = time.time()
    try:
        result = run_gui_task(
            task=task,
            use_browser=use_mcp,
            keep_browser_open=keep_browser,
            cancel_file=cancel_file,
            hint_file=hint_file,
            progress_callback=lambda msg: print(f"[进度] {msg}", flush=True),
            shared_mcp=shared_mcp,
            user_id=user_id, memory_key=memory_key,
            is_continuation=is_continuation,
        )
    except Exception as e:
        result = {
            "success": False,
            "message": f"Agent异常: {e}",
            "steps_done": "0/0",
        }
    elapsed = time.time() - start
    result["elapsed"] = f"{elapsed:.1f}s"
    return result, elapsed


def main():
    if len(sys.argv) < 2:
        print("用法: python run_gui_agent.py <任务> [--result result.json] [--mcp] [--keep-browser]")
        sys.exit(1)

    task = sys.argv[1]
    result_path = None
    use_mcp = False
    keep_browser = False
    stay_open = False
    cancel_file = ""
    hint_file = ""
    user_id = "default"
    memory_key = "desktop"
    is_continuation = False

    for i, arg in enumerate(sys.argv):
        if arg == "--result" and i + 1 < len(sys.argv):
            result_path = sys.argv[i + 1]
        if arg == "--mcp":
            use_mcp = True
        if arg == "--keep-browser":
            keep_browser = True
        if arg == "--stay-open":
            stay_open = True
        if arg == "--cancel-file" and i + 1 < len(sys.argv):
            cancel_file = sys.argv[i + 1]
        if arg == "--hint-file" and i + 1 < len(sys.argv):
            hint_file = sys.argv[i + 1]
        if arg == "--user-id" and i + 1 < len(sys.argv):
            user_id = sys.argv[i + 1]
        if arg == "--memory-key" and i + 1 < len(sys.argv):
            memory_key = sys.argv[i + 1]
        if arg == "--is-continuation":
            is_continuation = True

    print(f"[Agent进程] 任务: {task}", flush=True)
    print(f"[Agent进程] MCP: {use_mcp}", flush=True)

    if task == "__warmup_browser__":
        from agent.gui_agent import BrowserMCP
        mcp = BrowserMCP(keep_browser_open=keep_browser)
        ok = mcp.connect(auto_launch=True)
        if mcp and not keep_browser:
            mcp.close()
        result = {
            "success": ok,
            "message": "浏览器自动化环境已就绪" if ok else "浏览器自动化环境预热失败",
            "steps_done": "0/0",
        }
        if result_path:
            with open(result_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False)
        sys.exit(0 if ok else 1)

    shared_mcp = None
    if stay_open and use_mcp:
        from agent.gui_agent import BrowserMCP
        shared_mcp = BrowserMCP(keep_browser_open=keep_browser)

    def run_and_emit(current_task, cont=False):
        if cancel_file and os.path.exists(cancel_file):
            result = {
                "success": False,
                "canceled": True,
                "message": "操作已取消",
                "steps_done": "0/0",
                "elapsed": "0.0s",
            }
            print(f"[Agent进程] 已收到取消信号，跳过任务: {current_task}", flush=True)
            _write_result(result_path, result)
            return result
        result, elapsed = _run_one_task(
            current_task, use_mcp, keep_browser, cancel_file, hint_file=hint_file,
            shared_mcp=shared_mcp,
            user_id=user_id, memory_key=memory_key,
            is_continuation=cont,
        )
        print(f"[Agent进程] 完成 ({elapsed:.1f}s): {result}", flush=True)
        _write_result(result_path, result)
        if result_path:
            print(f"[Agent进程] 结果已写入: {result_path}", flush=True)
        return result

    result = run_and_emit(task, is_continuation)

    if stay_open:
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if not result.get("success"):
            if shared_mcp:
                shared_mcp.close()
            sys.exit(0)
        print("READY", flush=True)
        for line in sys.stdin:
            next_task = line.strip()
            if not next_task:
                continue
            if next_task.upper() == "STOP":
                break
            # ── 记忆门控: stdin 支持 [CONT] 前缀标记延续任务 ──
            is_cont = False
            if next_task.startswith("[CONT]"):
                is_cont = True
                next_task = next_task[6:].strip()
            result = run_and_emit(next_task, is_cont)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if not result.get("success"):
                break
            print("READY", flush=True)
        if shared_mcp:
            shared_mcp.close()
        sys.exit(0)

    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
