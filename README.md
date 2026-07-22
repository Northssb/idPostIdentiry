# idPostIdentiry

本地离线批量识别身份证正面图片，提取姓名和身份证号，并导出 Excel。

## 功能

- 递归扫描 JPG、JPEG、PNG 图片，扩展名不区分大小写。
- 单次最多处理100张图片，不跟随目录符号链接。
- 自动处理 EXIF 方向、常见旋转和轻度透视变形。
- 校验18位身份证号格式、出生日期和校验码。
- 按身份证号去重，并标记姓名冲突。
- 将来源文件、姓名、身份证号和识别状态导出为 `.xlsx`。
- 全程本地处理，不向网络发送图片或识别结果。

## 运行环境

- Python 3.9 或更高版本。
- macOS：使用系统 Vision OCR，需要系统已安装 Command Line Tools，可通过 `xcode-select -p` 检查。
- Windows/Linux：使用本机 Tesseract，需要 `tesseract` 命令位于 `PATH`，并已安装 `chi_sim` 和 `eng` 语言包。
- 项目不需要安装第三方 Python 包。

## 使用方法

直接运行且不填写参数时，macOS 会依次弹出身份证图片文件夹和 Excel 导出文件夹选择框：

```shell
/usr/bin/python3 id_card_ocr.py
```

也可以通过命令行直接指定文件夹：

```shell
python3 id_card_ocr.py \
  --input-dir "/path/to/身份证图片" \
  --output-dir "/path/to/导出目录"
```

指定输出文件名：

```shell
python3 id_card_ocr.py \
  --input-dir "/path/to/身份证图片" \
  --output-dir "/path/to/导出目录" \
  --output-name "身份证识别结果.xlsx"
```

目标文件已存在时，必须显式允许覆盖：

```shell
python3 id_card_ocr.py \
  --input-dir "/path/to/身份证图片" \
  --output-dir "/path/to/导出目录" \
  --output-name "身份证识别结果.xlsx" \
  --overwrite
```

未指定 `--output-name` 时，程序自动使用 `身份证识别结果_YYYYMMDD_HHMMSS.xlsx`。

## Excel 内容

工作表名称为“识别结果”，包含以下四列：

| 来源文件 | 姓名 | 身份证号 | 识别状态 |
| --- | --- | --- | --- |
| 相对输入目录的图片路径 | 识别成功时填写 | 以文本格式保存完整号码 | 成功、失败原因或重复说明 |

身份证号或姓名无法可靠识别时，两项字段均留空，不输出未经校验的猜测值。

## 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | 全部识别成功且没有重复 |
| `1` | 参数、目录、OCR 初始化或导出等致命错误 |
| `2` | 已生成 Excel，但包含识别失败或重复记录 |

## 隐私说明

- 程序不修改原始图片，也不缓存身份证图片。
- 终端只显示数量和状态，不打印姓名、身份证号或 OCR 原文。
- 导出的 Excel 包含完整身份证号，请限制访问权限并妥善保管。

详细需求与验收标准见 [身份证识别脚本需求清单](docs/身份证识别脚本需求清单.md)。
