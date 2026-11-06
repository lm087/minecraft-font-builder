# Minecraft Font Builder

[English](README.md) | 繁體中文

將 Minecraft（Java 版）的遊戲字型轉換成 TTF、OTF、WOFF2 的工具。

這裡不含任何遊戲檔案。執行時才會從 Mojang 下載，或讀取你自己電腦上的遊戲檔案。

NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT

## 開始使用

需要 [Python](https://www.python.org/downloads/) 3.10+。

1. 下載這個 repo (Code → Download ZIP) 並解壓縮。
2. 使用 `Minecraft Font Builder.command` (macOS) 或 `Minecraft Font Builder.bat` (Windows)。第一次啟動約需一分鐘。
3. 瀏覽器會打開程式。選好 Minecraft 版本和要轉的字型，按最下方的「產生字型」。

完成的字型在 `output` 資料夾裡。使用期間請不要關掉終端機視窗。

這個 App 可能會被安全軟體或服務攔截，在使用前請檢查設定。

## 指令

在下載的資料夾裡執行：

```bash
pip install .
```

```bash
mcfont build
```

`mcfont --help` 會列出所有指令。

## 授權

程式碼採用 [MIT 授權](LICENSE)。授權不包含遊戲檔案和轉出來的字型，它們屬於 Mojang，其中 Unifont 字形屬於 GNU Unifont 的作者，其授權會以 `LICENSE-Unifont.txt` 放在字型旁邊。`mcfont/unicode_blocks.py` 裡的 Unicode 區塊清單來自 Unicode 字元資料庫 ([Unicode License v3](LICENSES/Unicode-3.0.txt))。