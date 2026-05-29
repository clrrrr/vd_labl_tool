# Video Label Tool

桌面工具:批量观看一个文件夹里的 MP4/MOV 视频,为每个视频填写流程名称和涉及物品,
标注结果以 JSON 形式写入视频文件的 `comment` 元数据。Windows 资源管理器 → 属性 →
详细信息 → "备注" 字段会直接显示这段 JSON。

## JSON 格式

```json
{
  "Process Name": "Lens Reflector Bowl Installation",
  "Parts Involved": [
    "Reflector",
    "Glass lens",
    "Electric screwdriver"
  ]
}
```

## 开发环境运行 (macOS / Linux)

```bash
cd ~/Develop/video_label_tool
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 把系统的 ffmpeg/ffprobe 链到 vendor/, 仅开发期用
ln -sf "$(which ffmpeg)"  vendor/ffmpeg
ln -sf "$(which ffprobe)" vendor/ffprobe

python main.py
```

## Windows 打包

1. 把 `ffmpeg.exe` 和 `ffprobe.exe` (从 https://www.gyan.dev/ffmpeg/builds/ 的 essentials 包) 放到 `vendor/` 目录
2. 在 Windows 上 `pip install -r requirements.txt pyinstaller`
3. 运行 `build_windows.bat`
4. 产出 `dist/video_label_tool/`,把整个文件夹拷到目标机器,双击 `video_label_tool.exe`

## 文件结构

```
video_label_tool/
├── main.py                       # 入口
├── video_label_tool/
│   ├── ffbin.py                  # 定位 bundled ffmpeg/ffprobe
│   ├── metadata.py               # 读写视频 comment
│   ├── app.py                    # 主窗口
│   ├── file_list_view.py         # 文件列表 + 并发扫描
│   ├── annotate_window.py        # 标注窗 (播放器 + 表单)
│   └── ui_strings.py             # 界面字串
├── vendor/                       # 放 ffmpeg.exe / ffprobe.exe (gitignored)
├── video_label_tool.spec         # PyInstaller spec
└── build_windows.bat             # 一键打包
```
