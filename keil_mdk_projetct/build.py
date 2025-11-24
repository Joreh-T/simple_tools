# -*- coding: UTF-8 -*-
#============================================================================#
# Author      : Joreh
# Date        : 2023-04-10
# Description : 直接调用Keil编译、下载Keil工程
#============================================================================#

import os
import threading
import sys
import re

runing = True
KEIL_PATH = 'D:\\dev_software\\mdk\\core\\UV4\\UV4.exe'

Message_start = " Start build "
Message_finished = " Build finished "
Message_err = " Error occurred "
Message_open_in_keil = " Open project in keil "


#============================================================================#
# 函数区
#============================================================================#

def print_help():
    """打印脚本使用说明"""
    print(r"""
============================================================
   Keil 命令行编译脚本使用说明
------------------------------------------------------------
用法:
    python keil_build.py [mode] [project_path]

参数说明:
    [mode] 可选值:
        -b              编译工程（Build）
        -r              全量重编译（Rebuild all）
        -f              下载工程到目标板（Flash Download）
        open_project    打开工程（启动 Keil IDE）
        --help          显示本帮助信息

    [project_path]
        工程所在目录（脚本会自动在该目录递归查找 .uvprojx 文件）

示例:
    python keil_build.py -b D:\workspace\MyProject
    python keil_build.py -r D:\workspace\MyProject
    python keil_build.py -f D:\workspace\MyProject
    python keil_build.py open_project D:\workspace\MyProject
------------------------------------------------------------
备注:
    1. KEIL_PATH 需配置为你本地 UV4.exe 的实际路径
    2. 编译日志会保存到当前目录下的 build.log 文件
============================================================
""")
    sys.exit(0)


def get_project(path, suffix):
    """在指定目录下递归查找后缀为 suffix 的文件"""
    all_files = []
    for fpathe, dirs, fs in os.walk(path):
        for f in fs:
            if f.endswith(suffix):
                all_files.append(os.path.join(fpathe, f))
                break
    return all_files


def readfile(logfile):
    """实时读取并解析 Keil 生成的日志文件"""
    global runing
    err = False
    # warn = False
    with open(logfile, 'w') as f:
        pass
    with open(logfile, 'r') as f:
        while runing:
            line = f.readline(1000)
            if line != '':
                line = line.replace('\\', '/')
                if ('error:' in line and '0 errors' not in line) or 'failed' in line or 'Error:' in line:
                    print("\033[31m%s\033[0m" % line)
                    err = True
                elif 'warning' in line or 'Warning:' in line:
                    print("\033[33m%s\033[0m" % line)
                    # warn = True
                elif 'Program Size:' in line:
                    pattern = re.compile(r'(?<=Code=)\d*')
                    code_data = pattern.findall(line)
                    pattern = re.compile(r'(?<=RO-data=)\d*')
                    ro_data = pattern.findall(line)
                    pattern = re.compile(r'(?<=RW-data=)\d*')
                    rw_data = pattern.findall(line)
                    pattern = re.compile(r'(?<=ZI-data=)\d*')
                    zi_data = pattern.findall(line)
                    if all([code_data, ro_data, rw_data, zi_data]):
                        RO_Size = int(code_data[0]) + int(ro_data[0])
                        RW_Size = int(rw_data[0]) + int(zi_data[0])
                        ROM_Size = int(code_data[0]) + int(ro_data[0]) + int(rw_data[0])
                        print(line, end=' ')
                        print("============================================================================\n")
                        print("    Total RO  Size (Code + RO Data)\t\t%7d Byte\t%5.2f KB" % (RO_Size, RO_Size/1024))
                        print("    Total RAM Size (RW Data + ZI Data)\t\t%7d Byte\t%5.2f KB" % (RW_Size, RW_Size/1024))
                        print("    Total ROM Size (Code + RO Data + RW Data)\t%7d Byte\t%5.2f KB" % (ROM_Size, ROM_Size/1024))
                        print("\n============================================================================")
                else:
                    print(line, end=' ')
    if not err:
        print("\b\033[32m%s\033[0m" % Message_finished.center(75, '-'))
    else:
        print("\b\033[31m%s\033[0m" % Message_err.center(75, '-'))


if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] in ('--help', '-h', '/?'):
        print_help()

    open_in_keil = False
    if sys.argv[1] == 'open_project':
        open_in_keil = True
    elif sys.argv[1] == '-f':
        Message_start = " Start Download "
        Message_finished = " Download finished "
        Message_err = " Download failed "

    logfile = ''
    if not open_in_keil:
        modulePath = os.path.abspath(os.curdir)
        logfile = os.path.join(modulePath, 'build.log')

    optional_args = ['-f', '-b', '-r', 'open_project', '--help', '-h', '/?']
    if len(sys.argv) < 2 or sys.argv[1] not in optional_args:
        print("\033[31m错误：缺少工程目录参数！\033[0m\n")
        print_help()

    project_path = ''
    if len(sys.argv) < 3:
        project_path = '.'
    else:
        project_path = sys.argv[2]

    PROJECT_PATH = get_project(path=project_path, suffix=".uvprojx")
    if not PROJECT_PATH:
        print("\033[31m--Error: can't find the project file!!--\n\033[0m")
        sys.exit(1)

    print(f"Found project: {PROJECT_PATH[0]}")
    if not open_in_keil:
        cmd = f'{KEIL_PATH} {sys.argv[1]} "{PROJECT_PATH[0]}" -j0 -o "{logfile}"'
        print(cmd)
        thread = threading.Thread(target=readfile, args=(logfile,))
        thread.start()
        print("\033[32m%s\033[0m" % Message_start.center(75, '-'))
        code = os.system(cmd)
        runing = False
        thread.join()
        sys.exit(code)
    else:
        cmd = f'"{KEIL_PATH}" "{PROJECT_PATH[0]}"'
        print(cmd)
        print("\b\033[32m%s\033[0m" % Message_open_in_keil.center(75, '-'))
        os.popen(cmd)

