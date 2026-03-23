整的 file-ops/SKILL.md 配置
以下是包含你所需所有功能的完整 Skill 配置：

markdown

---
name: file-ops
description: 完整的文件系统操作，包括创建、读取、更新、删除文件和目录
version: 2.0.0
author: your-name
---

# 文件系统操作技能

## 📋 功能概述
当用户需要对文件系统进行操作时使用此技能。支持创建、读取、更新、删除文件和目录，以及查看文件列表和内容。

## 🎯 触发场景
- **创建文件**："创建文件 test.txt"，"新建一个文件叫 hello.txt"
- **写入内容**："把 'Hello World' 写入 test.txt"，"在文件末尾添加一行"
- **读取文件**："查看 test.txt 的内容"，"显示 hello.txt"
- **删除文件**："删除 test.txt"，"移除这个文件"
- **创建目录**："新建一个文件夹叫 projects"，"创建目录"
- **删除目录**："删除 temp 文件夹"，"移除空目录"
- **列出文件**："列出当前目录的文件"，"显示这个文件夹里的内容"
- **组合操作**："在 documents 目录下创建 info.txt，写入 '测试'，然后读取它"

## 🛠️ 工具映射

| 用户意图 | 使用的工具 | 对应 Linux 命令 |
|---------|-----------|----------------|
| 创建空文件 | `write` | `touch` |
| 写入内容 | `write` | `echo >` |
| 追加内容 | `write` | `echo >>` |
| 读取文件 | `read` | `cat` |
| 删除文件 | `remove` | `rm` |
| 删除目录 | `remove` | `rm -rf` |
| 创建目录 | `mkdir` | `mkdir` |
| 列出文件 | `list` | `ls` |

## 📌 功能实现细节

