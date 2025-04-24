#!/usr/bin/env python3
import os
import xml.etree.ElementTree as ET
import json
from pathlib import Path

def parse_uvprojx(uvprojx_path):
    """
    解析 Keil uvprojx 文件，提取编译参数和文件列表
    返回包含全局编译选项和文件列表的字典
    """

    if not os.path.exists(uvprojx_path):
        raise FileNotFoundError(f"uvprojx file not found: {uvprojx_path}")

    try:
        tree = ET.parse(uvprojx_path)
        root = tree.getroot()
    except ET.ParseError as e:
        raise ValueError(f"Invalid XML in uvprojx file: {uvprojx_path}") from e

    # 提取全局编译选项
    target = root.find('Targets/Target')
    global_flags = {
        'defines': [],
        'includes': [],
        'misc': []
    }

    # 提取宏定义（Define）
    defines = target.find('TargetOption/TargetArmAds/Cads/VariousControls/Define')
    print(f"Defines element: {defines}")  # Debug print
    if defines is not None:
        # print(f"Defines text: {defines.text}")  # Debug print
        try:
            global_flags['defines'] = [f'-D{d.strip()}' for d in defines.text.split(',')]
        except AttributeError:
            global_flags['defines'] = []
    else:
        print("No defines found in target")

    # 提取包含路径（Include Paths）
    includes = target.find('TargetOption/TargetArmAds/Cads/VariousControls/IncludePath')
    # print(f"Includes element: {includes}")  # Debug print
    if includes is not None and includes.text:
        # print(f"Includes text: {includes.text}")  # Debug print
        paths = includes.text.split(';')
        global_flags['includes'] = [f'-I{p.strip()}' for p in paths if p.strip()]
    else:
        print("No include paths found in target")
        global_flags['includes'] = []

    # 提取其他编译选项（如优化级别、调试信息等）
    misc = target.find('TargetOption/TargetArmAds/Cads/VariousControls/MiscControls')
    # print(f"Misc element: {misc}")  # Debug print
    if misc is not None and misc.text:
        # print(f"Misc text: {misc.text}")  # Debug print
        global_flags['misc'] = misc.text.split()
    else:
        print("No misc options found in target")
        global_flags['misc'] = []

    uc99 = target.find('TargetOption/TargetArmAds/Cads/uC99')
    if uc99 is not None and uc99.text == '1':
        # print("Detected uC99 == 1, adding '-sdt=c99'")
        global_flags['misc'].append('-sdt=c99')

    # 提取文件列表
    files = []
    
    # Debug: Print XML structure up to depth 3
    def print_xml_structure(element, depth=0, max_depth=3):
        if depth > max_depth:
            return
        indent = '  ' * depth
        print(f"{indent}{element.tag}: {element.attrib}")
        for child in element:
            print_xml_structure(child, depth + 1, max_depth)

    # print("\nXML structure (up to depth 3):")
    # print_xml_structure(root)

    # Try multiple possible paths for finding groups
    group_paths = [
        'Groups/Group',
        'Targets/Target/Groups/Group',
        'Project/Targets/Target/Groups/Group',
        'Project/Groups/Group'
    ]

    groups = []
    for path in group_paths:
        found_groups = root.findall(path)
        if found_groups:
            # print(f"\nFound {len(found_groups)} groups using path: {path}")
            groups = found_groups
            break
    else:
        print("\nNo groups found using any of the following paths:")
        for path in group_paths:
            print(f"- {path}")
    
    for group in groups:
        group_name = group.find('GroupName')
        # print(f"\nProcessing group: {group_name.text if group_name is not None else 'Unnamed group'}")
        
        # Debug: Print all files in this group
        group_files = group.findall('Files/File')
        # print(f"Found {len(group_files)} files in group")
        
        for file in group_files:
            file_path_node = file.find('FilePath')
            if file_path_node is None:
                print("Warning: File node has no FilePath element")
                continue
                
            file_path = file_path_node.text
            if not file_path:
                print("Warning: FilePath element has no text content")
                continue
                
            # print(f"Found file path: {file_path}")
            
            if file_path.endswith(('.c', '.cpp', '.s')):
                files.append(file_path)
                # print(f"Added source file: {file_path}")
            else:
                print(f"Skipping non-source file: {file_path}")


    return {
        'global_flags': global_flags,
        'files': files
    }

