# idPostIdentiry

本地离线批量识别身份证正面图片，提取姓名和身份证号，并导出 Excel。

## 功能

- 递归扫描 JPG、JPEG、PNG 图片，扩展名不区分大小写。
- 单次最多处理100张图片，不跟随目录符号链接。
- 自动处理 EXIF 方向、常见旋转和轻度透视变形。
- 标准识别失败后，自动进入一次有界的本地识别兜底流程。
- 校验18位身份证号格式、出生日期和校验码。
- 按身份证号去重，并标记姓名冲突。
- 将有效识别结果按“身份证号码、姓名”两列导出为 `.xlsx`。
- 全程本地处理，不向网络发送图片或识别结果。

## 桌面App

桌面App支持 Windows 和 macOS。解压对应平台的构建产物后直接双击运行，不需要安装Python：

- Windows：双击 `身份证识别.exe`。
- macOS：双击 `身份证识别.app`；如果系统首次拦截未公证应用，请右键选择“打开”。

App中的操作步骤：

1. 选择包含身份证正面图片的文件夹。
2. 选择Excel导出文件夹。
3. 填写Excel文件名，按需勾选允许覆盖。
4. 点击“开始识别”，等待进度完成。

Windows构建产物内置Tesseract及中文识别数据；macOS构建产物内置Vision桥接程序。图片、姓名和身份证号码始终在本机处理。

开发者可以在对应系统中自行构建：

```shell
# macOS
./scripts/build_macos.sh
```

```powershell
# Windows PowerShell（构建电脑需要先安装Tesseract）
.\scripts\build_windows.ps1
```

GitHub Actions中的“构建身份证识别桌面App”工作流会同时生成Windows和macOS压缩包。

## 运行环境

- Python 3.9 或更高版本。
- macOS：使用系统 Vision OCR，需要系统已安装 Command Line Tools，可通过 `xcode-select -p` 检查。
- Windows/Linux：使用本机 Tesseract，需要 `tesseract` 命令位于 `PATH`，并已安装 `chi_sim` 和 `osd` 语言包。
- 项目不需要安装第三方 Python 包。

## 命令行使用方法

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

“识别结果”工作表只包含以下两列：

| 身份证号码 | 姓名 |
| --- | --- |
| 以文本格式保存完整18位号码 | 完整姓名 |

身份证号或姓名无法可靠识别时，不向Excel写入该条记录，也不输出未经校验的猜测值；失败和重复数量会在终端或App完成提示中汇总。

标准识别失败时，程序会进入一次有界的本地兜底流程。macOS 会进行灰度化、对比度增强和锐化重试；Windows/Linux 会按固定顺序尝试备用页面分割模式、不同缩放尺度及180度方向。任一候选可靠识别后立即停止，兜底成功的状态为“成功（增强重试）”。

程序不会复制或嵌入身份证图片。

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