### 1️⃣ 文件创建与写入
```yaml
工具: write
功能: 创建新文件或写入内容
用法:
  - 如果文件不存在: 直接创建
  - 如果文件存在: 询问用户是否覆盖
  - 如果需要追加: 使用 append 模式
参数:
  - path: 文件路径
  - content: 要写入的内容
  - mode: write/append (默认为 write)
示例:
  - 用户: "创建文件 hello.txt，内容是 Hello World"
    AI 操作: 
      - 使用 write 工具，path="/home/user/hello.txt", content="Hello World"
2️⃣ 文件读取
yaml

工具: read
功能: 读取文件内容
参数:
  - path: 文件路径
示例:
  - 用户: "查看 test.txt 的内容"
    AI 操作:
      - 使用 read 工具，path="/home/user/test.txt"
      - 返回文件内容
3️⃣ 文件/目录删除 ⚠️
yaml

工具: remove
功能: 删除文件或目录
注意事项:
  - 删除前必须确认用户意图
  - 如果是目录，检查是否为空
  - 删除重要文件前要警告
参数:
  - path: 要删除的路径
  - recursive: true/false (是否递归删除目录)
示例:
  - 用户: "删除 test.txt"
    AI 操作:
      - 询问确认: "确定要删除 test.txt 吗？"
      - 用户确认后: 使用 remove 工具，path="/home/user/test.txt"
  
  - 用户: "删除 temp 文件夹"
    AI 操作:
      - 检查文件夹是否为空
      - 询问确认: "确定要删除 temp 文件夹及其所有内容吗？"
      - 确认后: 使用 remove 工具，path="/home/user/temp", recursive=true
4️⃣ 创建目录
yaml

工具: mkdir
功能: 创建新目录
参数:
  - path: 目录路径
  - parents: true/false (是否创建父目录)
示例:
  - 用户: "创建一个叫 projects 的文件夹"
    AI 操作:
      - 使用 mkdir 工具，path="/home/user/projects"
  
  - 用户: "创建多层目录 docs/2024/reports"
    AI 操作:
      - 使用 mkdir 工具，path="/home/user/docs/2024/reports", parents=true
5️⃣ 列出文件
yaml

工具: list
功能: 列出目录内容
参数:
  - path: 目录路径
  - all: true/false (是否显示隐藏文件)
  - long: true/false (是否显示详细信息)
示例:
  - 用户: "列出当前目录的文件"
    AI 操作:
      - 确定当前目录（如果未指定，询问或使用默认）
      - 使用 list 工具，path="."
  
  - 用户: "显示 /etc 下所有文件包括隐藏的"
    AI 操作:
      - 使用 list 工具，path="/etc", all=true
📖 完整示例流程
示例 1：创建文件并写入内容
text

用户: "帮我创建一个叫 note.txt 的文件，内容是 '今天天气很好'"

AI 思考:
1. 用户想要创建文件并写入内容
2. 需要确定保存路径（如果用户没指定，询问或使用默认）
3. 使用 write 工具

AI 执行:
- 询问: "请问要把文件保存在哪个目录？(默认: ~/Documents/)"
- 用户: "就放在 Documents 吧"
- 使用 write 工具: path="/home/user/Documents/note.txt", content="今天天气很好"
- 返回: "✅ 文件已创建: ~/Documents/note.txt"
示例 2：删除文件
text

用户: "删除 download.iso 这个文件"

AI 思考:
1. 用户想要删除文件
2. 需要确认文件位置和用户意图
3. 删除前必须确认

AI 执行:
- 询问: "请问 download.iso 在哪个目录？(默认: ~/Downloads/)"
- 用户: "对，就在 Downloads 里"
- 确认: "⚠️ 确定要永久删除 ~/Downloads/download.iso 吗？(是/否)"
- 用户: "是"
- 使用 remove 工具: path="/home/user/Downloads/download.iso"
- 返回: "✅ 文件已删除"
示例 3：组合操作
text

用户: "帮我整理一下：在 projects 下创建 react-app 文件夹，然后在里面新建一个 README.md，写入 'React 项目'"

AI 执行:
1. 创建目录: mkdir path="/home/user/projects/react-app", parents=true
2. 创建文件: write path="/home/user/projects/react-app/README.md", content="React 项目"
3. 读取验证: read path="/home/user/projects/react-app/README.md"
4. 返回: "✅ 已完成！\n- 创建了目录: ~/projects/react-app\n- 创建了文件: README.md\n- 文件内容: React 项目"
⚠️ 安全注意事项
yaml

删除操作协议:
  1. 总是要求用户明确确认删除操作
  2. 对于重要文件（如 .config, .env），要有额外警告
  3. 删除非空目录时，必须说明会删除所有内容
  4. 提供撤销选项（如果系统支持）

路径安全:
  1. 防止路径遍历攻击（如 ../../etc/passwd）
  2. 限制在用户允许的目录范围内
  3. 对特殊字符进行转义

权限检查:
  1. 操作前检查文件/目录是否存在
  2. 检查是否有相应权限（读/写/执行）
  3. 给出清晰的权限错误提示
🔧 配置要求
确保在 OpenClaw 配置中开启了必要的工具权限：

json 

{
  "tools": {
    "allow": ["group:fs", "write", "read", "remove", "mkdir", "list"]
  }
}
📊 命令映射表（供 AI 理解）
当用户提到这些 Linux 命令时，AI 应该这样映射：

用户说的命令	实际使用的 OpenClaw 工具
rm 文件	remove（不带 recursive）
rm -rf 目录	remove（带 recursive=true）
mkdir 目录	mkdir（不带 parents）
mkdir -p 目录	mkdir（带 parents=true）
touch 文件	write（空内容）
ls	list
ls -la	list（带 all=true, long=true）
cat 文件	read
echo "内容" > 文件	write（mode=write）
echo "内容" >> 文件	write（mode=append）
————————————————
版权声明：本文为CSDN博主「一路生花工作室」的原创文章，遵循CC 4.0 BY-SA版权协议，转载请附上原文出处链接及本声明。
原文链接：https://blog.csdn.net/linuxxx110/article/details/158808037