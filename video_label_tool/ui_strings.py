"""Centralized UI strings (Chinese)."""

APP_TITLE = "视频标注工具"

# Main window
BTN_OPEN_FOLDER = "选择文件夹"
BTN_REFRESH = "刷新"
BTN_EXPORT_XLSX = "导出表格"
BTN_PROJECT_INFO_PROMPT = "填写项目信息(必须)"
BTN_PROJECT_INFO_TEMPLATE = "项目: {factory_id} · {factory_name} (修改)"
LABEL_NO_FOLDER = "未选择文件夹"
LABEL_FOLDER_PREFIX = "当前文件夹: "
LABEL_COUNT_TEMPLATE = "共 {total} 个视频 · 已标注 {done} · 未标注 {todo}"

# Project info dialog
DLG_PROJECT_INFO_TITLE = "项目信息"
DLG_PROJECT_INFO_FACTORY_ID = "工厂编号:"
DLG_PROJECT_INFO_FACTORY_NAME = "工厂名:"
DLG_PROJECT_INFO_HELP_ID = "字母、数字、下划线、连字符,1–30 字符"
DLG_PROJECT_INFO_VALIDATE_ID = "工厂编号格式不合法"
DLG_PROJECT_INFO_VALIDATE_NAME = "工厂名不能为空"

# Rename
RENAME_WARNING_TITLE = "部分文件重命名失败"
RENAME_WARNING_TEMPLATE = "以下文件无法重命名(已跳过):\n{lines}"

# Export
DLG_EXPORT_NOTHING_TITLE = "没有可导出内容"
DLG_EXPORT_NOTHING_MSG = "当前没有视频可导出。先选择一个含视频的文件夹。"
DLG_EXPORT_OK_TITLE = "导出成功"
DLG_EXPORT_OK_TEMPLATE = "已导出 {count} 行到:\n{path}"
DLG_EXPORT_FAIL_TITLE = "导出失败"

# File list columns
COL_FILENAME = "文件名"
COL_STATUS = "状态"
COL_PROCESS_NAME = "Process Name"
COL_DURATION = "时长"
COL_SIZE = "大小"
COL_MTIME = "修改时间"

# Status values
STATUS_SCANNING = "扫描中…"
STATUS_SAVING = "保存中…"
STATUS_DONE = "已标注"
STATUS_TODO = "未标注"
STATUS_ERROR = "读取失败"

# Context menu
MENU_COPY_PARTS = "复制物品清单"
MENU_PASTE_PARTS = "粘贴物品清单"
MENU_PASTE_PARTS_TEMPLATE = "粘贴物品清单 ({n} 项)"
TOAST_PARTS_COPIED = "已复制 {n} 个物品到剪贴板"
TOAST_PARTS_PASTED = "已粘贴到 {filename}"

# Annotate window
ANNO_TITLE_TEMPLATE = "标注 — {filename}"
ANNO_LABEL_PROCESS = "Process Name (流程名称):"
ANNO_LABEL_PARTS = "Parts Involved (涉及物品):"
ANNO_PART_PLACEHOLDER = "输入物品名,回车或点添加"
ANNO_BTN_ADD = "添加"
ANNO_BTN_REMOVE = "删除选中"
ANNO_BTN_SAVE = "保存"
ANNO_BTN_CANCEL = "取消"

# Player
PLAY_BTN_PLAY = "播放"
PLAY_BTN_PAUSE = "暂停"
PLAY_LABEL_SPEED = "速度:"

# Dialogs
DLG_SAVE_OK_TITLE = "保存成功"
DLG_SAVE_OK_MSG = "标注已写入视频文件。"
DLG_SAVE_FAIL_TITLE = "保存失败"
DLG_VALIDATE_TITLE = "请补全标注"
DLG_VALIDATE_PROCESS_EMPTY = "Process Name 不能为空。"
DLG_VALIDATE_PARTS_EMPTY = "Parts Involved 至少需要一项。"
DLG_FFMPEG_MISSING_TITLE = "缺少 ffmpeg"
DLG_FFMPEG_MISSING_MSG = (
    "找不到 ffmpeg/ffprobe。请把 ffmpeg.exe 和 ffprobe.exe 放到程序目录下的 "
    "vendor/ 文件夹后重新启动。"
)
DLG_DIRTY_TITLE = "未保存的更改"
DLG_DIRTY_MSG = "标注内容已修改但未保存,确定要关闭吗?"
