"""trace 包（P0-02 A3）。

为什么这个包要有一个 __init__.py，而 llm/ 或 tools/ 没有：

  Python 的"命名空间包"（PEP 420，目录下没有 __init__.py）在所有 sys.path 条目里
  <b>优先级最低</b>：解析器会先记住这个目录，然后继续往后找，只要后面某一个条目里
  存在同名的<b>常规</b>模块/包，就用那个。

  标准库里正好有一个常规模块叫 trace（CPython 的语句追踪工具）。于是没有 __init__.py 时：

      $ uv run python -c "from trace.tracer import record"
      ModuleNotFoundError: No module named 'trace.tracer'; 'trace' is not a package

  —— 我们自己的 trace/ 目录被标准库的 trace.py 顶掉了。
  加上 __init__.py 后它是常规包，按 sys.path 顺序（脚本目录 → PYTHONPATH → 标准库）先被命中。

  这条坑也说明：包名撞标准库/第三方库名时，"能不能 import"取决于包形态，而不只是目录在不在。
  若将来要彻底躲开，把包改名为 tracing/ 是更省心的办法（本卡不动，因为规格钉了 trace/ 这个名字）。
"""
