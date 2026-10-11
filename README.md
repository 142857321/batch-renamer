# batch-renamer

> Preview-first batch file renamer. Rename files by find/replace, regex, prefix/suffix and numbering — and see exactly what will happen **before** anything is touched.

[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-21%20passing-brightgreen.svg)](#测试)

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
| `--apply` | **真正执行**(不加就是预览) |
| `--quiet` | 不打印逐条列表 |

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

还有一个容易被忽略的坑:**名称互换会自我覆盖**。比如把 `a.txt` 和 `b.txt` 的名字对调,直接执行的话第二个会覆盖第一个。本项目用**两阶段重命名**解决:先把所有源文件改成临时名,再统一改成目标名。

## 测试

```bash
pip install pytest
pytest -v
```

覆盖了纯函数规划逻辑、每一项安全检查、以及真实磁盘操作(在 `tmp_path` 里跑,不碰你的文件)。

## 项目结构

```
batch-renamer/
├── renamer/
│   ├── __init__.py      # 对外导出
│   ├── core.py          # 核心逻辑(纯函数 + 执行器)
│   └── cli.py           # 命令行入口
├── tests/
│   └── test_core.py     # 21 个测试
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

- 目前按**文件名**排序,不支持自定义排序(如按修改时间)
- 不支持撤销(建议重要目录先备份,或先 `git init` 管理起来)
- 不处理 symlink 的指向

## License

[MIT](LICENSE)
