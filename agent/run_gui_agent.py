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


def main():
    if len(sys.argv) < 2:
        print("用法: python run_gui_agent.py <任务> [--result result.json] [--mcp]")
        sys.exit(1)

    task = sys.argv[1]
    result_path = None
    use_mcp = False
    cancel_file = ""

    for i, arg in enumerate(sys.argv):
        if arg == "--result" and i + 1 < len(sys.argv):
            result_path = sys.argv[i + 1]
        if arg == "--mcp":
            use_mcp = True
        if arg == "--cancel-file" and i + 1 < len(sys.argv):
            cancel_file = sys.argv[i + 1]

    print(f"[Agent进程] 任务: {task}", flush=True)
    print(f"[Agent进程] MCP: {use_mcp}", flush=True)

    if task == "__warmup_browser__":
        from agent.gui_agent import BrowserMCP
        mcp = BrowserMCP()
        ok = mcp.connect(auto_launch=True)
        if mcp:
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

    try:
        from agent.gui_agent import run_gui_task

        start = time.time()
        result = run_gui_task(
            task=task,
            use_browser=use_mcp,
            cancel_file=cancel_file,
            progress_callback=lambda msg: print(f"[进度] {msg}", flush=True),
        )
    except Exception as e:
        result = {
            "success": False,
            "message": f"Agent异常: {e}",
            "steps_done": "0/0",
        }
        start = time.time()

    elapsed = time.time() - start
    result["elapsed"] = f"{elapsed:.1f}s"
    print(f"[Agent进程] 完成 ({elapsed:.1f}s): {result}", flush=True)

    if result_path:
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        print(f"[Agent进程] 结果已写入: {result_path}", flush=True)

    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