def generate_compile_commands(uvprojx_path, output_path, objects_dir_name='Objects'):
    """
    生成 compile_commands.json
    :param uvprojx_path: .uvprojx 文件路径
    :param output_path: 输出文件路径
    :param objects_dir_name: 对象目录名称 (default: Objects)
    """
    uvprojx_path = Path(uvprojx_path).resolve()
    project_dir = Path(os.path.dirname(uvprojx_path)).as_posix()

    # 查找 "Objects" 目录并获取 .build_log.htm 文件
    objects_dir = Path(project_dir) / objects_dir_name
    build_log_file = None
    if objects_dir.exists() and objects_dir.is_dir():
        for file in objects_dir.glob('*.build_log.htm'):
            build_log_file = file
            break  # 假设只有一个匹配文件，找到后跳出循环
    else:
        print(f"Warning: 'Objects' directory not found at {objects_dir}. Skipping toolchain path extraction.")

    # 如果找到了 .build_log.htm 文件，解析出 Toolchain Path
    toolchain_path = None
    armcc_include_dir = None
    armcc_path = None
    if build_log_file and build_log_file.exists():
        with open(build_log_file, 'r') as f:
            for line in f:
                if "Toolchain Path:" in line:
                    # 提取 Toolchain Path 后面的路径
                    toolchain_path = line.split("Toolchain Path:")[1].strip()
                    if toolchain_path:  # 确保路径非空
                        armcc_path = Path(toolchain_path) / "armcc.exe"  # 替换 armcc_path
                    break  # 找到路径后退出循环
    else:
        print(f"Warning: No valid .build_log.htm file found in {objects_dir}. Using default or skipping toolchain path.")

    if toolchain_path:
        print(f"Found Toolchain Path: {toolchain_path}")
        armcc_include_dir = Path(toolchain_path).parent / 'include'
    else:
        armcc_include_dir = None
        print("Warning: Toolchain path not found. Skipping include directory setup.")

    # 如果 Include 目录存在，加入到 includes 中
    includes = []
    if not (armcc_include_dir and armcc_include_dir.exists() and armcc_include_dir.is_dir()):
        print(f"Warning: Armcc include directory not found or invalid: {armcc_include_dir}")
    # else:
    #     includes.append(f"-I{armcc_include_dir.as_posix()}")

    data = parse_uvprojx(uvprojx_path)  # 假设这里拿到的数据结构正确

    entries = []
    for file in data['files']:
        # 处理源文件路径，转为 posix 格式（全 /）
        file_path = Path(file).as_posix()

        # 构建编译命令（以 arguments 数组形式）
        args = [
            Path(armcc_path).as_posix() if armcc_path else "armcc",
            '-c',
            file_path,
        ]

        # 处理 defines 和 includes 中可能存在的反斜杠
        defines = [Path(d).as_posix() if d.startswith('-D') else d for d in data['global_flags']['defines']]
        # 这里把新加的 Include 目录加到 includes 中
        includes += [Path(inc).as_posix() if inc.startswith('-I') else inc for inc in data['global_flags']['includes']]
        if armcc_include_dir and armcc_include_dir.exists() and armcc_include_dir.is_dir():
            includes.append(f"-I{armcc_include_dir.as_posix()}")
        # else:
        #     print(f"Warning: Include directory not found or invalid: {armcc_include_dir}")

        misc = data['global_flags']['misc']  # misc 一般是字符串列表，无需处理路径符

        args += defines
        args += includes
        args += misc
        args += ['-o', f'obj/{Path(file).stem}.o']

        entries.append({
            "directory": project_dir,
            "file": f"{project_dir}/{file_path}",
            "arguments": args
        })

    with open(output_path, 'w') as f:
        json.dump(entries, f, indent=2)

if __name__ == '__main__':
    import argparse
    # usage: python uvprojx2compileDatabase.py your_project.uvoptx -o compile_commands.json

    parser = argparse.ArgumentParser(description='Convert Keil uvprojx to compile_commands.json')
    parser.add_argument('uvprojx', help='Path to .uvprojx file')
    # parser.add_argument('--armcc', required=True, help='ARMCC compiler path')
    parser.add_argument('-o', '--output', default='compile_commands.json', help='Output path')
    parser.add_argument('-d', '--objects', default='Objects', help='Objects directory (default: Objects)')
    args = parser.parse_args()

    generate_compile_commands(args.uvprojx, args.output, args.objects)
