# vendor/

把 `ffmpeg` 和 `ffprobe` 二进制放到这里。

## Windows

从 https://www.gyan.dev/ffmpeg/builds/ 下载 "release essentials" zip,解压后把
`bin/ffmpeg.exe` 和 `bin/ffprobe.exe` 拷到本目录。

## macOS / Linux (仅开发)

直接软链系统已装的:

```bash
ln -sf "$(which ffmpeg)"  vendor/ffmpeg
ln -sf "$(which ffprobe)" vendor/ffprobe
```

或者 `brew install ffmpeg` 然后链。

这个目录里的二进制文件被 `.gitignore` 排除,不会进 git。
