"""共享中立层：全系统的依赖锚点。

utils 不依赖 kernel/organs/host 中的任何模块；
所有层（内核/器官/宿主）都可以 import utils。
"""
