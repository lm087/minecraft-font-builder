# Minecraft Font Builder

English | [繁體中文](README.zh-TW.md)

Minecraft Font Builder turns the fonts of Minecraft (Java Edition) into TTF/OTF/WOFF2.

This repository contains no game files. They are downloaded from Mojang, or read from your own copy of the game, when you run the tool.

This tool contains AI-assisted code

NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT

## Getting started

You need [Python](https://www.python.org/downloads/) 3.10+.

1. Download this repository (Code → Download ZIP) and unzip it.
2. Use `Minecraft Font Builder.command` (macOS) or `Minecraft Font Builder.bat` (Windows). The first start takes about a minute.
3. The App opens in your browser. Pick a Minecraft version and the fonts you want, then click Build fonts at the bottom.

The fonts end up in the `output` folder. Keep the terminal window open while you use the App.

It may be blocked by security services, check them before opening.

## Command line

From the downloaded folder:

```bash
pip install .
```

```bash
mcfont build
```

`mcfont --help` lists the commands.

## License

The code is under the [MIT License](LICENSE). It doesn't cover the game files or the fonts made from them, those belong to Mojang, and the Unifont glyphs to the authors of GNU Unifont, whose license is copied next to the fonts as `LICENSE-Unifont.txt`. The Unicode block list in `mcfont/unicode_blocks.py` comes from the Unicode Character Database ([Unicode License v3](LICENSES/Unicode-3.0.txt)).