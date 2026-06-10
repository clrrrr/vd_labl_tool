#!/usr/bin/env python3
"""测试新的文件名格式"""

from video_label_tool.metadata import parse_filename_pattern

# 测试新格式: factory_id{5位数字}[_{process_name}]
test_cases = [
    ("factory100001", ("factory1", "00001", "")),
    ("factory100001_装配", ("factory1", "00001", "装配")),
    ("ABC12345", ("ABC", "12345", "")),
    ("ABC12345_检验", ("ABC", "12345", "检验")),
    ("factory_a00001_测试", ("factory_a", "00001", "测试")),
    # 不匹配的情况
    ("factory1_1234", None),  # 只有4位数字
    ("video", None),  # 没有5位数字
    ("factory_123456", None),  # 6位数字
]

print("测试新格式解析:")
for stem, expected in test_cases:
    result = parse_filename_pattern(stem)
    status = "✓" if result == expected else "✗"
    print(f"{status} {stem:30} → {result}")
    if result != expected:
        print(f"  预期: {expected}")
