import asyncio
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    project_dir = Path(__file__).resolve().parent.parent
    os.chdir(project_dir)
    sys.path.insert(0, str(project_dir))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[str(project_dir / "app")],
        # __pycache__/*.pyc 由 import 时自动重写，不是真正的源码改动，
        # 排除掉避免误触发 reload 打断正在处理中的长耗时请求（如 Agent 多轮工具调用）
        reload_excludes=["*/__pycache__/*", "*.pyc"],
        timeout_graceful_shutdown=120,
    )
