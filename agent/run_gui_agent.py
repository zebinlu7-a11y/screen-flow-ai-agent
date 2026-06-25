"""
GUI Agent 独立进程入口。
通过命令行参数接收任务，执行完成后写结果文件。
不受 Qt asyncio 限制，可用 Playwright MCP。
"""
import sys
import io
# 强制 UTF-8 输出（Windows 控制台默认为 GBK）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
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

    for i, arg in enumerate(sys.argv):
        if arg == "--result" and i + 1 < len(sys.argv):
            result_path = sys.argv[i + 1]
        if arg == "--mcp":
            use_mcp = True

    print(f"[Agent进程] 任务: {task}")
    print(f"[Agent进程] MCP: {use_mcp}")

    from agent.gui_agent import run_gui_task

    start = time.time()
    result = run_gui_task(
        task=task,
        use_browser=use_mcp,
        progress_callback=lambda msg: print(f"[进度] {msg}"),
    )

    elapsed = time.time() - start
    result["elapsed"] = f"{elapsed:.1f}s"
    print(f"[Agent进程] 完成 ({elapsed:.1f}s): {result}")

    if result_path:
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        print(f"[Agent进程] 结果已写入: {result_path}")

    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
