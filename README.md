# batch-renamer

> Preview-first batch file renamer. Rename files by find/replace, regex, prefix/suffix and numbering — and see exactly what will happen **before** anything is touched.

[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-61%20passing-brightgreen.svg)](#测试)

---

## 为什么做这个

批量重命名工具很多,但**大多数一执行就动了你的文件**。名字改错了、顺序乱了、两个文件撞名互相覆盖——等你发现,已经来不及了。

`batch-renamer` 的核心设计是:**先把计划算出来,再执行。**

- 默认**只预览**,不加 `--apply` 绝不碰你的文件
- 重命名计划是一个**纯函数**:输入文件名和规则,输出「谁改成谁」,不依赖磁盘状态
- 所有危险情况(非法字符、重名、Windows 保留名、超长文件名)**收集成问题列表**,而不是直接抛异常中断

## 效果预览

```console
$ renamer ./photos --find IMG --replace holiday
  IMG_001.png  ->  holiday_001.png
  IMG_002.png  ->  holiday_002.png
  IMG_003.png  ->  holiday_003.png

Preview only: 3 file(s) would be renamed.
Re-run with --apply to make the change.
```

## 安装

```bash
git clone https://github.com/Epiphny/batch-renamer.git
cd batch-renamer
pip install -e .
```

或者不安装,直接从源码跑:

```bash
python -m renamer.cli ./photos --find IMG --replace holiday
```

## 用法

```
renamer <目录> [选项]
```

### 常用例子

```bash
# 1. 把文件名里的 IMG 换成 photo(先预览)
renamer ./photos --find IMG --replace photo

# 2. 加前缀后缀,确认无误后执行
renamer ./photos --prefix 2026_ --suffix _final --apply

# 3. 用正则把数字统一替换,再编号补零
renamer ./photos --regex --find "\d+" --replace N --number --padding 3

# 4. 只处理 jpg,且包含子文件夹
renamer ./photos --pattern "*.jpg" --recursive --lowercase

# 5. 按修改时间从新到旧编号(下载目录、照片常用)
renamer ./photos --sort mtime --number --padding 3

# 6. 改错了?撤销最近一次
renamer ./photos --undo
```

### 全部选项

| 选项 | 说明 |
|---|---|
| `目录` | 必填,要处理的文件夹 |
| `--pattern` | glob 匹配,默认 `*` |
| `-r, --recursive` | 递归子文件夹 |
| `--find` / `--replace` | 查找替换 |
| `--regex` | 把 `--find` 当正则表达式 |
| `--prefix` / `--suffix` | 在前面/后面插入文本 |
| `--lowercase` / `--uppercase` | 转小写/大写 |
| `--number` | 追加序号 |
| `--start` | 序号起始值,默认 `1` |
| `--padding` | 序号补零宽度 |
| `--sort` | 处理顺序:`name`(默认,按文件名,不区分大小写)、`mtime`(最新在前)、`size`(最大在前)、`none`(按传入顺序) |
| `--apply` | **真正执行**(不加就是预览) |
| `--undo` | 撤销**最近一次**执行过的重命名 |
| `--color` | 颜色:`auto`(默认,只在真终端上色)、`always`、`never` |
| `--quiet` | 不打印逐条列表 |

## 撤销(--undo)

上面的"安全机制"防的是**改错名字**;`--undo` 防的是**改完之后后悔**。

```bash
# 改错了?立刻反悔
renamer ./photos --undo
```

```
  holiday_001.png  ->  IMG_001.png
  holiday_002.png  ->  IMG_002.png

Restored 2 file(s) to their previous names.
```

工作方式:

- 每次 `--apply` 成功后会往该目录写一个隐藏日志 **`.renamer-undo.json`**,记录这一批改了哪些名字。
- 它是**栈**:`--undo` 弹出并撤销**最近一次**,再执行一次就继续往前撤。想连撤两批就敲两遍。
- 日志撤空后**自动删除**,不会在目录里留垃圾。
- 所有安全检查都在动手之前完成,**任一条不满足就一个文件都不改**:
  - 被改过的文件如果已经不在了(你手动删了/移走了)→ 拒绝
  - 原名如果已经被别的文件占用(不是本批的一部分)→ 拒绝
  - 日志文件损坏或被手改坏 → 拒绝,而且**拒绝发生在重命名之前**,不会让你丢掉可撤销的记录
- `--undo` 与 `--apply` 互斥,且**不需要**再写改名规则。

> 注意:撤销只覆盖**本工具做过的**重命名。如果你在资源管理器里手动改过名字,或中途用过别的工具,撤销会因"文件找不到"而拒绝——这是刻意的,宁可不做也不乱做。

## 颜色(--color)

```bash
renamer ./photos --find IMG --replace photo            # auto(默认)
renamer ./photos --find IMG --replace photo --color always
```

| 模式 | 行为 |
|---|---|
| `auto`(默认) | 只在输出是**真终端**、且没有设置 `NO_COLOR`、`TERM` 不是 `dumb` 时上色 |
| `always` | 强制上色(例如要 `\| less -R`) |
| `never` | 强制关闭 |

配色:旧文件名暗色,箭头和新文件名绿色,问题行红色,提示语黄色。遵循 [`NO_COLOR`](https://no-color.org/) 约定——**只要该环境变量存在**(哪怕值为空)就会关掉 `auto` 的颜色,`always` 仍然强制上色。

程序化调用时颜色**默认关闭**(`format_plan(plan)` 永远不含转义序列),需要时显式传 `color=True`,这样捕获或解析输出的人不会被转义码干扰。

## 安全机制

这是本项目最用心的部分,也是写测试最多的地方:

| 情况 | 处理方式 |
|---|---|
| 目标名含 `<>:"/\|?*` | 替换为 `_`,并**报告问题** |
| 两个文件改成同一个名字 | 检测到重复,**拒绝执行** |
| 规则把名字清空 | 报告「empty name」,拒绝执行 |
| 目标是 `CON`/`PRN`/`COM1` 等保留名 | 报告并拒绝(Windows 上会出错) |
| 文件名超过 255 字符 | 报告并拒绝 |
| 目标已存在且**不属于本次计划** | 抛 `FileExistsError`,不覆盖 |
| 计划有任何一个问题 | `apply_plan()` 直接 `ValueError`,**一个文件都不动** |
| 撤销时被改的文件已不在 | 拒绝撤销,**原样不动** |
| 撤销时原名已被别人占用 | 拒绝撤销,**原样不动** |
| 撤销日志损坏 | 拒绝(且在动手之前就拒绝) |
| 日志文件本身 | 不会被当成待改名文件处理 |

还有一个容易被忽略的坑:**名称互换会自我覆盖**。比如把 `a.txt` 和 `b.txt` 的名字对调,直接执行的话第二个会覆盖第一个。本项目用**两阶段重命名**解决:先把所有源文件改成临时名,再统一改成目标名。

## 测试

```bash
pip install pytest
pytest -v
```

覆盖了纯函数规划逻辑、四种排序模式、每一项安全检查、撤销栈的完整往返与各条拒绝规则、颜色的开/关/自动判定与转义码配对、以及真实磁盘操作(在项目内的 `_scratch/` 里跑,用完即删,不碰你的文件)。

测试按模块分开:`tests/test_core.py` 覆盖规划、执行与撤销,`tests/test_color.py` 覆盖颜色与 CLI 的端到端行为。

### 在沙箱/受限环境里跑测试

系统临时目录不可写时(例如 AI 代理沙箱里的 `C:\Users\PC\AppData\Local\Temp` 是只读的),pytest 默认的临时根目录(`%TEMP%\pytest-of-<用户>`)建不出来,用到临时目录的测试会以 `PermissionError` 失败。两层处理:

- `tests/conftest.py` 把 `tmp_path` 夹具改到项目内的 `_scratch/caseNNN/`,每个测试用完即删 —— 这是主要机制。
- `pyproject.toml` 里用 `addopts = "--basetemp=./_scratch"` 覆盖 pytest **自带的**临时目录机制(`tmp_path_factory` / `tmpdir`),那是 conftest 覆盖不到的。该设置需要 **pytest ≥ 7.3**(`tmp_path_retention_policy` 从这个版本才有),所以 `dev` 依赖也钉在 `pytest>=7.3`。

自动配置在**从项目根目录**启动 pytest 时生效。需要手动指定时:

```bash
pytest --basetemp=./_scratch
```

注意 `--basetemp` 的相对路径按**当前工作目录**解析(不是 pytest 的 rootdir),而且该目录每次会话开始都会被清空,所以别往里放东西。

## 项目结构

```
batch-renamer/
├── renamer/
│   ├── __init__.py      # 对外导出
│   ├── core.py          # 核心逻辑(纯函数 + 执行器 + 撤销)
│   ├── color.py         # 颜色判定与 ANSI 包装
│   └── cli.py           # 命令行入口
├── tests/
│   ├── conftest.py      # 临时目录夹具(项目内 _scratch,用完即删)
│   ├── test_core.py     # 规划 / 执行 / 撤销
│   └── test_color.py    # 颜色与 CLI 端到端
├── .gitignore
├── LICENSE
├── pyproject.toml
└── README.md
```

## 设计取舍

**为什么 `core.py` 里没有一句 `print`?**
所有输出都由 `format_plan()` 生成文本、`cli.py` 负责打印。这样核心逻辑可以被任何界面复用(CLI、GUI、Web),也更好测试。

**为什么问题要收集而不是抛异常?**
一次性告诉用户"这几条有问题",比改一条报一次错、来回试十遍友好得多。

## 已知限制

- 排序键只有文件名、修改时间、大小三种(`--sort`),不支持 EXIF 拍摄时间等自定义键
- 撤销只认本工具写下的日志:一旦目录里没有 `.renamer-undo.json`,就没法回退
- 不处理符号链接的指向(会当作普通文件重命名)

## License

[MIT](LICENSE)
